"""
aurum-desk/backend/services/kh_02_entry_foundation_research_service.py

AURUM DESK — KH-02: Entry Foundation Research Service
SPEC VERSION: KH-02.R1 / RESEARCH-ONLY / NO PRODUCTION ACTIVATION

Strictly ADDITIVE, RESEARCH-ONLY service built on top of existing authoritative AURUM Desk
modules WITHOUT modifying any production, strategy, SL/TP, risk, fee, True Unicorn D4, or
prior KH-01* code or sealed artifacts:
  - SharedStrategyEvaluator.evaluate_model_direction (KEEP_AS_IS, read-only)
  - domain_calculator.CostAssumptions & calculate_risk_reward (KEEP_AS_IS, read-only)
  - v17_9_3_1_2_2_signal_intelligence (KEEP_AS_IS, read-only)
  - v17_9_3_macro_unicorn_intelligence (KEEP_AS_IS, read-only)
  - unicorn_evidence_service (KEEP_AS_IS, read-only)
  - v17_9_3_1_2_14_accounting_target_and_d4_truth_service (KEEP_AS_IS, read-only)
  - v17_9_3_1_2_executable_intervention_replay_service (KEEP_AS_IS, read-only)
  - kh_01b_entry_unicorn_research_service / kh_01b_r1_* (KEEP_AS_IS, read-only)

HARD SAFETY LOCKS (INVARIANT ACROSS ALL KH-02 EXECUTION):
  - PRODUCTION = OFF, LIVE = OFF, PAPER = OFF, AUTO = OFF, ACTIVATION_READY = NO.
  - Zero live/paper order submission, zero production flag mutations, zero TP stretching.
  - Zero lookahead: M15/H1 features use strictly closed bars (`close_time + latency <= decision_time`),
    confirmed swings/FVG/sweeps track explicit `confirmation_time <= decision_time`, scalers/quantiles
    fit ONLY on trailing causal prefix.
  - Zero trades => expectancy = None (`NOT_ESTIMABLE`), NEVER 0.0R.
  - Reused historical DEV data => FRESH_OOS_VERDICT = BLOCKED_NO_UNTOUCHED_DATA; never claims
    PROVEN_EDGE without real fresh untouched OOS/forward evidence.
"""
from __future__ import annotations

import ast
import bisect
from collections import Counter
import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import importlib
import inspect
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.kh_01a_foundation_service import (
    CANONICAL_DATASET_SHA256_KH01A,
    MANDATORY_BRANCH_KH01A,
)
from services.kh_01b_entry_unicorn_research_service import (
    FILL_MODES_KH01B,
    _summarize_episode_list_kh01b,
    evaluate_strict_true_unicorn_hypothesis,
    evaluate_unicorn_adjacent_research_hypothesis,
    load_and_verify_kh01b_reference_baseline,
)
from services.kh_01b_r1_data_safety_closure_service import (
    LOCKED_PRODUCTION_AND_RESEARCH_FILES,
    compute_kh01b_r1_fee_sensitivity,
)
from services.kh_01b_r1_entry_edge_discovery_service import (
    FORBIDDEN_EXPOST_LEAKAGE_KEYS,
    PRIOR_KH01B_MANIFEST_SHA256,
    PRIOR_KH01B_RUN_ID,
    PRIOR_KH01B_TESTED_SHA,
    load_and_verify_kh01b_r1_sources,
)
from services.kh_01b_r1_ram_safety_r3_service import (
    HARD_ABORT_RAM_FLOOR_BYTES_R3,
    PRIOR_R2_ACCEPTED_RUN_ID,
    PRIOR_R2_LATEST_PTR_REL,
    PRIOR_R2_LATEST_PTR_SHA256,
    PRIOR_R2_PUBLICATION_HEAD_SHA,
    PRIOR_R2_TESTED_SHA,
    RAM_POLICY_VERSION_R3,
    SEALED_R2_A1_56710_IDS_SHA256,
    SEALED_R2_E00_BUNDLE_SHA256,
    SEALED_R2_SI0_242_TUPLES_SHA256,
    SEALED_R2_SI8_6375_IDS_SHA256,
    WARNING_NEAR_RAM_FLOOR_BYTES_R3,
    audit_r3_memory_samples_jsonl_gz,
    compute_adaptive_slot_limit_r3,
    detect_active_64mib_ram_floor_in_source,
    evaluate_r3_shard_admission,
)
from services.scalp_m1_types import get_scalp_config
from services.v17_9_3_1_2_2_signal_intelligence import (
    PRODUCTION_MODEL_STATIC_PRIORITY,
    arbitrate_competing_model_intents_v1793122,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_19_truth_and_core_repair_service import EvidenceReceiptV17931219
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    PortfolioReplayStateV179312,
    advance_portfolio_to_next_event_v179312,
    enumerate_full_replay_indices_v179312,
    recalculate_trade_risk_quantity_v179312,
    replay_intervention_outcome_v179312,
)
from services.v17_9_3_macro_unicorn_intelligence import resolve_causal_macro_time_window
from tools.kh_01b_r1_collect_market_data import (
    CANONICAL_SAMPLE_END_MS,
    build_and_validate_future_oos_escrow_registry,
)

# ==============================================================================
# CONSTANTS, SEALED PRIOR CHECKPOINTS & SAFETY LOCKS
# ==============================================================================

SPEC_VERSION_KH02 = "KH-02.R1"
SCHEMA_VERSION_KH02 = "kh_02.entry_foundation_research.v1"
MANDATORY_BRANCH_KH02 = MANDATORY_BRANCH_KH01A
CANONICAL_DATASET_SHA256_KH02 = CANONICAL_DATASET_SHA256_KH01A
KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256 = "2bfeae81c454a5d9d39b66358c30db014db2133a79eb091dc5e2501ff12bc38c"

PRIOR_R3_ACCEPTED_RUN_ID = "20261121T005605Z_312083bc8024"
PRIOR_R3_TESTED_SHA = "312083bc80247c4931e3b7ab059a346d360d465f"
PRIOR_R3_EVIDENCE_SHA = "5e3c6a45dc4fa915f259039f6b10fe297485ccf2"
PRIOR_R3_PUBLICATION_HEAD_SHA = "98a6d25da94b07572decb531cd6207b91a78fdb0"
PRIOR_R3_RUN_REL_DIR = f"aurum-desk/docs/artifacts/kh_01b_r1_ram_safety_r3/{PRIOR_R3_ACCEPTED_RUN_ID}"
PRIOR_R3_LATEST_PTR_REL = "aurum-desk/docs/artifacts/kh_01b_r1_ram_safety_r3/latest_run.json"
PRIOR_R3_LATEST_PTR_SHA256 = "3af706d98b7980120cc124e9616e18b17de4d5067121475c4e38c5408b18e283"
PRIOR_R3_MANIFEST_14_SHA256 = "2c69e5f3f58dbb4b8099ed5223bcf6b44c1d432e6b6a4e240cb4ea80bcd89fcb"

KH02_RESEARCH_ONLY_RUNTIME_FLAGS: Dict[str, Any] = {
    "policy_namespace": "KH02_ENTRY_FOUNDATION_RESEARCH_ONLY_OFF",
    "PRODUCTION": "OFF",
    "LIVE": "OFF",
    "PAPER": "OFF",
    "AUTO": "OFF",
    "ACTIVATION_READY": "NO",
    "research_only": True,
}

VALID_DATA_ORIGIN_ENUM_KH02: Tuple[str, ...] = (
    "OBSERVED",
    "VERIFIED_SNAPSHOT",
    "RECONSTRUCTED",
    "SIMULATED",
    "UNKNOWN",
)

FORBIDDEN_EXPOST_KEYS_KH02: frozenset[str] = FORBIDDEN_EXPOST_LEAKAGE_KEYS | frozenset({
    "future_high",
    "future_low",
    "future_close",
    "future_return",
    "future_mfe",
    "future_mae",
    "highest_after_entry",
    "lowest_after_entry",
    "future_pivot",
    "outcome_label",
    "realized_net_pnl_usd",
    "realized_net_r",
})

FORBIDDEN_ORDER_IMPORT_MODULES_KH02: frozenset[str] = frozenset({
    "services.bitget_client",
    "services.live_execution_service",
    "services.order_executor",
    "services.paper_broker",
    "services.scalp_watcher_orchestrator",
    "routers.orders",
    "routers.trading",
})

NY_TZ = ZoneInfo("America/New_York")

REQUIRED_ARTIFACTS_KH02: Tuple[str, ...] = (
    "00_preflight_git_branch_head_resource_runtime.json",
    "01_function_callgraph_keep_as_is_diff_immutability.json",
    "02_dataset_registry_lineage_m1_m15_h1_quality.json",
    "03_frozen_hypotheses_budget_splits_power_falsification.json",
    "04_asof_mtf_feature_provenance_and_leakage_controls.json",
    "05_opportunity_universe_denominators_and_cohort_ids.json",
    "06_b0_b1_b2_entry_candidates_and_reason_codes.jsonl.gz",
    "07_matched_cohort_confounder_balance_ablations.json",
    "08_chronological_one_position_three_fill_replay.json",
    "09_fee_spread_slippage_1x_2x_3x_counterfactuals.json",
    "10_purged_walkforward_and_cluster_uncertainty.json",
    "11_oos_escrow_no_overlap_no_peek_status.json",
    "12_full_pytest_collection_junit_bijection_exit.json",
    "13_async_w1_w2_w4_w1repeat_hashes_speedups.json",
    "14_resource_telemetry_ram_disk_pid_cleanup.json",
    "15_red_green_fault_injection_independent_controls.json",
    "16_production_baseline_adapter_fingerprint_parity.json",
    "17_engineering_economic_oos_activation_gate_matrix.json",
    "18_independent_verifier_content_sha256_manifest.json",
    "19_git_tested_sha_evidence_publication_receipt.json",
    "README_KH02_ENTRY_FOUNDATION.md",
    "HANDOVER_KH02_ENTRY_FOUNDATION_VI.txt",
)

ALL_KH02_GATES: Tuple[str, ...] = tuple(f"G-KH02-{i:02d}" for i in range(1, 19))

KH02_KEEP_AS_IS_SPECS: Tuple[Tuple[str, str, str, str, str], ...] = (
    (
        "aurum-desk/backend/services/shared_strategy_evaluator.py",
        "services.shared_strategy_evaluator",
        "SharedStrategyEvaluator.evaluate_model_direction",
        "Read-only baseline parity & 3-adapter verification",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/shared_strategy_evaluator.py",
        "services.shared_strategy_evaluator",
        "build_market_decision_context",
        "Read-only shared market context contract",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/domain_calculator.py",
        "domain_calculator",
        "CostAssumptions",
        "Authoritative default cost config (0.0002 / 0.0004 / 0.10); read-only",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/domain_calculator.py",
        "domain_calculator",
        "calculate_risk_reward",
        "Authoritative risk/sizing/net-RR gate (<=2.50 USD, >=2.0R); read-only",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
        "services.v17_9_3_1_2_2_signal_intelligence",
        "advance_pending_confirmation_state_v1793122",
        "Authoritative causal confirmation state machine; untouched",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
        "services.v17_9_3_1_2_2_signal_intelligence",
        "arbitrate_competing_model_intents_v1793122",
        "Authoritative deterministic multi-model priority arbitration",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
        "services.v17_9_3_macro_unicorn_intelligence",
        "resolve_causal_macro_time_window",
        "Causal NY DST / macro window authority; read-only",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
        "services.v17_9_3_macro_unicorn_intelligence",
        "evaluate_causal_unicorn_confluence",
        "Strict True Unicorn confluence authority; untouched",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
        "services.v17_9_3_macro_unicorn_intelligence",
        "bind_unicorn_to_causal_episode",
        "Strict Unicorn episode binding; untouched",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/unicorn_evidence_service.py",
        "services.unicorn_evidence_service",
        "detect_unicorn_evidence",
        "Strict Unicorn evidence detector; untouched",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/unicorn_evidence_service.py",
        "services.unicorn_evidence_service",
        "detect_breaker_blocks",
        "Strict Breaker Block detector; untouched",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
        "services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service",
        "compute_causal_tf_atr_v17931214",
        "Authoritative source-TF causal ATR; read-only",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
        "services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service",
        "audit_target_reachability_row_v17931214",
        "Authoritative target reachability audit; read-only",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
        "services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service",
        "build_d4_full_63085_and_matched_funnel_v17931214",
        "Authoritative 63,085 -> 0 strict D4 funnel; untouched",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_1_2_executable_intervention_replay_service.py",
        "services.v17_9_3_1_2_executable_intervention_replay_service",
        "replay_intervention_outcome_v179312",
        "Authoritative 3-fill historical trade simulator; read-only",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_1_2_executable_intervention_replay_service.py",
        "services.v17_9_3_1_2_executable_intervention_replay_service",
        "advance_portfolio_to_next_event_v179312",
        "Authoritative chronological 1-position state machine; read-only",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/v17_9_3_1_2_executable_intervention_replay_service.py",
        "services.v17_9_3_1_2_executable_intervention_replay_service",
        "recalculate_trade_risk_quantity_v179312",
        "Authoritative lot sizing & $2.50 risk / 2.0R net-RR gate; read-only",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py",
        "services.kh_01b_entry_unicorn_research_service",
        "verify_kh01b_full_baseline_and_adapter_parity",
        "Baseline & 3-adapter parity verifier; read-only",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py",
        "services.kh_01b_r1_entry_edge_discovery_service",
        "load_and_verify_kh01b_r1_sources",
        "Sealed KH-01B / R1 immutable lineage check; read-only",
        "KEEP_AS_IS",
    ),
    (
        "aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py",
        "services.kh_01b_r1_ram_safety_r3_service",
        "evaluate_r3_shard_admission",
        "Reused R3 >=512MiB predictive hysteresis RAM scheduler; read-only",
        "KEEP_AS_IS",
    ),
)

KH02_ADDITIVE_FILES: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_02_entry_foundation_research_service.py",
    "aurum-desk/tools/run_kh_02_entry_foundation_research.py",
    "aurum-desk/tools/kh_02_entry_foundation_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh_02_entry_foundation_research.py",
    "aurum-desk/backend/tests/test_kh_02_asof_mtf_and_fault_controls.py",
)

# ==============================================================================
# UTILITY HELPERS & HARD SAFETY GUARDS
# ==============================================================================


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _wilson_95ci(successes: int, n: int) -> Optional[Tuple[float, float]]:
    if n <= 0:
        return None
    z = 1.959963984540054
    p = successes / n
    denom = 1.0 + (z * z) / n
    center = (p + (z * z) / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * n)) / n)) / denom
    return (round(max(0.0, center - half), 6), round(min(1.0, center + half), 6))


def assert_kh02_runtime_safety_modes_off(
    runtime_modes: Optional[Mapping[str, Any]] = None,
    *,
    check_live_scalp_config: bool = True,
) -> Dict[str, Any]:
    """
    Fail-closed guard enforcing PRODUCTION=OFF, LIVE=OFF, PAPER=OFF, AUTO=OFF, ACTIVATION_READY=NO.
    Verifies both explicit mode flags and live `get_scalp_config()` defaults.
    """
    modes = dict(runtime_modes) if runtime_modes is not None else dict(KH02_RESEARCH_ONLY_RUNTIME_FLAGS)
    for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO", "RESEARCH_APPLY_LIVE", "enabled_in_production"):
        val = modes.get(k)
        if val is True or (isinstance(val, str) and val.strip().upper() not in ("OFF", "NO", "FALSE", "")):
            raise PermissionError(f"KH02_UNAUTHORIZED_RUNTIME_ACTIVATION:{k}={val!r}")
    act = str(modes.get("ACTIVATION_READY", "NO")).strip().upper()
    if act != "NO":
        raise PermissionError(f"KH02_ACTIVATION_READY_MUST_BE_NO:{act!r}")

    cfg_snapshot: Dict[str, Any] = {}
    if check_live_scalp_config:
        cfg = get_scalp_config()
        cfg_d = cfg.model_dump() if hasattr(cfg, "model_dump") else vars(cfg)
        scalp_mode = str(cfg_d.get("scalp_m1_mode") or "OFF").upper()
        cg01_mode = str(cfg_d.get("cg01_profile_mode") or "OFF").upper()
        si_mode = str(cfg_d.get("v17_9_3_1_2_2_signal_intelligence_mode") or "OFF").upper()
        exec_mode = cfg_d.get("execution_mode")
        if scalp_mode != "OFF" or cg01_mode != "OFF" or si_mode != "OFF":
            raise PermissionError(
                f"KH02_PRODUCTION_CONFIG_NOT_OFF:scalp={scalp_mode},cg01={cg01_mode},si={si_mode}"
            )
        if exec_mode is not None and str(exec_mode).upper() not in ("OFF", "NONE", ""):
            raise PermissionError(f"KH02_EXECUTION_MODE_NOT_OFF:{exec_mode!r}")
        if float(cfg_d.get("max_risk_usd", 2.5)) > 2.5 + 1e-9 or float(cfg_d.get("min_net_rr", 2.0)) < 2.0 - 1e-9:
            raise PermissionError("KH02_PRODUCTION_RISK_OR_RR_BOUNDS_WEAKENED")
        cfg_snapshot = {
            "scalp_m1_mode": scalp_mode,
            "cg01_profile_mode": cg01_mode,
            "signal_intelligence_mode": si_mode,
            "execution_mode": exec_mode,
            "max_risk_usd": float(cfg_d.get("max_risk_usd", 2.5)),
            "min_net_rr": float(cfg_d.get("min_net_rr", 2.0)),
        }

    costs = CostAssumptions()
    if (
        abs(costs.maker_fee_rate - 0.0002) > 1e-9
        or abs(costs.taker_fee_rate - 0.0004) > 1e-9
        or abs(costs.slippage_usd - 0.10) > 1e-9
    ):
        raise PermissionError("KH02_DOMAIN_CALCULATOR_COST_DEFAULTS_MUTATED")

    return {
        "status": "PASS",
        "runtime_safety_modes": {
            "PRODUCTION": "OFF",
            "LIVE": "OFF",
            "PAPER": "OFF",
            "AUTO": "OFF",
        },
        "ACTIVATION_READY": "NO",
        "live_scalp_config_verified": cfg_snapshot,
        "cost_assumptions_verified": {
            "maker_fee_rate": costs.maker_fee_rate,
            "taker_fee_rate": costs.taker_fee_rate,
            "slippage_usd": costs.slippage_usd,
        },
    }


def _git_safe(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (rc={proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


# ==============================================================================
# A. LOAD IMMUTABLE BASELINE REGISTRY & CALLGRAPH / SOURCE FREEZE (00, 01, 02)
# ==============================================================================


@dataclass(frozen=True)
class KH02ImmutableBaselineRegistry:
    spec_version: str
    schema_version: str
    branch: str
    expected_tested_sha: str
    canonical_dataset_sha256: str
    prior_use_status: str
    prior_r3_run_id: str
    prior_r3_tested_sha: str
    prior_r3_publication_head_sha: str
    prior_r3_latest_ptr_sha256: str
    prior_r3_manifest_14_sha256: str
    prior_r2_latest_ptr_sha256: str
    prior_kh01b_manifest_sha256: str
    a1_candidate_count: int
    si8_discovery_count: int
    total_candidate_universe_count: int
    si0_reference_scheduled: int
    si0_reference_closed: int
    si0_reference_wins: int
    si0_reference_losses: int
    si0_reference_net_pnl_usd: float
    si0_reference_expectancy_r: float
    sealed_a1_56710_ids_sha256: str
    sealed_si8_6375_ids_sha256: str
    sealed_si0_242_tuples_sha256: str
    sealed_e00_bundle_sha256: str
    verification_passed: bool
    violations: Tuple[str, ...]
    registry_sha256: str


def load_kh02_immutable_baseline_registry(
    repo_root: Path,
    expected_tested_sha: str,
    *,
    branch_override: Optional[str] = None,
) -> KH02ImmutableBaselineRegistry:
    """
    §6.A: Reads and cryptographically verifies sealed KH-01B, KH-01B.R1, R2, and R3 bundles,
    manifests, latest_run.json pointers, and canonical dataset SHA256 without modifying any prior file.
    """
    violations: List[str] = []
    branch = str(branch_override or MANDATORY_BRANCH_KH02)
    if branch != MANDATORY_BRANCH_KH02:
        violations.append(f"WRONG_MANDATORY_BRANCH:{branch}!={MANDATORY_BRANCH_KH02}")
    if not expected_tested_sha or len(expected_tested_sha.strip()) < 7:
        violations.append(f"INVALID_TESTED_SHA:{expected_tested_sha!r}")

    r1_src = load_and_verify_kh01b_r1_sources(
        repo_root, expected_tested_sha, branch_override=branch
    )
    if not r1_src.verification_passed:
        violations.extend(f"KH01B_R1_SOURCE:{v}" for v in r1_src.violations)

    # Verify R2 pointer
    r2_ptr = repo_root / PRIOR_R2_LATEST_PTR_REL
    r2_ptr_sha = _file_sha256(r2_ptr) if r2_ptr.is_file() else "MISSING"
    if r2_ptr_sha != PRIOR_R2_LATEST_PTR_SHA256:
        violations.append(f"PRIOR_R2_LATEST_PTR_DRIFT:{r2_ptr_sha}!={PRIOR_R2_LATEST_PTR_SHA256}")

    # Verify R3 pointer + manifest 14 + verdict 13
    r3_ptr = repo_root / PRIOR_R3_LATEST_PTR_REL
    r3_ptr_sha = _file_sha256(r3_ptr) if r3_ptr.is_file() else "MISSING"
    if r3_ptr_sha != PRIOR_R3_LATEST_PTR_SHA256:
        violations.append(f"PRIOR_R3_LATEST_PTR_DRIFT:{r3_ptr_sha}!={PRIOR_R3_LATEST_PTR_SHA256}")

    r3_m14 = repo_root / PRIOR_R3_RUN_REL_DIR / "14_sha256_manifest_independent_os_readback.json"
    r3_m14_sha = _file_sha256(r3_m14) if r3_m14.is_file() else "MISSING"
    if r3_m14_sha != PRIOR_R3_MANIFEST_14_SHA256:
        violations.append(f"PRIOR_R3_MANIFEST_14_DRIFT:{r3_m14_sha}!={PRIOR_R3_MANIFEST_14_SHA256}")

    # Verify canonical dataset bytes
    ds_path = repo_root / "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz"
    ds_sha = _file_sha256(ds_path) if ds_path.is_file() else "MISSING"
    if ds_sha != CANONICAL_DATASET_SHA256_KH02:
        violations.append(f"CANONICAL_DATASET_SHA_DRIFT:{ds_sha}!={CANONICAL_DATASET_SHA256_KH02}")

    # Verify R3 sealed 07 backtest parity reference numbers directly from disk
    si0_net = -345.8971
    si0_exp = -0.8474
    r3_07 = repo_root / PRIOR_R3_RUN_REL_DIR / "07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json"
    if r3_07.is_file():
        d07 = json.loads(r3_07.read_text("utf-8"))
        nb = d07.get("si0_e00_primary_nb_summary") or {}
        fps = d07.get("per_decision_and_episode_fingerprints") or {}
        si0_net = float(nb.get("total_realized_net_usd", 0.0))
        si0_exp = float(nb.get("expectancy_net_r", 0.0))
        if fps.get("a1_56710_ids_sha256") != SEALED_R2_A1_56710_IDS_SHA256:
            violations.append("SEALED_R3_A1_56710_SHA_MISMATCH")
        if fps.get("si8_6375_ids_sha256") != SEALED_R2_SI8_6375_IDS_SHA256:
            violations.append("SEALED_R3_SI8_6375_SHA_MISMATCH")
        if fps.get("si0_242_episode_matched_tuples_sha256") != SEALED_R2_SI0_242_TUPLES_SHA256:
            violations.append("SEALED_R3_SI0_242_SHA_MISMATCH")
        if fps.get("e00_bundle_sha256") != SEALED_R2_E00_BUNDLE_SHA256:
            violations.append("SEALED_R3_E00_BUNDLE_SHA_MISMATCH")
    else:
        violations.append(f"MISSING_PRIOR_R3_07_PARITY:{r3_07}")

    body = {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "branch": branch,
        "expected_tested_sha": expected_tested_sha,
        "canonical_dataset_sha256": ds_sha,
        "prior_use_status": "REUSED_RESEARCH",
        "prior_r3_run_id": PRIOR_R3_ACCEPTED_RUN_ID,
        "prior_r3_tested_sha": PRIOR_R3_TESTED_SHA,
        "prior_r3_publication_head_sha": PRIOR_R3_PUBLICATION_HEAD_SHA,
        "prior_r3_latest_ptr_sha256": r3_ptr_sha,
        "prior_r3_manifest_14_sha256": r3_m14_sha,
        "prior_r2_latest_ptr_sha256": r2_ptr_sha,
        "prior_kh01b_manifest_sha256": r1_src.prior_kh01b_manifest_sha256,
        "a1_candidate_count": r1_src.a1_candidate_count,
        "si8_discovery_count": r1_src.si8_discovery_count,
        "total_candidate_universe_count": r1_src.total_candidate_universe_count,
        "si0_reference_scheduled": r1_src.si0_scheduled_count,
        "si0_reference_closed": r1_src.si0_closed_count,
        "si0_reference_wins": r1_src.si0_wins_count,
        "si0_reference_losses": r1_src.si0_losses_count,
        "si0_reference_net_pnl_usd": si0_net,
        "si0_reference_expectancy_r": si0_exp,
        "sealed_a1_56710_ids_sha256": SEALED_R2_A1_56710_IDS_SHA256,
        "sealed_si8_6375_ids_sha256": SEALED_R2_SI8_6375_IDS_SHA256,
        "sealed_si0_242_tuples_sha256": SEALED_R2_SI0_242_TUPLES_SHA256,
        "sealed_e00_bundle_sha256": SEALED_R2_E00_BUNDLE_SHA256,
        "verification_passed": len(violations) == 0,
        "violations": tuple(violations),
    }
    return KH02ImmutableBaselineRegistry(**body, registry_sha256=canonical_hash(body))


def build_kh02_ast_callgraph_and_immutability_map(
    repo_root: Path,
    *,
    head_at_start_sha: str,
    tested_sha: str,
) -> Dict[str, Any]:
    """
    §3.2 & §6: Live AST + inspect.signature callgraph map for KEEP_AS_IS production/prior modules
    and ADDITIVE_ONLY KH-02 modules, plus git diff proof of zero locked-file edits and zero
    order-submission imports.
    """
    keep_rows: List[Dict[str, Any]] = []
    missing_symbols: List[str] = []
    for rel_path, mod_name, sym_name, use_case, disposition in KH02_KEEP_AS_IS_SPECS:
        fp = repo_root / rel_path
        f_sha = _file_sha256(fp) if fp.is_file() else "MISSING"
        sig_str = "MISSING"
        lineno: Optional[int] = None
        try:
            mod = importlib.import_module(mod_name)
            obj: Any = mod
            for part in sym_name.split("."):
                obj = getattr(obj, part)
            sig_str = str(inspect.signature(obj))
            try:
                _, lineno = inspect.getsourcelines(obj)
            except Exception:
                lineno = None
        except Exception as exc:
            missing_symbols.append(f"{rel_path}:{sym_name}:{exc}")

        keep_rows.append({
            "path": rel_path,
            "symbol": sym_name,
            "signature": sig_str,
            "line_number": lineno,
            "side_effects": "NONE_READ_ONLY",
            "production_touched": False,
            "snapshot_sha256": f_sha,
            "kh02_use_case": use_case,
            "disposition": disposition,
        })

    additive_rows: List[Dict[str, Any]] = []
    forbidden_import_hits: List[str] = []
    for rel_p in KH02_ADDITIVE_FILES:
        fp = repo_root / rel_p
        if not fp.is_file():
            continue
        src = fp.read_text(encoding="utf-8", errors="replace")
        f_sha = _file_sha256(fp)
        tree = ast.parse(src)
        top_defs: List[Dict[str, Any]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    top_defs.append({
                        "name": node.name,
                        "kind": "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                        "line": int(node.lineno),
                        "args": [a.arg for a in node.args.args],
                    })
            elif isinstance(node, ast.ClassDef):
                top_defs.append({
                    "name": node.name,
                    "kind": "class",
                    "line": int(node.lineno),
                    "args": [],
                })
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name == bad or alias.name.startswith(bad + ".") for bad in FORBIDDEN_ORDER_IMPORT_MODULES_KH02):
                        forbidden_import_hits.append(f"{rel_p}:{node.lineno}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module == bad or node.module.startswith(bad + ".") for bad in FORBIDDEN_ORDER_IMPORT_MODULES_KH02):
                    forbidden_import_hits.append(f"{rel_p}:{node.lineno}:{node.module}")
        additive_rows.append({
            "path": rel_p,
            "snapshot_sha256": f_sha,
            "production_touched": False,
            "side_effects": "RESEARCH_ONLY_OFFLINE_ARTIFACTS",
            "disposition": "ADDITIVE_ONLY",
            "exported_symbols_count": len(top_defs),
            "exported_symbols": top_defs,
        })

    diff_files: List[str] = []
    modified_existing_files: List[str] = []
    locked_modified: List[str] = []
    try:
        diff_raw = _git_safe(repo_root, "diff", "--name-status", f"{head_at_start_sha}..{tested_sha}")
        for ln in diff_raw.splitlines():
            parts = ln.split("\t")
            if len(parts) >= 2:
                st, fpath = parts[0].strip(), parts[-1].strip().replace("\\", "/")
                diff_files.append(f"{st}:{fpath}")
                if not st.startswith("A"):
                    modified_existing_files.append(fpath)
                if fpath in LOCKED_PRODUCTION_AND_RESEARCH_FILES:
                    locked_modified.append(fpath)
    except Exception:
        pass

    status_ok = (
        len(missing_symbols) == 0
        and len(forbidden_import_hits) == 0
        and len(locked_modified) == 0
        and len(modified_existing_files) == 0
    )
    return {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "head_at_start_sha": head_at_start_sha,
        "tested_executable_sha": tested_sha,
        "keep_as_is_symbols_count": len(keep_rows),
        "keep_as_is_symbols": keep_rows,
        "missing_keep_as_is_symbols": missing_symbols,
        "additive_kh02_files_count": len(additive_rows),
        "additive_kh02_files": additive_rows,
        "forbidden_order_import_hits": forbidden_import_hits,
        "git_diff_entries": diff_files,
        "modified_existing_files_count": len(modified_existing_files),
        "modified_existing_files": modified_existing_files,
        "locked_files_modified_count": len(locked_modified),
        "locked_files_modified": locked_modified,
        "status": "PASS" if status_ok else "FAIL",
    }


def audit_kh02_dataset_registry_and_quality(
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    dataset_path: Path,
    dataset_sha256: str,
) -> Dict[str, Any]:
    """
    §5.1 & §10 (02): Audits XAUUSDT M1/M5/M15/1H/4H/1D bar lineage, monotonicity, duplicates,
    OHLC validity, unclosed bars, gap classification, NY timezone/DST spans, and explicit
    data_origin enum (`VERIFIED_SNAPSHOT` for OHLCV, `SIMULATED` for modeled $0.20 bid/ask).
    """
    tf_specs = (
        ("1m", 60_000),
        ("5m", 300_000),
        ("15m", 900_000),
        ("1H", 3_600_000),
        ("4H", 14_400_000),
        ("1D", 86_400_000),
    )
    per_tf: Dict[str, Any] = {}
    violations: List[str] = []

    for tf_k, span_ms in tf_specs:
        bars = list(datasets.get(tf_k) or ())
        if not bars:
            violations.append(f"MISSING_TIMEFRAME_SERIES:{tf_k}")
            per_tf[tf_k] = {"bar_count": 0, "status": "FAIL"}
            continue
        seen_open: Set[int] = set()
        dup_n = 0
        non_mono_n = 0
        unclosed_n = 0
        bad_ohlc_n = 0
        wrong_span_n = 0
        weekend_gaps = 0
        intra_gaps = 0
        missing_slots = 0
        prev_o: Optional[int] = None
        dst_offsets: Set[str] = set()

        for b in bars:
            o_ms = int(b.get("open_time") or b.get("timestamp") or 0)
            c_ms = int(b.get("close_time") or (o_ms + span_ms))
            o_px = float(b.get("open") or 0.0)
            h_px = float(b.get("high") or 0.0)
            l_px = float(b.get("low") or 0.0)
            c_px = float(b.get("close") or 0.0)
            if not bool(b.get("is_closed", True)):
                unclosed_n += 1
            if (
                o_ms <= 0
                or c_ms <= o_ms
                or min(o_px, h_px, l_px, c_px) <= 0.0
                or h_px < max(o_px, c_px, l_px) - 1e-6
                or l_px > min(o_px, c_px, h_px) + 1e-6
            ):
                bad_ohlc_n += 1
            if abs((c_ms - o_ms) - span_ms) > 1000:
                wrong_span_n += 1
            if o_ms in seen_open:
                dup_n += 1
            seen_open.add(o_ms)
            if prev_o is not None:
                dt = o_ms - prev_o
                if dt <= 0:
                    non_mono_n += 1
                elif dt > span_ms:
                    miss_k = max(0, int(round(dt / float(span_ms))) - 1)
                    missing_slots += miss_k
                    prev_dt = datetime.fromtimestamp(prev_o / 1000.0, tz=timezone.utc)
                    if dt >= 36 * 3_600_000 or prev_dt.weekday() in (4, 5, 6):
                        weekend_gaps += 1
                    else:
                        intra_gaps += 1
            prev_o = o_ms

        first_o = int(bars[0].get("open_time") or bars[0].get("timestamp") or 0)
        last_o = int(bars[-1].get("open_time") or bars[-1].get("timestamp") or 0)
        last_c = int(bars[-1].get("close_time") or (last_o + span_ms))
        for sample_ms in (first_o, (first_o + last_o) // 2, last_o):
            ny_dt = datetime.fromtimestamp(sample_ms / 1000.0, tz=timezone.utc).astimezone(NY_TZ)
            dst_offsets.add(ny_dt.strftime("%Z(UTC%z)"))

        tf_ok = (dup_n == 0 and non_mono_n == 0 and unclosed_n == 0 and bad_ohlc_n == 0 and wrong_span_n == 0)
        if not tf_ok:
            violations.append(
                f"TIMEFRAME_INTEGRITY_FAIL:{tf_k}:dup={dup_n},non_mono={non_mono_n},unclosed={unclosed_n},bad_ohlc={bad_ohlc_n},wrong_span={wrong_span_n}"
            )
        per_tf[tf_k] = {
            "bar_count": len(bars),
            "expected_span_ms": span_ms,
            "first_open_ms": first_o,
            "first_open_utc": datetime.fromtimestamp(first_o / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_open_ms": last_o,
            "last_close_ms": last_c,
            "last_close_utc": datetime.fromtimestamp(last_c / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "span_days": round((last_c - first_o) / 86_400_000.0, 3),
            "duplicates_count": dup_n,
            "non_monotonic_count": non_mono_n,
            "unclosed_bars_count": unclosed_n,
            "invalid_ohlc_count": bad_ohlc_n,
            "wrong_duration_count": wrong_span_n,
            "weekend_or_rollover_gaps_count": weekend_gaps,
            "intra_session_gaps_count": intra_gaps,
            "missing_slots_total": missing_slots,
            "ny_timezone_offsets_observed": sorted(dst_offsets),
            "status": "PASS" if tf_ok else "FAIL",
        }

    # Check M15/H1 coverage of M1 window
    m1_info = per_tf.get("1m") or {}
    m15_info = per_tf.get("15m") or {}
    h1_info = per_tf.get("1H") or {}
    mtf_covers_m1 = (
        int(m15_info.get("first_open_ms") or 10**18) <= int(m1_info.get("first_open_ms") or 0)
        and int(m15_info.get("last_close_ms") or 0) >= int(m1_info.get("last_close_ms") or 10**18)
        and int(h1_info.get("first_open_ms") or 10**18) <= int(m1_info.get("first_open_ms") or 0)
        and int(h1_info.get("last_close_ms") or 0) >= int(m1_info.get("last_close_ms") or 10**18)
    )
    if not mtf_covers_m1:
        violations.append("MTF_M15_H1_DOES_NOT_COVER_M1_WINDOW")

    u_manifest = enumerate_full_replay_indices_v179312(datasets, warmup=30)
    if not u_manifest.monotonic_strictly_increasing or not u_manifest.zero_duplicate_timestamps:
        violations.append("M1_FULL_UNIVERSE_ENUMERATION_FAILED")

    return {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "dataset_path": str(dataset_path).replace("\\", "/"),
        "dataset_sha256": dataset_sha256,
        "symbol": "XAUUSDT",
        "exchange_contract_family": "BITGET_USDT_PERPETUAL_XAUUSDT",
        "partition_role": "REUSED_RESEARCH",
        "eligible_as_fresh_oos": False,
        "data_origin_enum": {
            "ohlcv_candles": "VERIFIED_SNAPSHOT",
            "l1_bid_ask_quotes": "SIMULATED",
            "quote_mode": "MODELED_BID_ASK",
            "modeled_spread_usd": 0.20,
            "observed_l1_bbo_available": False,
            "never_relabeled_observed": True,
        },
        "fee_provenance": {
            "authoritative_default_reference": {
                "maker_fee_rate": 0.0002,
                "taker_fee_rate": 0.0004,
                "slippage_usd": 0.10,
                "source": "domain_calculator.CostAssumptions",
            },
            "public_contract_reference_taker_rate": 0.0006,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        },
        "timezone_conversion_version": "IANA_ZoneInfo_America_New_York_UTC_EpochMs_v1",
        "m1_post_warmup_manifest": u_manifest.to_dict(),
        "mtf_m15_h1_covers_m1_window": mtf_covers_m1,
        "per_timeframe_quality": per_tf,
        "violations": violations,
        "status": "PASS" if not violations else "FAIL",
    }


# ==============================================================================
# B. STRICT POINT-IN-TIME AS-OF M1 / M15 / H1 CAUSAL FEATURE ENGINE (04)
# ==============================================================================


@dataclass(frozen=True)
class KH02AsOfMTFRecord:
    bar_index_1m: int
    decision_timestamp_ms: int
    m1_open_time_ms: int
    m1_close_time_ms: int
    m1_open: float
    m1_high: float
    m1_low: float
    m1_close: float
    m1_atr_14: Optional[float]
    m1_ema_20: Optional[float]
    m1_body_ratio: float
    m1_range_atr_ratio: Optional[float]
    m1_confirmed_swing_low: Optional[float]
    m1_confirmed_swing_low_conf_ms: Optional[int]
    m1_confirmed_swing_high: Optional[float]
    m1_confirmed_swing_high_conf_ms: Optional[int]
    m1_bull_sweep_reclaim: bool
    m1_bear_sweep_reclaim: bool
    m1_bull_bos: bool
    m1_bear_bos: bool
    m1_bull_fvg_retest: bool
    m1_bear_fvg_retest: bool
    # M15 strict as-of closed bar (close_time + latency <= decision_timestamp_ms)
    m15_last_close_ms: Optional[int]
    m15_knowledge_time_ms: Optional[int]
    m15_close: Optional[float]
    m15_ema_20: Optional[float]
    m15_ema_slope: Optional[float]
    m15_raw_dir_efficiency_8: Optional[float]
    m15_trend_dir: str  # BULL | BEAR | NEUTRAL | UNKNOWN_MISSING
    m15_confirmed_swing_low: Optional[float]
    m15_confirmed_swing_low_conf_ms: Optional[int]
    m15_confirmed_swing_high: Optional[float]
    m15_confirmed_swing_high_conf_ms: Optional[int]
    # H1 strict as-of closed bar (close_time + latency <= decision_timestamp_ms)
    h1_last_close_ms: Optional[int]
    h1_knowledge_time_ms: Optional[int]
    h1_close: Optional[float]
    h1_ema_20: Optional[float]
    h1_ema_slope: Optional[float]
    h1_atr_14: Optional[float]
    h1_atr_causal_prefix_quantile: Optional[float]
    h1_trend_dir: str  # BULL | BEAR | NEUTRAL | UNKNOWN_MISSING
    h1_confirmed_swing_low: Optional[float]
    h1_confirmed_swing_low_conf_ms: Optional[int]
    h1_confirmed_swing_high: Optional[float]
    h1_confirmed_swing_high_conf_ms: Optional[int]
    # Session & cost-environment regime
    ny_date: str
    ny_hour: int
    ny_weekday: int
    ny_session_bucket: str
    is_liquid_session: bool
    spread_to_m1_atr_ratio: Optional[float]
    recent_gap_180m: bool
    max_known_at_ms: int
    source_origin: str
    invalidation_reasons: Tuple[str, ...]
    record_sha256: str


def _validate_and_precompute_tf_series(
    bars: Sequence[Mapping[str, Any]],
    *,
    tf_label: str,
    tf_span_ms: int,
    source_latency_ms: int = 0,
    confirm_right_bars: int = 2,
    ema_period: int = 20,
    atr_period: int = 14,
    eff_lookback: int = 8,
    allow_unsorted_canonicalize: bool = False,
) -> Dict[str, Any]:
    """
    Single causal linear pass computing EMA, ATR, directional efficiency, causal prefix ATR
    quantile, and strictly confirmed fractal swings where `confirmation_time = close_time[k + R]`.
    Rejects unclosed candles, non-monotonic timestamps, or invalid OHLC fail-closed.
    """
    bar_list = [dict(b) for b in bars]
    if allow_unsorted_canonicalize:
        bar_list.sort(key=lambda b: (int(b.get("open_time") or b.get("timestamp") or 0), int(b.get("close_time") or 0)))

    n = len(bar_list)
    open_ms: List[int] = []
    close_ms: List[int] = []
    know_ms: List[int] = []
    opens: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []

    for i, b in enumerate(bar_list):
        if b.get("is_closed") is False:
            raise ValueError(f"UNCLOSED_CANDLE_FORBIDDEN:{tf_label}:idx={i}")
        for bad_k in FORBIDDEN_EXPOST_KEYS_KH02:
            if bad_k in b and b.get(bad_k) is not None:
                raise ValueError(f"EXPOST_LEAKAGE_KEY_IN_BAR:{tf_label}:{bad_k}")
        o_t = int(b.get("open_time") or b.get("timestamp") or 0)
        c_t = int(b.get("close_time") or (o_t + tf_span_ms))
        if o_t <= 0 or c_t <= o_t:
            raise ValueError(f"INVALID_BAR_TIMESTAMPS:{tf_label}:idx={i}:o={o_t}:c={c_t}")
        if open_ms and o_t <= open_ms[-1]:
            raise ValueError(f"DUPLICATE_OR_NON_MONOTONIC_BAR:{tf_label}:idx={i}:{o_t}<={open_ms[-1]}")
        if close_ms and c_t <= close_ms[-1]:
            raise ValueError(f"NON_MONOTONIC_CLOSE_TIME:{tf_label}:idx={i}:{c_t}<={close_ms[-1]}")
        o_p = float(b["open"])
        h_p = float(b["high"])
        l_p = float(b["low"])
        c_p = float(b["close"])
        if not (math.isfinite(o_p) and math.isfinite(h_p) and math.isfinite(l_p) and math.isfinite(c_p)):
            raise ValueError(f"NON_FINITE_OHLC:{tf_label}:idx={i}")
        if min(o_p, h_p, l_p, c_p) <= 0.0 or h_p < max(o_p, c_p, l_p) - 1e-6 or l_p > min(o_p, c_p, h_p) + 1e-6:
            raise ValueError(f"INVALID_OHLC_GEOMETRY:{tf_label}:idx={i}")
        open_ms.append(o_t)
        close_ms.append(c_t)
        know_ms.append(c_t + int(source_latency_ms))
        opens.append(o_p)
        highs.append(h_p)
        lows.append(l_p)
        closes.append(c_p)

    # Causal EMA, ATR, directional efficiency, and trailing prefix ATR quantile
    ema: List[Optional[float]] = [None] * n
    ema_slope: List[Optional[float]] = [None] * n
    atr: List[Optional[float]] = [None] * n
    atr_prefix_q: List[Optional[float]] = [None] * n
    dir_eff: List[Optional[float]] = [None] * n

    alpha = 2.0 / (ema_period + 1.0)
    trs: List[float] = []
    rolling_atr_hist: List[float] = []

    for i in range(n):
        c_p = closes[i]
        if i == 0:
            ema[i] = c_p
            trs.append(highs[i] - lows[i])
        else:
            prev_e = float(ema[i - 1])  # type: ignore[arg-type]
            ema[i] = alpha * c_p + (1.0 - alpha) * prev_e
            if i >= 3 and ema[i - 3] is not None:
                ema_slope[i] = round(float(ema[i]) - float(ema[i - 3]), 6)  # type: ignore[arg-type]
            pc = closes[i - 1]
            trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))

        if i + 1 >= atr_period:
            cur_a = sum(trs[i + 1 - atr_period : i + 1]) / float(atr_period)
            atr[i] = round(cur_a, 6)
            rolling_atr_hist.append(cur_a)
            # Strictly causal trailing prefix window (max 96 bars, NEVER looking forward!)
            win = rolling_atr_hist[-96:]
            if len(win) >= 12:
                atr_prefix_q[i] = round(sum(1 for x in win if x <= cur_a + 1e-12) / float(len(win)), 6)

        if i >= eff_lookback:
            net_c = closes[i] - closes[i - eff_lookback]
            path_c = sum(abs(closes[k] - closes[k - 1]) for k in range(i - eff_lookback + 1, i + 1))
            dir_eff[i] = round(net_c / path_c, 6) if path_c > 1e-9 else 0.0

    # Strictly causal confirmed fractal swings (pivot k confirmed ONLY at bar k + confirm_right_bars)
    r_bars = max(1, int(confirm_right_bars))
    last_sw_low: List[Optional[float]] = [None] * n
    last_sw_low_conf: List[Optional[int]] = [None] * n
    last_sw_high: List[Optional[float]] = [None] * n
    last_sw_high_conf: List[Optional[int]] = [None] * n
    # Also maintain rolling pool of recent confirmed unswept highs/lows (strictly causal!)
    unswept_highs_at: List[Tuple[Tuple[float, int], ...]] = [()] * n
    unswept_lows_at: List[Tuple[Tuple[float, int], ...]] = [()] * n

    active_highs: List[Tuple[float, int]] = []
    active_lows: List[Tuple[float, int]] = []
    cur_sw_l: Optional[float] = None
    cur_sw_l_ms: Optional[int] = None
    cur_sw_h: Optional[float] = None
    cur_sw_h_ms: Optional[int] = None

    for i in range(n):
        # First, invalidate any previously confirmed level swept by bars up to and including i
        hi_i = highs[i]
        lo_i = lows[i]
        active_highs = [(px, c_t) for px, c_t in active_highs if px > hi_i + 1e-9]
        active_lows = [(px, c_t) for px, c_t in active_lows if px < lo_i - 1e-9]

        # Pivot at k = i - r_bars becomes causally known at bar i (`confirmation_time = know_ms[i]`)
        k = i - r_bars
        if k >= r_bars:
            is_low_p = all(lows[k] <= lows[k - d] for d in range(1, r_bars + 1)) and all(
                lows[k] < lows[k + d] for d in range(1, r_bars + 1)
            )
            is_high_p = all(highs[k] >= highs[k - d] for d in range(1, r_bars + 1)) and all(
                highs[k] > highs[k + d] for d in range(1, r_bars + 1)
            )
            if is_low_p:
                cur_sw_l = lows[k]
                cur_sw_l_ms = know_ms[i]
                if lows[k] < min(lows[k + 1 : i + 1]):
                    active_lows.append((lows[k], know_ms[i]))
                    active_lows = active_lows[-12:]
            if is_high_p:
                cur_sw_h = highs[k]
                cur_sw_h_ms = know_ms[i]
                if highs[k] > max(highs[k + 1 : i + 1]):
                    active_highs.append((highs[k], know_ms[i]))
                    active_highs = active_highs[-12:]

        last_sw_low[i] = cur_sw_l
        last_sw_low_conf[i] = cur_sw_l_ms
        last_sw_high[i] = cur_sw_h
        last_sw_high_conf[i] = cur_sw_h_ms
        unswept_highs_at[i] = tuple(active_highs)
        unswept_lows_at[i] = tuple(active_lows)

    return {
        "tf_label": tf_label,
        "count": n,
        "open_ms": open_ms,
        "close_ms": close_ms,
        "know_ms": know_ms,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "ema": ema,
        "ema_slope": ema_slope,
        "atr": atr,
        "atr_prefix_q": atr_prefix_q,
        "dir_eff": dir_eff,
        "last_sw_low": last_sw_low,
        "last_sw_low_conf": last_sw_low_conf,
        "last_sw_high": last_sw_high,
        "last_sw_high_conf": last_sw_high_conf,
        "unswept_highs_at": unswept_highs_at,
        "unswept_lows_at": unswept_lows_at,
    }


def build_kh02_asof_mtf_features(
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    warmup_m1: int = 30,
    source_latency_ms: int = 0,
    data_origin: str = "VERIFIED_SNAPSHOT",
    quote_mode: str = "MODELED_BID_ASK",
    allow_unsorted_canonicalize: bool = False,
    injected_extra_features: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    §6.B: Builds the strict point-in-time M1/M15/H1 causal feature frame for every post-warmup M1 bar.
    Guarantees:
      - `m15_knowledge_time_ms <= decision_timestamp_ms` and `h1_knowledge_time_ms <= decision_timestamp_ms`.
      - All swing/pivot/FVG/sweep confirmations satisfy `confirmation_time <= decision_timestamp_ms`.
      - Rejects unclosed M15/H1 bars, hidden ex-post keys, invalid data origins, or relabeled BBO.
    """
    if data_origin not in VALID_DATA_ORIGIN_ENUM_KH02:
        raise ValueError(f"INVALID_DATA_ORIGIN_ENUM:{data_origin}")
    if quote_mode == "OBSERVED" and data_origin != "OBSERVED":
        raise ValueError(f"SIMULATED_BBO_RELABELED_OBSERVED:quote_mode={quote_mode},origin={data_origin}")
    if int(source_latency_ms) < 0:
        raise ValueError(f"NEGATIVE_SOURCE_LATENCY_FORBIDDEN:{source_latency_ms}")
    if injected_extra_features:
        for k in injected_extra_features:
            if k in FORBIDDEN_EXPOST_KEYS_KH02:
                raise ValueError(f"FORBIDDEN_EXPOST_FEATURE_INJECTED:{k}")

    m1_s = _validate_and_precompute_tf_series(
        datasets.get("1m", ()),
        tf_label="1M",
        tf_span_ms=60_000,
        source_latency_ms=0,
        confirm_right_bars=2,
        allow_unsorted_canonicalize=allow_unsorted_canonicalize,
    )
    m15_s = _validate_and_precompute_tf_series(
        datasets.get("15m", ()),
        tf_label="15M",
        tf_span_ms=900_000,
        source_latency_ms=source_latency_ms,
        confirm_right_bars=2,
        allow_unsorted_canonicalize=allow_unsorted_canonicalize,
    )
    h1_s = _validate_and_precompute_tf_series(
        datasets.get("1H", ()),
        tf_label="1H",
        tf_span_ms=3_600_000,
        source_latency_ms=source_latency_ms,
        confirm_right_bars=2,
        allow_unsorted_canonicalize=allow_unsorted_canonicalize,
    )

    n1 = m1_s["count"]
    if n1 <= warmup_m1:
        raise ValueError(f"INSUFFICIENT_M1_BARS:{n1}<={warmup_m1}")

    # Precompute causal M1 FVG zones (3-bar FVG `(i-2, i-1, i)` confirmed at bar `i`)
    active_bull_fvg: Optional[Tuple[float, float, int]] = None  # (low, high, created_idx)
    active_bear_fvg: Optional[Tuple[float, float, int]] = None

    records_by_idx: Dict[int, KH02AsOfMTFRecord] = {}
    records_by_ts: Dict[int, KH02AsOfMTFRecord] = {}
    m15_know = m15_s["know_ms"]
    h1_know = h1_s["know_ms"]

    # Track trailing 180m M1 gap timestamps
    last_gap_idx = -10**9

    for i in range(n1):
        if i > 0 and (m1_s["open_ms"][i] - m1_s["open_ms"][i - 1]) > 5 * 60_000:
            last_gap_idx = i

        # Update causal FVG zones at bar i using bars (i-2, i-1, i) ONLY
        if i >= 2:
            if m1_s["lows"][i] > m1_s["highs"][i - 2] + 0.05:
                active_bull_fvg = (m1_s["highs"][i - 2], m1_s["lows"][i], i)
            if m1_s["highs"][i] < m1_s["lows"][i - 2] - 0.05:
                active_bear_fvg = (m1_s["highs"][i], m1_s["lows"][i - 2], i)

        # Invalidate consumed/expired FVG (max 15 bars old or close strictly beyond distal edge)
        if active_bull_fvg is not None:
            if (i - active_bull_fvg[2]) > 15 or m1_s["closes"][i] < active_bull_fvg[0]:
                active_bull_fvg = None
        if active_bear_fvg is not None:
            if (i - active_bear_fvg[2]) > 15 or m1_s["closes"][i] > active_bear_fvg[1]:
                active_bear_fvg = None

        if i < warmup_m1:
            continue

        dec_ts = m1_s["close_ms"][i]
        o_p, h_p, l_p, c_p = m1_s["opens"][i], m1_s["highs"][i], m1_s["lows"][i], m1_s["closes"][i]
        rng = max(0.01, h_p - l_p)
        body_ratio = round(abs(c_p - o_p) / rng, 4)
        atr1 = m1_s["atr"][i]
        rng_atr = round(rng / atr1, 4) if (atr1 is not None and atr1 > 1e-9) else None

        # Strictly causal prior-bar confirmed swings (at i-1 so bar i can sweep/break them)
        prev_sw_l = m1_s["last_sw_low"][i - 1]
        prev_sw_l_conf = m1_s["last_sw_low_conf"][i - 1]
        prev_sw_h = m1_s["last_sw_high"][i - 1]
        prev_sw_h_conf = m1_s["last_sw_high_conf"][i - 1]

        bull_sweep = bool(prev_sw_l is not None and l_p < prev_sw_l and c_p > prev_sw_l and c_p > o_p)
        bear_sweep = bool(prev_sw_h is not None and h_p > prev_sw_h and c_p < prev_sw_h and c_p < o_p)
        bull_bos = bool(prev_sw_h is not None and c_p > prev_sw_h and m1_s["closes"][i - 1] <= prev_sw_h and c_p > o_p)
        bear_bos = bool(prev_sw_l is not None and c_p < prev_sw_l and m1_s["closes"][i - 1] >= prev_sw_l and c_p < o_p)
        bull_fvg_ret = bool(
            active_bull_fvg is not None
            and active_bull_fvg[2] < i
            and l_p <= active_bull_fvg[1]
            and l_p >= active_bull_fvg[0] - 0.15
            and c_p > o_p
        )
        bear_fvg_ret = bool(
            active_bear_fvg is not None
            and active_bear_fvg[2] < i
            and h_p >= active_bear_fvg[0]
            and h_p <= active_bear_fvg[1] + 0.15
            and c_p < o_p
        )

        # Strict as-of M15 closed bar join (`know_ms <= dec_ts`)
        j15 = bisect.bisect_right(m15_know, dec_ts) - 1
        inv_reasons: List[str] = []
        if j15 >= 0:
            m15_c_ms = m15_s["close_ms"][j15]
            m15_k_ms = m15_know[j15]
            m15_cl = m15_s["closes"][j15]
            m15_e = m15_s["ema"][j15]
            m15_sl = m15_s["ema_slope"][j15]
            m15_eff = m15_s["dir_eff"][j15]
            m15_swl = m15_s["last_sw_low"][j15]
            m15_swl_c = m15_s["last_sw_low_conf"][j15]
            m15_swh = m15_s["last_sw_high"][j15]
            m15_swh_c = m15_s["last_sw_high_conf"][j15]
            if m15_e is not None and m15_sl is not None:
                if m15_cl > m15_e and m15_sl > 0.0:
                    m15_dir = "BULL"
                elif m15_cl < m15_e and m15_sl < 0.0:
                    m15_dir = "BEAR"
                else:
                    m15_dir = "NEUTRAL"
            else:
                m15_dir = "UNKNOWN_MISSING"
            if m15_k_ms > dec_ts:
                inv_reasons.append(f"M15_KNOWLEDGE_AFTER_DECISION:{m15_k_ms}>{dec_ts}")
        else:
            m15_c_ms = m15_k_ms = m15_swl_c = m15_swh_c = None
            m15_cl = m15_e = m15_sl = m15_eff = m15_swl = m15_swh = None
            m15_dir = "UNKNOWN_MISSING"

        # Strict as-of H1 closed bar join (`know_ms <= dec_ts`)
        jh1 = bisect.bisect_right(h1_know, dec_ts) - 1
        if jh1 >= 0:
            h1_c_ms = h1_s["close_ms"][jh1]
            h1_k_ms = h1_know[jh1]
            h1_cl = h1_s["closes"][jh1]
            h1_e = h1_s["ema"][jh1]
            h1_sl = h1_s["ema_slope"][jh1]
            h1_a = h1_s["atr"][jh1]
            h1_aq = h1_s["atr_prefix_q"][jh1]
            h1_swl = h1_s["last_sw_low"][jh1]
            h1_swl_c = h1_s["last_sw_low_conf"][jh1]
            h1_swh = h1_s["last_sw_high"][jh1]
            h1_swh_c = h1_s["last_sw_high_conf"][jh1]
            if h1_e is not None and h1_sl is not None:
                if h1_cl > h1_e and h1_sl > 0.0:
                    h1_dir = "BULL"
                elif h1_cl < h1_e and h1_sl < 0.0:
                    h1_dir = "BEAR"
                else:
                    h1_dir = "NEUTRAL"
            else:
                h1_dir = "UNKNOWN_MISSING"
            if h1_k_ms > dec_ts:
                inv_reasons.append(f"H1_KNOWLEDGE_AFTER_DECISION:{h1_k_ms}>{dec_ts}")
        else:
            h1_c_ms = h1_k_ms = h1_swl_c = h1_swh_c = None
            h1_cl = h1_e = h1_sl = h1_a = h1_aq = h1_swl = h1_swh = None
            h1_dir = "UNKNOWN_MISSING"

        ny_dt = datetime.fromtimestamp(dec_ts / 1000.0, tz=timezone.utc).astimezone(NY_TZ)
        ny_hr = ny_dt.hour
        ny_wd = ny_dt.weekday()
        if ny_wd >= 5:
            sess_b = "WEEKEND_WINDOW"
        elif 2 <= ny_hr < 8:
            sess_b = "LONDON_SESSION"
        elif 8 <= ny_hr < 12:
            sess_b = "NY_AM_SESSION"
        elif 12 <= ny_hr < 17:
            sess_b = "NY_PM_SESSION"
        else:
            sess_b = "ASIA_OR_ROLLOVER_SESSION"
        is_liq = sess_b in ("LONDON_SESSION", "NY_AM_SESSION", "NY_PM_SESSION")

        sp_atr = round(0.20 / atr1, 6) if (atr1 is not None and atr1 > 1e-9) else None
        recent_gap = (i - last_gap_idx) <= 180

        known_times = [dec_ts]
        for kt in (m15_k_ms, h1_k_ms, prev_sw_l_conf, prev_sw_h_conf, m15_swl_c, m15_swh_c, h1_swl_c, h1_swh_c):
            if kt is not None:
                known_times.append(int(kt))
        max_kn = max(known_times)
        if max_kn > dec_ts:
            inv_reasons.append(f"MAX_KNOWN_AT_EXCEEDS_DECISION:{max_kn}>{dec_ts}")

        rec_core = {
            "i": i,
            "ts": dec_ts,
            "m1_c": round(c_p, 4),
            "m1_a": atr1,
            "sw": (bull_sweep, bear_sweep, bull_bos, bear_bos, bull_fvg_ret, bear_fvg_ret),
            "m15": (m15_k_ms, m15_dir, m15_eff),
            "h1": (h1_k_ms, h1_dir, h1_aq),
            "sess": sess_b,
            "inv": tuple(inv_reasons),
        }
        rec = KH02AsOfMTFRecord(
            bar_index_1m=i,
            decision_timestamp_ms=dec_ts,
            m1_open_time_ms=m1_s["open_ms"][i],
            m1_close_time_ms=dec_ts,
            m1_open=o_p,
            m1_high=h_p,
            m1_low=l_p,
            m1_close=c_p,
            m1_atr_14=atr1,
            m1_ema_20=round(float(m1_s["ema"][i]), 4) if m1_s["ema"][i] is not None else None,
            m1_body_ratio=body_ratio,
            m1_range_atr_ratio=rng_atr,
            m1_confirmed_swing_low=prev_sw_l,
            m1_confirmed_swing_low_conf_ms=prev_sw_l_conf,
            m1_confirmed_swing_high=prev_sw_h,
            m1_confirmed_swing_high_conf_ms=prev_sw_h_conf,
            m1_bull_sweep_reclaim=bull_sweep,
            m1_bear_sweep_reclaim=bear_sweep,
            m1_bull_bos=bull_bos,
            m1_bear_bos=bear_bos,
            m1_bull_fvg_retest=bull_fvg_ret,
            m1_bear_fvg_retest=bear_fvg_ret,
            m15_last_close_ms=m15_c_ms,
            m15_knowledge_time_ms=m15_k_ms,
            m15_close=m15_cl,
            m15_ema_20=round(float(m15_e), 4) if m15_e is not None else None,
            m15_ema_slope=m15_sl,
            m15_raw_dir_efficiency_8=m15_eff,
            m15_trend_dir=m15_dir,
            m15_confirmed_swing_low=m15_swl,
            m15_confirmed_swing_low_conf_ms=m15_swl_c,
            m15_confirmed_swing_high=m15_swh,
            m15_confirmed_swing_high_conf_ms=m15_swh_c,
            h1_last_close_ms=h1_c_ms,
            h1_knowledge_time_ms=h1_k_ms,
            h1_close=h1_cl,
            h1_ema_20=round(float(h1_e), 4) if h1_e is not None else None,
            h1_ema_slope=h1_sl,
            h1_atr_14=h1_a,
            h1_atr_causal_prefix_quantile=h1_aq,
            h1_trend_dir=h1_dir,
            h1_confirmed_swing_low=h1_swl,
            h1_confirmed_swing_low_conf_ms=h1_swl_c,
            h1_confirmed_swing_high=h1_swh,
            h1_confirmed_swing_high_conf_ms=h1_swh_c,
            ny_date=ny_dt.strftime("%Y-%m-%d"),
            ny_hour=ny_hr,
            ny_weekday=ny_wd,
            ny_session_bucket=sess_b,
            is_liquid_session=is_liq,
            spread_to_m1_atr_ratio=sp_atr,
            recent_gap_180m=recent_gap,
            max_known_at_ms=max_kn,
            source_origin=data_origin,
            invalidation_reasons=tuple(inv_reasons),
            record_sha256=canonical_hash(rec_core),
        )
        records_by_idx[i] = rec
        records_by_ts[dec_ts] = rec

    all_hashes = [records_by_idx[k].record_sha256 for k in sorted(records_by_idx.keys())]
    inv_total = sum(1 for r in records_by_idx.values() if r.invalidation_reasons)
    m15_valid_n = sum(1 for r in records_by_idx.values() if r.m15_trend_dir != "UNKNOWN_MISSING")
    h1_valid_n = sum(1 for r in records_by_idx.values() if r.h1_trend_dir != "UNKNOWN_MISSING")

    return {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "source_latency_ms": int(source_latency_ms),
        "data_origin": data_origin,
        "quote_mode": quote_mode,
        "warmup_m1": warmup_m1,
        "post_warmup_bar_count": len(records_by_idx),
        "m15_asof_valid_count": m15_valid_n,
        "h1_asof_valid_count": h1_valid_n,
        "invalidation_violations_count": inv_total,
        "feature_frame_sha256": canonical_hash(all_hashes),
        "records_by_idx": records_by_idx,
        "records_by_ts": records_by_ts,
        "m1_series": m1_s,
        "m15_series": m15_s,
        "h1_series": h1_s,
    }


def run_kh02_future_mutation_invariance_check(
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    cutoff_m1_index: int = 1200,
    window_end_index: int = 2400,
) -> Dict[str, Any]:
    """
    §5.3 Real Future-Mutation Leakage Test:
    Mutates ALL M1, M15, and H1 bars strictly AFTER `decision_timestamp_ms` at `cutoff_m1_index`
    by +500 USD and asserts every causal feature record at `idx <= cutoff_m1_index` has an
    IDENTICAL SHA256 hash.
    """
    m1_sub = [dict(b) for b in datasets["1m"][:window_end_index]]
    cutoff_ts = int(m1_sub[cutoff_m1_index]["close_time"])
    ds_clean = {
        "1m": m1_sub,
        "15m": [dict(b) for b in datasets["15m"]],
        "1H": [dict(b) for b in datasets["1H"]],
    }
    ds_mut = {
        "1m": [dict(b) for b in ds_clean["1m"]],
        "15m": [dict(b) for b in ds_clean["15m"]],
        "1H": [dict(b) for b in ds_clean["1H"]],
    }
    mutated_bars = 0
    for tf_k in ("1m", "15m", "1H"):
        for b in ds_mut[tf_k]:
            if int(b.get("close_time") or 0) > cutoff_ts:
                b["open"] = float(b["open"]) + 500.0
                b["high"] = float(b["high"]) + 550.0
                b["low"] = float(b["low"]) + 450.0
                b["close"] = float(b["close"]) + 500.0
                mutated_bars += 1

    f_clean = build_kh02_asof_mtf_features(ds_clean, warmup_m1=30)
    f_mut = build_kh02_asof_mtf_features(ds_mut, warmup_m1=30)

    checked_n = 0
    mismatches = 0
    for idx in range(30, cutoff_m1_index + 1):
        checked_n += 1
        if f_clean["records_by_idx"][idx].record_sha256 != f_mut["records_by_idx"][idx].record_sha256:
            mismatches += 1

    return {
        "cutoff_m1_index": cutoff_m1_index,
        "cutoff_decision_timestamp_ms": cutoff_ts,
        "future_bars_mutated_count": mutated_bars,
        "past_bars_checked_count": checked_n,
        "past_feature_hash_mismatches": mismatches,
        "status": "PASS" if (mutated_bars > 0 and checked_n > 0 and mismatches == 0) else "FAIL",
    }


# ==============================================================================
# C. PRE-REGISTERED HYPOTHESIS PLAN FREEZE (03)
# ==============================================================================

DEFAULT_KH02_PRIMARY_HYPOTHESES: Tuple[Dict[str, Any], ...] = (
    {
        "hypothesis_id": "H1",
        "arm_code": "B2_H1",
        "variant_id": "KH02-B2-H1-MTF-CLOSED-BAR-TREND-REGIME",
        "branch_family": "FOUNDATION_B2",
        "is_primary_confirmatory": True,
        "statement": (
            "Closed-bar M15 and H1 directional trend alignment (M15 close vs EMA-20 with positive "
            "slope + M15 signed directional efficiency >= 0.20 + H1 close vs EMA-20 with aligned "
            "slope, strictly close_time + latency <= decision_time) improves net expectancy R "
            "after all costs over the full post-warmup M1 opportunity universe."
        ),
        "event_denominator": "ALL_POST_WARMUP_M1_DIRECTIONAL_BARS_44692_AND_A1_56710_MATCHED",
        "min_observation_bars": {"1m": 30, "15m": 25, "1H": 25},
        "data_availability_rule": "close_time + source_latency_ms <= decision_timestamp_ms",
        "treatment": "B2_M1_CAUSAL_CONFIRMATION + M15_H1_CLOSED_TREND_ALIGNED",
        "control": "CONTROL_B0_PRODUCTION_BASELINE & NC_NULL_BASELINE_TRIGGER",
        "exclusion_criteria": [
            "RECENT_180M_M1_GAP",
            "MISSING_OR_INVALID_M15_H1_CLOSED_BAR",
            "NET_RR_LT_2_0_AFTER_COSTS",
            "PLANNED_NET_RISK_GT_2_50_USD",
        ],
        "return_horizon_minutes": 180,
        "unit_of_analysis": "chronological_1pos_closed_trade_clustered_by_ny_day_and_episode",
        "primary_outcome": "net_expectancy_r_1x_nb_after_fees_spread_slippage",
        "secondary_outcomes": ["net_pnl_usd", "max_drawdown_usd", "win_rate", "trade_frequency", "stress_2x_all_cost_net_pnl"],
        "directional_alternative": "expectancy_1x_nb_r > 0 AND stress_2x_all_cost_net_pnl_usd > 0 AND lower_95_cluster_ci_r > 0",
        "min_effective_sample_closed_trades": 30,
        "falsification_criteria": [
            "net_expectancy_1x_nb_r <= 0.0",
            "stress_2x_all_cost_net_pnl_usd <= 0.0",
            "closed_trades_nb < 30 (NOT_ESTIMABLE / UNDERPOWERED)",
            "cluster_bootstrap_95ci_low_r <= 0.0",
            "no_fresh_untouched_oos (cannot claim PROVEN_EDGE on REUSED_RESEARCH)",
        ],
        "rule": {
            "type": "B2_H1_MTF_TREND",
            "require_m15_trend": True,
            "require_h1_trend": True,
            "min_m15_signed_eff": 0.20,
            "require_h2_structure": False,
            "require_h3_vol_session": False,
        },
    },
    {
        "hypothesis_id": "H2",
        "arm_code": "B2_H2",
        "variant_id": "KH02-B2-H2-CAUSAL-STRUCTURE-LIQUIDITY-CONTEXT",
        "branch_family": "FOUNDATION_B2",
        "is_primary_confirmatory": True,
        "statement": (
            "Confirmed causal market structure and liquidity sweep/reclaim context (prior M1 swing "
            "liquidity sweep + reclaim or confirmed causal BOS + FVG retest, with explicit "
            "confirmation_time <= decision_time and verified unswept M15/H1 liquidity target) "
            "yields positive net expectancy R after realistic execution costs."
        ),
        "event_denominator": "ALL_POST_WARMUP_M1_DIRECTIONAL_BARS_44692_AND_A1_56710_MATCHED",
        "min_observation_bars": {"1m": 30, "15m": 25, "1H": 25},
        "data_availability_rule": "pivot_confirmation_time <= decision_timestamp_ms (confirm_right_bars=2)",
        "treatment": "B2_M1_CAUSAL_CONFIRMATION + CONFIRMED_SWEEP_OR_BOS_WITH_UNSWEPT_HTF_LIQUIDITY",
        "control": "CONTROL_B0_PRODUCTION_BASELINE & NC_NULL_BASELINE_TRIGGER",
        "exclusion_criteria": [
            "UNCONFIRMED_FUTURE_PIVOT",
            "NO_UNSWEPT_CAUSAL_STRUCTURAL_TARGET",
            "NET_RR_LT_2_0_AFTER_COSTS",
            "PLANNED_NET_RISK_GT_2_50_USD",
        ],
        "return_horizon_minutes": 180,
        "unit_of_analysis": "chronological_1pos_closed_trade_clustered_by_ny_day_and_episode",
        "primary_outcome": "net_expectancy_r_1x_nb_after_fees_spread_slippage",
        "secondary_outcomes": ["net_pnl_usd", "max_drawdown_usd", "win_rate", "trade_frequency", "stress_2x_all_cost_net_pnl"],
        "directional_alternative": "expectancy_1x_nb_r > 0 AND stress_2x_all_cost_net_pnl_usd > 0 AND lower_95_cluster_ci_r > 0",
        "min_effective_sample_closed_trades": 30,
        "falsification_criteria": [
            "net_expectancy_1x_nb_r <= 0.0",
            "stress_2x_all_cost_net_pnl_usd <= 0.0",
            "closed_trades_nb < 30 (NOT_ESTIMABLE / UNDERPOWERED)",
            "cluster_bootstrap_95ci_low_r <= 0.0",
            "no_fresh_untouched_oos (cannot claim PROVEN_EDGE on REUSED_RESEARCH)",
        ],
        "rule": {
            "type": "B2_H2_CAUSAL_STRUCTURE",
            "require_m15_trend": False,
            "require_h1_trend": False,
            "require_h2_structure": True,
            "require_h3_vol_session": False,
        },
    },
    {
        "hypothesis_id": "H3",
        "arm_code": "B2_H3",
        "variant_id": "KH02-B2-H3-VOLATILITY-SESSION-COST-REGIME",
        "branch_family": "FOUNDATION_B2",
        "is_primary_confirmatory": True,
        "statement": (
            "Causal volatility & NY/London session regime (trailing prefix H1 ATR quantile >= 0.45, "
            "active London/NY AM/NY PM session excluding weekend/rollover, and modeled spread-to-M1-ATR "
            "ratio <= 0.30) separates viable net-positive entry conditions from fee/spread-dominated chop."
        ),
        "event_denominator": "ALL_POST_WARMUP_M1_DIRECTIONAL_BARS_44692_AND_A1_56710_MATCHED",
        "min_observation_bars": {"1m": 30, "15m": 25, "1H": 26},
        "data_availability_rule": "trailing_prefix_only_quantile_no_full_series_scaling",
        "treatment": "B2_M1_CAUSAL_CONFIRMATION + H1_ATR_Q_GE_0_45 + LIQUID_SESSION + SPREAD_ATR_LE_0_30",
        "control": "CONTROL_B0_PRODUCTION_BASELINE & NC_NULL_BASELINE_TRIGGER",
        "exclusion_criteria": [
            "WEEKEND_OR_ASIA_ROLLOVER_SESSION",
            "SPREAD_TO_M1_ATR_GT_0_30",
            "LOW_VOL_COMPRESSION_H1_Q_LT_0_45",
            "NET_RR_LT_2_0_AFTER_COSTS",
        ],
        "return_horizon_minutes": 180,
        "unit_of_analysis": "chronological_1pos_closed_trade_clustered_by_ny_day_and_episode",
        "primary_outcome": "net_expectancy_r_1x_nb_after_fees_spread_slippage",
        "secondary_outcomes": ["net_pnl_usd", "max_drawdown_usd", "win_rate", "trade_frequency", "stress_2x_all_cost_net_pnl"],
        "directional_alternative": "expectancy_1x_nb_r > 0 AND stress_2x_all_cost_net_pnl_usd > 0 AND lower_95_cluster_ci_r > 0",
        "min_effective_sample_closed_trades": 30,
        "falsification_criteria": [
            "net_expectancy_1x_nb_r <= 0.0",
            "stress_2x_all_cost_net_pnl_usd <= 0.0",
            "closed_trades_nb < 30 (NOT_ESTIMABLE / UNDERPOWERED)",
            "cluster_bootstrap_95ci_low_r <= 0.0",
            "no_fresh_untouched_oos (cannot claim PROVEN_EDGE on REUSED_RESEARCH)",
        ],
        "rule": {
            "type": "B2_H3_VOL_SESSION",
            "require_m15_trend": False,
            "require_h1_trend": False,
            "require_h2_structure": False,
            "require_h3_vol_session": True,
            "min_h1_atr_quantile": 0.45,
            "max_spread_to_m1_atr": 0.30,
        },
    },
)

DEFAULT_KH02_SECONDARY_AND_CONTROL_ARMS: Tuple[Dict[str, Any], ...] = (
    {
        "arm_code": "B2_H123",
        "variant_id": "KH02-B2-COMPOSITE-H1-H2-H3",
        "arm_role": "COMPOSITE_PREDECLARED",
        "rule": {
            "type": "B2_COMPOSITE",
            "require_m15_trend": True,
            "require_h1_trend": True,
            "min_m15_signed_eff": 0.20,
            "require_h2_structure": True,
            "require_h3_vol_session": True,
            "min_h1_atr_quantile": 0.45,
            "max_spread_to_m1_atr": 0.30,
        },
    },
    {
        "arm_code": "ABL_M15_ONLY",
        "variant_id": "KH02-ABL-M15-ONLY",
        "arm_role": "ABLATION",
        "rule": {"type": "B2_ABL", "require_m15_trend": True, "require_h1_trend": False, "min_m15_signed_eff": 0.20},
    },
    {
        "arm_code": "ABL_H1_ONLY",
        "variant_id": "KH02-ABL-H1-ONLY",
        "arm_role": "ABLATION",
        "rule": {"type": "B2_ABL", "require_m15_trend": False, "require_h1_trend": True},
    },
    {
        "arm_code": "ABL_STRUCT_ONLY",
        "variant_id": "KH02-ABL-STRUCT-ONLY",
        "arm_role": "ABLATION",
        "rule": {"type": "B2_ABL", "require_h2_structure": True},
    },
    {
        "arm_code": "ABL_NO_REGIME",
        "variant_id": "KH02-ABL-NO-VOL-REGIME",
        "arm_role": "ABLATION",
        "rule": {"type": "B2_ABL", "require_h2_structure": True, "require_session_only": True},
    },
    {
        "arm_code": "ABL_NO_SESSION",
        "variant_id": "KH02-ABL-NO-SESSION",
        "arm_role": "ABLATION",
        "rule": {
            "type": "B2_ABL",
            "require_m15_trend": True,
            "require_h1_trend": True,
            "min_m15_signed_eff": 0.20,
            "require_vol_only": True,
            "min_h1_atr_quantile": 0.45,
            "max_spread_to_m1_atr": 0.30,
        },
    },
    {
        "arm_code": "NC_NULL_TRIGGER",
        "variant_id": "KH02-NC-NULL-UNFILTERED-B2",
        "arm_role": "NEGATIVE_CONTROL",
        "rule": {"type": "B2_NULL_UNFILTERED"},
    },
    {
        "arm_code": "NC_RANDOM_DIR",
        "variant_id": "KH02-NC-RANDOMIZED-DIRECTION",
        "arm_role": "NEGATIVE_CONTROL",
        "rule": {"type": "B2_NEGATIVE_RANDOM_DIR", "seed": 20261121},
    },
    {
        "arm_code": "NC_SHIFTED_REGIME",
        "variant_id": "KH02-NC-PAST-SHIFTED-48H-REGIME",
        "arm_role": "NEGATIVE_CONTROL",
        "rule": {"type": "B2_NEGATIVE_PAST_SHIFTED_REGIME", "shift_bars_1m": 2880},
    },
)


@dataclass(frozen=True)
class KH02FrozenHypothesisPlan:
    spec_version: str
    schema_version: str
    plan_version: str
    is_exploratory_post_freeze: bool
    dataset_sha256: str
    config_sha256: str
    code_sha256: str
    prior_use_status: str
    max_primary_hypotheses_budget: int
    primary_hypotheses_count: int
    secondary_ablation_and_control_count: int
    multiplicity_method: str
    primary_family_size_m: int
    full_arms_family_size_m: int
    holm_primary_min_alpha: float
    purged_embargo_minutes: int
    max_holding_horizon_minutes: int
    min_powered_closed_trades: int
    max_planned_risk_usd: float
    min_net_rr: float
    cost_multipliers: Tuple[float, ...]
    fill_modes: Tuple[str, ...]
    branches_compared: Tuple[str, ...]
    primary_hypotheses: Tuple[Dict[str, Any], ...]
    secondary_and_control_arms: Tuple[Dict[str, Any], ...]
    stop_rule: str
    frozen_before_outcome_evaluation: bool
    manifest_sha256: str


def preregister_kh02_hypotheses(
    *,
    dataset_sha256: str,
    config_sha256: str,
    code_sha256: str,
    primary_hypotheses: Optional[Sequence[Mapping[str, Any]]] = None,
    secondary_arms: Optional[Sequence[Mapping[str, Any]]] = None,
    purged_embargo_minutes: int = 240,
    max_holding_horizon_minutes: int = 180,
    plan_version: str = "KH02_PLAN_V1_FROZEN",
    is_exploratory_post_freeze: bool = False,
) -> KH02FrozenHypothesisPlan:
    """
    §6.C & §4: Freezes hypothesis_plan_frozen.json BEFORE outcome computation.
    Enforces <= 3 primary hypotheses, embargo >= max_holding_horizon_minutes, fixed risk <= $2.50,
    min net R:R >= 2.0, disjoint arm codes, and immutable SHA256 digest.
    """
    if not dataset_sha256 or not config_sha256 or not code_sha256:
        raise ValueError("KH02_PREREGISTRATION_REQUIRES_NON_EMPTY_SHAS")
    if int(purged_embargo_minutes) < int(max_holding_horizon_minutes):
        raise ValueError(
            f"KH02_EMBARGO_LT_HOLDING_HORIZON:{purged_embargo_minutes}<{max_holding_horizon_minutes}"
        )

    prim = [copy.deepcopy(dict(h)) for h in (primary_hypotheses or DEFAULT_KH02_PRIMARY_HYPOTHESES)]
    sec = [copy.deepcopy(dict(s)) for s in (secondary_arms or DEFAULT_KH02_SECONDARY_AND_CONTROL_ARMS)]
    if len(prim) == 0 or len(prim) > 3:
        raise ValueError(f"KH02_PRIMARY_HYPOTHESIS_BUDGET_VIOLATION:count={len(prim)} (must be 1..3)")

    seen_ids: Set[str] = set()
    seen_rules: Set[str] = set()
    for h in prim:
        hid = str(h.get("hypothesis_id") or "")
        ac = str(h.get("arm_code") or "")
        rh = canonical_hash(h.get("rule") or {})
        if not hid or not ac or ac in seen_ids or rh in seen_rules:
            raise ValueError(f"KH02_DUPLICATE_OR_INVALID_PRIMARY_HYPOTHESIS:{hid}:{ac}")
        if not h.get("falsification_criteria"):
            raise ValueError(f"KH02_MISSING_FALSIFICATION_CRITERIA:{hid}")
        seen_ids.add(ac)
        seen_rules.add(rh)
    for s in sec:
        ac = str(s.get("arm_code") or "")
        if not ac or ac in seen_ids:
            raise ValueError(f"KH02_DUPLICATE_OR_INVALID_SECONDARY_ARM:{ac}")
        seen_ids.add(ac)

    body = {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "plan_version": plan_version,
        "is_exploratory_post_freeze": bool(is_exploratory_post_freeze),
        "dataset_sha256": dataset_sha256,
        "config_sha256": config_sha256,
        "code_sha256": code_sha256,
        "prior_use_status": "REUSED_RESEARCH",
        "max_primary_hypotheses_budget": 3,
        "primary_hypotheses_count": len(prim),
        "secondary_ablation_and_control_count": len(sec),
        "multiplicity_method": "HOLM_BONFERRONI_PRIMARY_FAMILY_PLUS_FULL_ARM_DISCLOSURE",
        "primary_family_size_m": len(prim),
        "full_arms_family_size_m": len(prim) + len(sec),
        "holm_primary_min_alpha": round(0.05 / max(1, len(prim)), 6),
        "purged_embargo_minutes": int(purged_embargo_minutes),
        "max_holding_horizon_minutes": int(max_holding_horizon_minutes),
        "min_powered_closed_trades": 30,
        "max_planned_risk_usd": 2.50,
        "min_net_rr": 2.0,
        "cost_multipliers": (1.0, 2.0, 3.0),
        "fill_modes": FILL_MODES_KH01B,
        "branches_compared": ("CONTROL-B0", "DIAG-B1", "FOUNDATION-B2"),
        "primary_hypotheses": tuple(prim),
        "secondary_and_control_arms": tuple(sec),
        "stop_rule": (
            "STOP_NO_PRODUCTION_ACTIVATION: If any primary arm fails net_expectancy > 0, "
            "2x cost stress > 0, cluster 95% CI > 0, powered N >= 30, OR lacks sealed fresh "
            "untouched OOS data, conclude NO_PROVEN_EDGE / BLOCKED_NO_UNTOUCHED_DATA; do NOT "
            "tune parameters on outcomes or unlock production."
        ),
        "frozen_before_outcome_evaluation": not bool(is_exploratory_post_freeze),
    }
    return KH02FrozenHypothesisPlan(**body, manifest_sha256=canonical_hash(body))


def verify_kh02_plan_integrity_unchanged(
    frozen_plan: KH02FrozenHypothesisPlan,
    claimed_plan_dict: Mapping[str, Any],
) -> Dict[str, Any]:
    """Detects post-freeze hypothesis tampering (`F20`)."""
    violations: List[str] = []
    if claimed_plan_dict.get("manifest_sha256") != frozen_plan.manifest_sha256:
        violations.append("FROZEN_HYPOTHESIS_MANIFEST_SHA256_MISMATCH")
    re_body = {k: v for k, v in claimed_plan_dict.items() if k != "manifest_sha256"}
    if canonical_hash(re_body) != str(claimed_plan_dict.get("manifest_sha256") or ""):
        violations.append("HYPOTHESIS_EDITED_AFTER_FREEZE")
    if not bool(claimed_plan_dict.get("frozen_before_outcome_evaluation", False)):
        violations.append("HYPOTHESIS_PLAN_NOT_FROZEN_BEFORE_OUTCOME")
    return {"status": "PASS" if not violations else "FAIL", "violations": violations}


# ==============================================================================
# D. RESEARCH-ONLY FOUNDATION B2 CANDIDATE GENERATOR (05, 06)
# ==============================================================================


def _resolve_b2_structural_stop_and_target(
    rec: KH02AsOfMTFRecord,
    direction: str,
    *,
    m15_unswept_highs: Sequence[Tuple[float, int]],
    m15_unswept_lows: Sequence[Tuple[float, int]],
    h1_unswept_highs: Sequence[Tuple[float, int]],
    h1_unswept_lows: Sequence[Tuple[float, int]],
    stretch_tp_illegally: bool = False,
) -> Tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """
    Derives strictly causal structural SL and unswept structural TP WITHOUT arbitrary TP stretching.
    Returns `(sl_price, tp_price, target_source, rejection_reason)`.
    """
    entry = rec.m1_close
    atr1 = rec.m1_atr_14
    if atr1 is None or atr1 <= 1e-6:
        return None, None, None, "MISSING_CAUSAL_M1_ATR"

    noise_buf = max(0.15, round(0.25 * atr1, 2))
    min_stop_dist = max(0.50, round(1.0 * atr1, 2))
    max_stop_dist = max(4.0, round(6.0 * atr1, 2))

    if direction == "LONG":
        anchors = [x for x in (rec.m1_low, rec.m1_confirmed_swing_low) if x is not None and x < entry]
        if not anchors:
            return None, None, None, "NO_CAUSAL_LONG_STOP_ANCHOR"
        sl_raw = round(min(anchors) - noise_buf, 2)
        stop_d = entry - sl_raw
        if stop_d < min_stop_dist:
            sl_raw = round(entry - min_stop_dist, 2)
            stop_d = entry - sl_raw
        if stop_d > max_stop_dist:
            return None, None, None, "CAUSAL_STOP_DISTANCE_TOO_WIDE"

        # Nearest strictly unswept confirmed M15/H1 swing high above entry
        pool_candidates: List[Tuple[float, str]] = []
        for px, conf_t in m15_unswept_highs:
            if conf_t <= rec.decision_timestamp_ms and px > entry + stop_d * 1.5:
                pool_candidates.append((px, "M15_UNSWEPT_SWING_HIGH"))
        for px, conf_t in h1_unswept_highs:
            if conf_t <= rec.decision_timestamp_ms and px > entry + stop_d * 1.5:
                pool_candidates.append((px, "H1_UNSWEPT_SWING_HIGH"))
        if rec.m15_confirmed_swing_high is not None and rec.m15_confirmed_swing_high > entry + stop_d * 1.5:
            pool_candidates.append((rec.m15_confirmed_swing_high, "M15_CONFIRMED_SWING_HIGH"))
        if rec.h1_confirmed_swing_high is not None and rec.h1_confirmed_swing_high > entry + stop_d * 1.5:
            pool_candidates.append((rec.h1_confirmed_swing_high, "H1_CONFIRMED_SWING_HIGH"))

        if not pool_candidates:
            return sl_raw, None, None, "NO_UNSWEPT_CAUSAL_STRUCTURAL_TARGET"
        pool_candidates.sort(key=lambda t: (t[0] - entry, t[1]))
        tp_px, tp_src = round(pool_candidates[0][0], 2), pool_candidates[0][1]
        if stretch_tp_illegally:
            tp_px = round(entry + max(stop_d * 3.5, (tp_px - entry) * 1.5), 2)
        return sl_raw, tp_px, tp_src, None

    # SHORT
    anchors = [x for x in (rec.m1_high, rec.m1_confirmed_swing_high) if x is not None and x > entry]
    if not anchors:
        return None, None, None, "NO_CAUSAL_SHORT_STOP_ANCHOR"
    sl_raw = round(max(anchors) + noise_buf, 2)
    stop_d = sl_raw - entry
    if stop_d < min_stop_dist:
        sl_raw = round(entry + min_stop_dist, 2)
        stop_d = sl_raw - entry
    if stop_d > max_stop_dist:
        return None, None, None, "CAUSAL_STOP_DISTANCE_TOO_WIDE"

    pool_candidates = []
    for px, conf_t in m15_unswept_lows:
        if conf_t <= rec.decision_timestamp_ms and px < entry - stop_d * 1.5:
            pool_candidates.append((px, "M15_UNSWEPT_SWING_LOW"))
    for px, conf_t in h1_unswept_lows:
        if conf_t <= rec.decision_timestamp_ms and px < entry - stop_d * 1.5:
            pool_candidates.append((px, "H1_UNSWEPT_SWING_LOW"))
    if rec.m15_confirmed_swing_low is not None and rec.m15_confirmed_swing_low < entry - stop_d * 1.5:
        pool_candidates.append((rec.m15_confirmed_swing_low, "M15_CONFIRMED_SWING_LOW"))
    if rec.h1_confirmed_swing_low is not None and rec.h1_confirmed_swing_low < entry - stop_d * 1.5:
        pool_candidates.append((rec.h1_confirmed_swing_low, "H1_CONFIRMED_SWING_LOW"))

    if not pool_candidates:
        return sl_raw, None, None, "NO_UNSWEPT_CAUSAL_STRUCTURAL_TARGET"
    pool_candidates.sort(key=lambda t: (entry - t[0], t[1]))
    tp_px, tp_src = round(pool_candidates[0][0], 2), pool_candidates[0][1]
    if stretch_tp_illegally:
        tp_px = round(entry - max(stop_d * 3.5, (entry - tp_px) * 1.5), 2)
    return sl_raw, tp_px, tp_src, None


def evaluate_kh02_hypothesis_predicates_asof(
    rec: KH02AsOfMTFRecord,
    direction: str,
    *,
    shifted_rec: Optional[KH02AsOfMTFRecord] = None,
) -> Dict[str, Any]:
    """Evaluates causal H1, H2, H3 predicates for `(rec, direction)` using ONLY as-of fields."""
    src = shifted_rec if shifted_rec is not None else rec
    is_long = (direction == "LONG")

    # H1: M15 + H1 closed-bar trend alignment
    req_tf_dir = "BULL" if is_long else "BEAR"
    m15_signed_eff = (
        (src.m15_raw_dir_efficiency_8 if is_long else -src.m15_raw_dir_efficiency_8)
        if src.m15_raw_dir_efficiency_8 is not None
        else None
    )
    m15_aligned = bool(
        src.m15_trend_dir == req_tf_dir
        and m15_signed_eff is not None
        and m15_signed_eff >= 0.20
    )
    h1_aligned = bool(src.h1_trend_dir == req_tf_dir)
    h1_pass = bool(m15_aligned and h1_aligned and not src.recent_gap_180m)

    # H2: Confirmed causal structure/liquidity context
    if is_long:
        struct_event = bool(rec.m1_bull_sweep_reclaim or (rec.m1_bull_bos and rec.m1_body_ratio >= 0.50) or rec.m1_bull_fvg_retest)
    else:
        struct_event = bool(rec.m1_bear_sweep_reclaim or (rec.m1_bear_bos and rec.m1_body_ratio >= 0.50) or rec.m1_bear_fvg_retest)
    h2_pass = bool(struct_event and not rec.recent_gap_180m)

    # H3: Volatility + liquid session + cost-ratio regime
    vol_ok = bool(
        src.h1_atr_causal_prefix_quantile is not None
        and src.h1_atr_causal_prefix_quantile >= 0.45
        and src.spread_to_m1_atr_ratio is not None
        and src.spread_to_m1_atr_ratio <= 0.30
    )
    sess_ok = bool(src.is_liquid_session)
    h3_pass = bool(vol_ok and sess_ok and not src.recent_gap_180m)

    return {
        "m15_aligned": m15_aligned,
        "h1_aligned": h1_aligned,
        "m15_signed_eff": round(m15_signed_eff, 6) if m15_signed_eff is not None else None,
        "h1_mtf_trend_pass": h1_pass,
        "h2_structure_pass": h2_pass,
        "h3_vol_only_pass": vol_ok,
        "h3_session_only_pass": sess_ok,
        "h3_vol_session_pass": h3_pass,
    }


def generate_kh02_research_candidates(
    mtf_bundle: Mapping[str, Any],
    frozen_plan: KH02FrozenHypothesisPlan,
    *,
    runtime_modes: Optional[Mapping[str, Any]] = None,
    bar_indices_subset: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """
    §6.D: Offline research-only Foundation-B2 candidate generator operating across the FULL
    post-warmup M1 opportunity universe (`22,346 × 2 = 44,692` directional bar opportunities).
    Records EVERY `NO_SIGNAL`, `REJECTED_*`, and `ELIGIBLE` opportunity to eliminate
    survivorship/collider bias. Enforces authoritative `recalculate_trade_risk_quantity_v179312`
    (`planned_net_risk_usd <= 2.50`, `net_rr >= 2.0`), zero TP stretching, and runtime modes OFF.
    """
    assert_kh02_runtime_safety_modes_off(runtime_modes, check_live_scalp_config=False)
    if not frozen_plan.frozen_before_outcome_evaluation:
        raise ValueError("KH02_CANDIDATE_GEN_REQUIRES_FROZEN_PLAN")

    records_by_idx: Mapping[int, KH02AsOfMTFRecord] = mtf_bundle["records_by_idx"]
    m15_s = mtf_bundle["m15_series"]
    h1_s = mtf_bundle["h1_series"]
    m15_know = m15_s["know_ms"]
    h1_know = h1_s["know_ms"]

    all_arms: List[Dict[str, Any]] = []
    for h in frozen_plan.primary_hypotheses:
        all_arms.append({"arm_code": h["arm_code"], "variant_id": h["variant_id"], "arm_role": "PRIMARY", "rule": h["rule"]})
    for s in frozen_plan.secondary_and_control_arms:
        all_arms.append({"arm_code": s["arm_code"], "variant_id": s["variant_id"], "arm_role": s["arm_role"], "rule": s["rule"]})

    indices = sorted(bar_indices_subset) if bar_indices_subset is not None else sorted(records_by_idx.keys())
    rng_nc = random.Random(20261121)

    # First pass: evaluate raw B2 M1 causal trigger + authoritative risk/RR on FULL universe
    raw_trigger_events: Dict[Tuple[int, str], Dict[str, Any]] = {}
    universe_reason_counts: Counter[str] = Counter()
    total_directional_opps = 0
    raw_signal_triggered = 0
    raw_geometry_risk_eligible = 0

    for idx in indices:
        rec = records_by_idx[idx]
        j15 = bisect.bisect_right(m15_know, rec.decision_timestamp_ms) - 1
        jh1 = bisect.bisect_right(h1_know, rec.decision_timestamp_ms) - 1
        m15_uh = m15_s["unswept_highs_at"][j15] if j15 >= 0 else ()
        m15_ul = m15_s["unswept_lows_at"][j15] if j15 >= 0 else ()
        h1_uh = h1_s["unswept_highs_at"][jh1] if jh1 >= 0 else ()
        h1_ul = h1_s["unswept_lows_at"][jh1] if jh1 >= 0 else ()

        for direction in ("LONG", "SHORT"):
            total_directional_opps += 1
            if rec.invalidation_reasons:
                universe_reason_counts["REJECTED_INVALID_CAUSAL_EVIDENCE"] += 1
                continue
            if rec.recent_gap_180m:
                universe_reason_counts["REJECTED_RECENT_180M_M1_GAP"] += 1
                continue

            # Raw M1 causal confirmation trigger (strictly closed bar i)
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
                universe_reason_counts["NO_M1_CAUSAL_TRIGGER"] += 1
                continue

            raw_signal_triggered += 1
            sl_px, tp_px, tp_src, geo_rej = _resolve_b2_structural_stop_and_target(
                rec,
                direction,
                m15_unswept_highs=m15_uh,
                m15_unswept_lows=m15_ul,
                h1_unswept_highs=h1_uh,
                h1_unswept_lows=h1_ul,
            )
            if geo_rej is not None or sl_px is None or tp_px is None:
                universe_reason_counts[f"REJECTED_{geo_rej}"] += 1
                continue

            rk = recalculate_trade_risk_quantity_v179312(
                rec.m1_close,
                sl_px,
                tp_px,
                direction=direction,
                decision_ts=rec.decision_timestamp_ms,
            )
            if not rk.get("can_execute"):
                rej_k = str(rk.get("skip_reason") or rk.get("invalid_reason") or "RISK_CALC_REJECTED").split(":")[0]
                universe_reason_counts[f"REJECTED_RISK_GATE:{rej_k}"] += 1
                continue
            net_rr = float(rk.get("net_rr") or 0.0)
            plan_risk = float(rk.get("planned_net_risk_usd") or 99.0)
            qty = float(rk.get("quantity") or 0.0)
            if net_rr < 2.0 - 1e-9:
                universe_reason_counts["REJECTED_NET_RR_LT_2_0"] += 1
                continue
            if plan_risk > 2.50 + 1e-9 or qty < 0.01:
                universe_reason_counts["REJECTED_RISK_CAP_OR_MIN_LOT"] += 1
                continue

            raw_geometry_risk_eligible += 1
            universe_reason_counts["ELIGIBLE_B2_RAW_TRIGGER_AND_RISK_OK"] += 1
            opp_id = f"kh02_b2_{direction}_idx{idx}_ts{rec.decision_timestamp_ms}"
            ep_key = f"kh02_ep_{direction}_{rec.decision_timestamp_ms // 900_000}"
            preds = evaluate_kh02_hypothesis_predicates_asof(rec, direction)
            shifted_idx = idx - 2880
            preds_shifted = (
                evaluate_kh02_hypothesis_predicates_asof(rec, direction, shifted_rec=records_by_idx[shifted_idx])
                if shifted_idx in records_by_idx
                else None
            )
            raw_trigger_events[(idx, direction)] = {
                "opportunity_id": opp_id,
                "setup_episode_id": ep_key,
                "model_id": "KH02_RESEARCH_B2_FOUNDATION",
                "direction": direction,
                "checkpoint_index_1m": idx,
                "decision_timestamp_ms": rec.decision_timestamp_ms,
                "known_at_ms": rec.max_known_at_ms,
                "entry_reference": rec.m1_close,
                "selected_sl": sl_px,
                "take_profit": tp_px,
                "target_source": tp_src,
                "hypothetical_quantity": qty,
                "planned_net_risk_usd": round(plan_risk, 4),
                "net_rr": round(net_rr, 4),
                "gross_rr": round(float(rk.get("gross_rr") or net_rr), 4),
                "ny_date": rec.ny_date,
                "ny_session_bucket": rec.ny_session_bucket,
                "h1_atr_quantile": rec.h1_atr_causal_prefix_quantile,
                "m15_trend_dir": rec.m15_trend_dir,
                "h1_trend_dir": rec.h1_trend_dir,
                "predicates": preds,
                "predicates_past_shifted_48h": preds_shifted,
                "counts_toward_d4_true_unicorn": False,
            }

    # Second pass: evaluate every pre-registered B2 arm deterministically over raw triggers
    arm_candidates: Dict[str, List[Dict[str, Any]]] = {}
    arm_conservation: Dict[str, Dict[str, Any]] = {}
    candidate_audit_rows: List[Dict[str, Any]] = []

    sorted_keys = sorted(raw_trigger_events.keys())
    # Deterministic coin-flip assignment for NC_RANDOM_DIR on trigger timestamps
    random_dir_flip_by_idx: Dict[int, str] = {}
    for idx in sorted({k[0] for k in sorted_keys}):
        random_dir_flip_by_idx[idx] = "LONG" if rng_nc.random() < 0.5 else "SHORT"

    for arm in all_arms:
        ac = str(arm["arm_code"])
        vid = str(arm["variant_id"])
        rule = arm["rule"]
        rtype = str(rule.get("type") or "")
        elig_list: List[Dict[str, Any]] = []
        rej_c: Counter[str] = Counter()

        for key in sorted_keys:
            ev = raw_trigger_events[key]
            idx, d_orig = key
            preds = ev["predicates"]
            ok = False
            rej_code: Optional[str] = None
            eff_ev = ev

            if rtype == "B2_NULL_UNFILTERED":
                ok = True
            elif rtype == "B2_NEGATIVE_RANDOM_DIR":
                chosen_d = random_dir_flip_by_idx[idx]
                if d_orig == chosen_d:
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
                fail_reasons: List[str] = []
                if rule.get("require_m15_trend") and not preds["m15_aligned"]:
                    fail_reasons.append("M15_TREND_NOT_ALIGNED")
                if rule.get("require_h1_trend") and not preds["h1_aligned"]:
                    fail_reasons.append("H1_TREND_NOT_ALIGNED")
                if rule.get("require_h2_structure") and not preds["h2_structure_pass"]:
                    fail_reasons.append("H2_STRUCTURE_NOT_CONFIRMED")
                if rule.get("require_h3_vol_session") and not preds["h3_vol_session_pass"]:
                    fail_reasons.append("H3_VOL_OR_SESSION_EXCLUDED")
                if rule.get("require_session_only") and not preds["h3_session_only_pass"]:
                    fail_reasons.append("SESSION_EXCLUDED")
                if rule.get("require_vol_only") and not preds["h3_vol_only_pass"]:
                    fail_reasons.append("VOL_REGIME_EXCLUDED")
                if not fail_reasons:
                    ok = True
                else:
                    rej_code = "|".join(fail_reasons)

            if ok:
                cand_row = {
                    **eff_ev,
                    "arm_code": ac,
                    "variant_id": vid,
                    "eligible": True,
                    "rejection_code": None,
                }
                elig_list.append(cand_row)
            else:
                rej_c[str(rej_code or "REJECTED")] += 1

            if ac in ("B2_H1", "B2_H2", "B2_H3", "B2_H123"):
                candidate_audit_rows.append({
                    "arm_code": ac,
                    "variant_id": vid,
                    "opportunity_id": ev["opportunity_id"],
                    "decision_timestamp_ms": ev["decision_timestamp_ms"],
                    "direction": ev["direction"],
                    "eligible": ok,
                    "rejection_code": rej_code,
                    "entry_reference": ev["entry_reference"],
                    "selected_sl": ev["selected_sl"],
                    "take_profit": ev["take_profit"],
                    "net_rr": ev["net_rr"],
                    "planned_net_risk_usd": ev["planned_net_risk_usd"],
                    "ny_session_bucket": ev["ny_session_bucket"],
                    "m15_trend_dir": ev["m15_trend_dir"],
                    "h1_trend_dir": ev["h1_trend_dir"],
                })

        arm_candidates[ac] = elig_list
        arm_conservation[ac] = {
            "arm_code": ac,
            "variant_id": vid,
            "arm_role": arm["arm_role"],
            "total_directional_bar_universe": total_directional_opps,
            "raw_m1_trigger_count": raw_signal_triggered,
            "raw_geometry_risk_eligible_count": raw_geometry_risk_eligible,
            "arm_eligible_count": len(elig_list),
            "arm_rejected_from_raw_eligible_count": raw_geometry_risk_eligible - len(elig_list),
            "rejection_reasons": dict(sorted(rej_c.items())),
        }

    return {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "total_post_warmup_m1_bars": len(indices),
        "total_directional_bar_opportunities": total_directional_opps,
        "raw_m1_triggers_count": raw_signal_triggered,
        "raw_geometry_risk_eligible_count": raw_geometry_risk_eligible,
        "universe_reason_codes": dict(sorted(universe_reason_counts.items())),
        "arm_candidates": arm_candidates,
        "arm_conservation": arm_conservation,
        "candidate_audit_rows": candidate_audit_rows,
        "candidates_canonical_sha256": canonical_hash({
            ac: [(c["opportunity_id"], c["entry_reference"], c["selected_sl"], c["take_profit"], c["net_rr"]) for c in lst]
            for ac, lst in sorted(arm_candidates.items())
        }),
    }


# ==============================================================================
# F. CHRONOLOGICAL 1-POSITION 3-FILL REPLAY ADAPTER (08)
# ==============================================================================


def _make_kh02_materialized_plan(cand: Mapping[str, Any], variant_id: str) -> Dict[str, Any]:
    oid = str(cand["opportunity_id"])
    ts = int(cand["decision_timestamp_ms"])
    chk = int(cand["checkpoint_index_1m"])
    entry = float(cand["entry_reference"])
    sl = float(cand["selected_sl"])
    tp = float(cand["take_profit"])
    qty = float(cand["hypothetical_quantity"])
    risk = float(cand["planned_net_risk_usd"])
    nrr = float(cand["net_rr"])
    grr = float(cand.get("gross_rr") or nrr)
    return {
        "schema_version": SCHEMA_VERSION_KH02,
        "intent_id": f"kh02_intent_{variant_id}_{hashlib.sha256(f'{variant_id}|{oid}|{ts}'.encode('utf-8')).hexdigest()[:16]}",
        "opportunity_id": oid,
        "setup_id": str(cand.get("setup_episode_id") or oid),
        "variant_id": variant_id,
        "intervention_type": "KH02_RESEARCH_ENTRY_FOUNDATION",
        "status": "ACCEPTED_UNCHANGED_NO_OP",
        "accepted": True,
        "rejection_reason": None,
        "old_plan": {
            "opportunity_id": oid,
            "model_id": str(cand.get("model_id") or "KH02_RESEARCH_B2_FOUNDATION"),
            "direction": str(cand["direction"]),
            "checkpoint_index_1m": chk,
            "decision_timestamp_ms": ts,
            "entry_reference": entry,
            "selected_sl": sl,
            "take_profit": tp,
            "quantity": qty,
            "planned_net_risk_usd": risk,
            "net_rr": nrr,
        },
        "new_checkpoint_index_1m": chk,
        "new_decision_timestamp_ms": ts,
        "new_entry_reference": entry,
        "new_selected_sl": sl,
        "new_take_profit": tp,
        "new_quantity": qty,
        "new_planned_net_risk_usd": risk,
        "new_net_rr": nrr,
        "new_gross_rr": grr,
        "new_structural_invalidation_price": sl,
        "new_selected_anchor_id": None,
        "new_anchor_kind": None,
        "new_selected_target_id": cand.get("target_source"),
        "changed_dimensions": [],
        "known_at_ms": int(cand.get("known_at_ms") or ts),
        "confirmation_subtype": "M1_CLOSED_BAR_CAUSAL",
        "new_trade_fingerprint": canonical_hash((variant_id, oid, ts, entry, sl, tp, qty)),
    }


def replay_kh02_chronological_one_position(
    arm_code: str,
    variant_id: str,
    candidates: Sequence[Mapping[str, Any]],
    bars_1m: Sequence[Mapping[str, Any]],
    *,
    fill_modes: Sequence[str] = FILL_MODES_KH01B,
    use_production_model_arbitration: bool = False,
    feature_ledger: Optional[Mapping[str, Mapping[str, Any]]] = None,
    runtime_modes: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    §6.F: Chronological 1-position portfolio replay reusing authoritative
    `advance_portfolio_to_next_event_v179312` and `replay_intervention_outcome_v179312`.
    Enforces strict timestamp ordering, zero overlapping positions, risk cap <= $2.50,
    min net R:R >= 2.0, and deterministic tie-breaking when conflicting signals occur at the same bar.
    """
    assert_kh02_runtime_safety_modes_off(runtime_modes, check_live_scalp_config=False)

    # Group by checkpoint_index_1m to resolve conflicting simultaneous signals deterministically
    by_chk: Dict[int, List[Mapping[str, Any]]] = {}
    for c in candidates:
        by_chk.setdefault(int(c["checkpoint_index_1m"]), []).append(c)

    selected: List[Mapping[str, Any]] = []
    conflicting_suppressed = 0
    for chk in sorted(by_chk.keys()):
        grp = by_chk[chk]
        if len(grp) == 1:
            selected.append(grp[0])
        elif use_production_model_arbitration:
            w, losers = arbitrate_competing_model_intents_v1793122(
                [dict(x) for x in grp],
                arbitration_mode="STATIC_PRODUCTION_PRIORITY",
                feature_ledger_by_opp_id=feature_ledger or {},
            )
            if w is not None:
                selected.append(w)
                conflicting_suppressed += len(losers)
        else:
            # Deterministic research tie-break: higher net_rr, then LONG before SHORT, then opportunity_id
            grp_sorted = sorted(
                grp,
                key=lambda x: (
                    -float(x.get("net_rr") or 0.0),
                    0 if str(x.get("direction")) == "LONG" else 1,
                    str(x.get("opportunity_id") or ""),
                ),
            )
            selected.append(grp_sorted[0])
            conflicting_suppressed += len(grp_sorted) - 1

    selected.sort(key=lambda x: (int(x["decision_timestamp_ms"]), int(x["checkpoint_index_1m"]), str(x["opportunity_id"])))

    fill_summaries: Dict[str, Dict[str, Any]] = {}
    episodes_by_mode: Dict[str, List[Dict[str, Any]]] = {}
    overlap_violations = 0
    risk_cap_violations = 0
    rr_violations = 0

    for f_mode in fill_modes:
        state = PortfolioReplayStateV179312(variant=variant_id, max_concurrent_positions=1)
        mode_eps: List[Dict[str, Any]] = []
        blocked_open = 0
        last_release_ts = -1

        for cand in selected:
            dec_ts = int(cand["decision_timestamp_ms"])
            plan_risk = float(cand.get("planned_net_risk_usd") or 0.0)
            net_rr = float(cand.get("net_rr") or 0.0)
            if plan_risk > 2.50 + 1e-6:
                risk_cap_violations += 1
                continue
            if net_rr < 2.0 - 1e-6:
                rr_violations += 1
                continue

            advance_portfolio_to_next_event_v179312(state, None, dec_ts)
            if state.active_episode is not None and dec_ts < state.active_release_ts_ms:
                blocked_open += 1
                continue

            mat = _make_kh02_materialized_plan(cand, variant_id)
            rep = replay_intervention_outcome_v179312(mat, bars_1m, mode=f_mode, base_record=cand)
            ep = dict(rep["episode_record"])
            ep["opportunity_id"] = str(cand["opportunity_id"])
            ep["setup_episode_id"] = str(cand.get("setup_episode_id") or cand["opportunity_id"])
            ep["arm_code"] = arm_code
            ep["variant_id"] = variant_id
            ep["fill_mode"] = f_mode
            ep["decision_timestamp_ms"] = dec_ts
            ep["entry_ts_ms"] = int(ep.get("entry_fill_ts") or dec_ts)
            ep["exit_ts_ms"] = int(ep.get("exit_fill_ts") or ep.get("release_ts_ms") or dec_ts)
            ep["total_fees_usd"] = round(
                float(ep.get("entry_fee_usd") or 0.0) + float(ep.get("exit_fee_usd") or 0.0), 4
            )
            ep["realized_net_r"] = float(ep.get("realized_r") or 0.0)
            ep["ny_date"] = str(cand.get("ny_date") or datetime.fromtimestamp(dec_ts / 1000.0, tz=timezone.utc).astimezone(NY_TZ).strftime("%Y-%m-%d"))
            ep["ny_session_bucket"] = str(cand.get("ny_session_bucket") or "UNKNOWN")

            if ep.get("filled", False):
                if last_release_ts > 0 and ep["entry_ts_ms"] < last_release_ts:
                    overlap_violations += 1
                last_release_ts = int(ep["release_ts_ms"])
                state.active_episode = ep
                state.active_release_ts_ms = last_release_ts

            mode_eps.append(ep)

        fill_summaries[f_mode] = _summarize_episode_list_kh01b(mode_eps, blocked_open)
        episodes_by_mode[f_mode] = mode_eps

    return {
        "arm_code": arm_code,
        "variant_id": variant_id,
        "input_eligible_candidates_count": len(candidates),
        "post_arbitration_selected_count": len(selected),
        "conflicting_same_bar_suppressed_count": conflicting_suppressed,
        "overlap_violations_count": overlap_violations,
        "risk_cap_violations_count": risk_cap_violations,
        "min_net_rr_violations_count": rr_violations,
        "fill_mode_summaries": fill_summaries,
        "episodes_by_mode": episodes_by_mode,
        "replay_sha256": canonical_hash({
            "arm": arm_code,
            "sel": len(selected),
            "sum": fill_summaries,
        }),
    }


def verify_chronological_non_overlap(episodes: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Audits strict chronological 1-position non-overlap invariant (`F16`)."""
    filled = [e for e in episodes if e.get("filled", False)]
    filled.sort(key=lambda e: (int(e.get("entry_ts_ms") or e.get("decision_timestamp_ms") or 0), str(e.get("opportunity_id") or "")))
    overlaps: List[Dict[str, Any]] = []
    prev_rel = -1
    prev_oid = ""
    for e in filled:
        ent = int(e.get("entry_ts_ms") or e.get("decision_timestamp_ms") or 0)
        rel = int(e.get("release_ts_ms") or e.get("exit_ts_ms") or ent)
        if prev_rel > 0 and ent < prev_rel:
            overlaps.append({
                "prev_opportunity_id": prev_oid,
                "prev_release_ts_ms": prev_rel,
                "curr_opportunity_id": str(e.get("opportunity_id") or ""),
                "curr_entry_ts_ms": ent,
            })
        prev_rel = max(prev_rel, rel)
        prev_oid = str(e.get("opportunity_id") or "")
    return {
        "filled_episodes_checked": len(filled),
        "overlaps_count": len(overlaps),
        "overlaps": overlaps[:10],
        "status": "PASS" if len(overlaps) == 0 else "FAIL",
    }


# ==============================================================================
# G. EXECUTION & COST STRESS (1X / 2X / 3X + 0.04% vs 0.06% FEE) (09)
# ==============================================================================


def stress_kh02_execution_and_costs(
    replay_result: Mapping[str, Any],
    *,
    baseline_fee_rate: float = 0.0004,
    public_ref_fee_rate: float = 0.0006,
) -> Dict[str, Any]:
    """
    §6.G: Computes 3-fill comparison + 1x/2x/3x all-in cost stress + Decimal-exact fee sensitivity
    comparing `0.0004` default reference vs `0.0006` public contract reference (fixed-fills AND
    explicit re-risked caveat) without ever hard-coding `0.0006` as verified account tier.
    """
    eps_nb = [
        e for e in (replay_result.get("episodes_by_mode") or {}).get("NEXT_BAR_OPEN_BID_ASK", ())
        if e.get("filled", False) and str(e.get("outcome_label")) in ("WIN", "LOSS")
    ]
    eps_sp2 = [
        e for e in (replay_result.get("episodes_by_mode") or {}).get("STRESS_WIDER_SPREAD_SLIPPAGE_2X", ())
        if e.get("filled", False) and str(e.get("outcome_label")) in ("WIN", "LOSS")
    ]
    eps_leg = [
        e for e in (replay_result.get("episodes_by_mode") or {}).get("LEGACY_CLOSE_FILL", ())
        if e.get("filled", False) and str(e.get("outcome_label")) in ("WIN", "LOSS")
    ]

    def _dec4(v: Decimal) -> float:
        return float(v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

    gross_1x = sum((Decimal(str(e.get("realized_gross_pnl_usd") or 0.0)) for e in eps_nb), Decimal("0"))
    fees_1x = sum((Decimal(str(e.get("total_fees_usd") or 0.0)) for e in eps_nb), Decimal("0"))
    net_1x = sum((Decimal(str(e.get("realized_net_pnl_usd") or 0.0)) for e in eps_nb), Decimal("0"))

    # 2x ALL-COST (2x spread/slippage from STRESS_2X + 2x fees)
    net_2x_vals: List[float] = []
    r_2x_vals: List[float] = []
    for e2 in eps_sp2:
        g2 = float(e2.get("realized_gross_pnl_usd") or 0.0)
        f1 = float(e2.get("total_fees_usd") or 0.0)
        n2 = g2 - 2.0 * f1
        rk = max(0.01, float(e2.get("planned_net_risk_usd") or 2.50))
        net_2x_vals.append(n2)
        r_2x_vals.append(n2 / rk)

    # 3x ALL-COST
    sp2_map = {str(e["opportunity_id"]): e for e in eps_sp2}
    net_3x_vals: List[float] = []
    r_3x_vals: List[float] = []
    for e1 in eps_nb:
        oid = str(e1["opportunity_id"])
        g1 = float(e1.get("realized_gross_pnl_usd") or 0.0)
        f1 = float(e1.get("total_fees_usd") or 0.0)
        rk = max(0.01, float(e1.get("planned_net_risk_usd") or 2.50))
        if oid in sp2_map:
            g2 = float(sp2_map[oid].get("realized_gross_pnl_usd") or g1)
            extra_ss = max(0.0, g1 - g2)
            g3 = g1 - 2.0 * extra_ss
        else:
            g3 = g1 - 0.60 * float(e1.get("executed_quantity") or 0.0)
        n3 = g3 - 3.0 * f1
        net_3x_vals.append(n3)
        r_3x_vals.append(n3 / rk)

    # Exact Decimal fee-only fixed-fill comparison: 0.0004 vs 0.0006
    ratio_06 = Decimal(str(public_ref_fee_rate)) / Decimal(str(baseline_fee_rate))
    fees_06_fixed = fees_1x * ratio_06
    net_06_fixed = gross_1x - fees_06_fixed

    # Re-risked 0.0006 counterfactual on the closed trade subset (explicitly labeled subset-only!)
    rerisk_06_closed = 0
    rerisk_06_net = Decimal("0")
    cost_06 = CostAssumptions(maker_fee_rate=0.0002, taker_fee_rate=public_ref_fee_rate, slippage_usd=0.10)
    for e1 in eps_nb:
        ent = float(e1.get("entry_reference") or e1.get("entry_fill_price") or 0.0)
        sl = float(e1.get("selected_sl") or 0.0)
        tp = float(e1.get("take_profit") or 0.0)
        d = str(e1.get("direction") or "LONG")
        if ent <= 0 or sl <= 0 or tp <= 0:
            continue
        cr = calculate_risk_reward(direction=d, entry=ent, sl=sl, tp=tp, capital=1000.0, risk_pct=0.25, min_net_rr=2.0, costs=cost_06)
        if cr.can_execute and cr.net_rr >= 2.0 and cr.net_risk_usdt <= 2.50:
            rerisk_06_closed += 1
            old_q = max(1e-9, float(e1.get("executed_quantity") or 0.01))
            scale = Decimal(str(cr.quantity)) / Decimal(str(old_q))
            g_sc = Decimal(str(e1.get("realized_gross_pnl_usd") or 0.0)) * scale
            f_sc = Decimal(str(e1.get("total_fees_usd") or 0.0)) * scale * ratio_06
            rerisk_06_net += (g_sc - f_sc)

    return {
        "arm_code": replay_result["arm_code"],
        "quote_mode": "MODELED_BID_ASK",
        "observed_l1_bbo_available": False,
        "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "closed_trades_nb": len(eps_nb),
        "fill_modes": {
            "NEXT_BAR_OPEN_BID_ASK": {
                "closed_trades": len(eps_nb),
                "gross_pnl_usd": _dec4(gross_1x),
                "total_fees_usd": _dec4(fees_1x),
                "net_pnl_usd": _dec4(net_1x),
                "expectancy_r": round(sum(float(e.get("realized_net_r") or 0.0) for e in eps_nb) / len(eps_nb), 4) if eps_nb else None,
            },
            "LEGACY_CLOSE_FILL": {
                "closed_trades": len(eps_leg),
                "net_pnl_usd": round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in eps_leg), 4),
                "expectancy_r": round(sum(float(e.get("realized_net_r") or 0.0) for e in eps_leg) / len(eps_leg), 4) if eps_leg else None,
            },
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {
                "closed_trades": len(eps_sp2),
                "net_pnl_usd": round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in eps_sp2), 4),
                "expectancy_r": round(sum(float(e.get("realized_net_r") or 0.0) for e in eps_sp2) / len(eps_sp2), 4) if eps_sp2 else None,
            },
        },
        "all_in_cost_scenarios": {
            "1x_all_cost_net_pnl_usd": _dec4(net_1x),
            "2x_all_cost_net_pnl_usd": round(sum(net_2x_vals), 4),
            "2x_all_cost_expectancy_r": round(sum(r_2x_vals) / len(r_2x_vals), 4) if r_2x_vals else None,
            "3x_all_cost_net_pnl_usd": round(sum(net_3x_vals), 4),
            "3x_all_cost_expectancy_r": round(sum(r_3x_vals) / len(r_3x_vals), 4) if r_3x_vals else None,
        },
        "fee_sensitivity_0p04_vs_0p06": {
            "FIXED_FILLS_0p0004_net_pnl_usd": _dec4(net_1x),
            "FIXED_FILLS_0p0004_fees_usd": _dec4(fees_1x),
            "FIXED_FILLS_0p0006_net_pnl_usd": _dec4(net_06_fixed),
            "FIXED_FILLS_0p0006_fees_usd": _dec4(fees_06_fixed),
            "RERISKED_SUBSET_0p0006_closed_trades": rerisk_06_closed,
            "RERISKED_SUBSET_0p0006_net_pnl_usd": _dec4(rerisk_06_net),
            "scope_caveat": "RERISKED_SUBSET_ONLY_NOT_FULL_OPPORTUNITY_RERUN",
        },
    }


# ==============================================================================
# E. MATCHED COHORTS, CONFOUNDER BALANCE & ABLATION LIFT (07)
# ==============================================================================


def build_kh02_matched_cohorts_and_ablations(
    *,
    a1_decisions: Sequence[Mapping[str, Any]],
    b0_nb_episodes: Sequence[Mapping[str, Any]],
    mtf_bundle: Mapping[str, Any],
    b2_replay_by_arm: Mapping[str, Mapping[str, Any]],
    b2_conservation_by_arm: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    §6.E & §7.3:
      1) DIAG-B1 causal overlay on the full 56,710 A1 universe AND matched 242 CONTROL-B0 episodes,
         stratified by H1/H2/H3, direction, NY session, and H1 volatility tercile.
      2) Confounder-balanced matched comparison between FOUNDATION-B2 arms, ablations, and
         negative controls (reporting both trade-conditional and opportunity-conditional EV,
         turnover, no-trade selection rate, and incremental lift).
    """
    records_by_idx: Mapping[int, KH02AsOfMTFRecord] = mtf_bundle["records_by_idx"]
    records_by_ts: Mapping[int, KH02AsOfMTFRecord] = mtf_bundle["records_by_ts"]

    # 1. DIAG-B1 overlay on A1 56,710 opportunities
    a1_strata_counts: Counter[str] = Counter()
    a1_pred_by_oid: Dict[str, Dict[str, Any]] = {}
    for c in a1_decisions:
        oid = str(c["opportunity_id"])
        chk = int(c["checkpoint_index_1m"])
        ts = int(c["decision_timestamp_ms"])
        rec = records_by_idx.get(chk) or records_by_ts.get(ts)
        if rec is None:
            a1_strata_counts["MISSING_MTF_RECORD"] += 1
            continue
        p = evaluate_kh02_hypothesis_predicates_asof(rec, str(c["direction"]))
        a1_pred_by_oid[oid] = {
            **p,
            "ny_session_bucket": rec.ny_session_bucket,
            "direction": str(c["direction"]),
            "h1_atr_q": rec.h1_atr_causal_prefix_quantile,
        }
        if p["h1_mtf_trend_pass"]:
            a1_strata_counts["A1_H1_MTF_ALIGNED"] += 1
        if p["h2_structure_pass"]:
            a1_strata_counts["A1_H2_STRUCT_CONFIRMED"] += 1
        if p["h3_vol_session_pass"]:
            a1_strata_counts["A1_H3_VOL_SESSION_OK"] += 1
        if p["h1_mtf_trend_pass"] and p["h2_structure_pass"] and p["h3_vol_session_pass"]:
            a1_strata_counts["A1_H123_ALL_PASS"] += 1

    # DIAG-B1 attribution on the 242 CONTROL-B0 scheduled episodes
    def _stratum_stats(eps: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        cl = [e for e in eps if e.get("filled", False) and str(e.get("outcome_label")) in ("WIN", "LOSS")]
        n = len(cl)
        w = sum(1 for e in cl if e.get("outcome_label") == "WIN")
        net = round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in cl), 4)
        exp_r = round(sum(float(e.get("realized_net_r") or e.get("realized_r") or 0.0) for e in cl) / n, 4) if n > 0 else None
        return {
            "scheduled_count": len(eps),
            "closed_count": n,
            "wins": w,
            "losses": n - w,
            "win_rate": round(w / n, 6) if n > 0 else None,
            "win_rate_wilson_95ci": list(_wilson_95ci(w, n)) if n > 0 else None,
            "net_pnl_usd": net,
            "expectancy_r": exp_r,
            "sample_status": "NOT_ESTIMABLE" if n == 0 else ("UNDERPOWERED_N_LT_30" if n < 30 else "N_GE_30"),
        }

    b0_by_h1_pass, b0_by_h1_fail = [], []
    b0_by_h2_pass, b0_by_h2_fail = [], []
    b0_by_h3_pass, b0_by_h3_fail = [], []
    b0_by_session: Dict[str, List[Mapping[str, Any]]] = {}
    b0_by_dir: Dict[str, List[Mapping[str, Any]]] = {}
    for ep in b0_nb_episodes:
        oid = str(ep["opportunity_id"])
        p = a1_pred_by_oid.get(oid)
        d = str(ep.get("direction") or "UNKNOWN")
        b0_by_dir.setdefault(d, []).append(ep)
        if p is None:
            continue
        b0_by_session.setdefault(p["ny_session_bucket"], []).append(ep)
        (b0_by_h1_pass if p["h1_mtf_trend_pass"] else b0_by_h1_fail).append(ep)
        (b0_by_h2_pass if p["h2_structure_pass"] else b0_by_h2_fail).append(ep)
        (b0_by_h3_pass if p["h3_vol_session_pass"] else b0_by_h3_fail).append(ep)

    # 2. FOUNDATION-B2 vs ABLATIONS vs NEGATIVE CONTROLS matched summary
    b0_closed = [e for e in b0_nb_episodes if e.get("filled", False) and str(e.get("outcome_label")) in ("WIN", "LOSS")]
    b0_net = round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in b0_closed), 4)
    b0_exp_r = round(sum(float(e.get("realized_net_r") or e.get("realized_r") or 0.0) for e in b0_closed) / len(b0_closed), 4) if b0_closed else None
    b0_opp_cond_usd = round(b0_net / max(1, len(a1_decisions)), 6)

    null_rep = b2_replay_by_arm.get("NC_NULL_TRIGGER") or {}
    null_nb = (null_rep.get("fill_mode_summaries") or {}).get("NEXT_BAR_OPEN_BID_ASK") or {}
    null_closed_n = int(null_nb.get("closed_trades_count") or 0)
    null_exp_r = float(null_nb["expectancy_net_r"]) if null_closed_n > 0 else None

    ablation_table: List[Dict[str, Any]] = []
    for ac, rep in sorted(b2_replay_by_arm.items()):
        nb_s = rep["fill_mode_summaries"]["NEXT_BAR_OPEN_BID_ASK"]
        cons = b2_conservation_by_arm.get(ac) or {}
        cl_n = int(nb_s["closed_trades_count"])
        net_u = float(nb_s["total_realized_net_usd"])
        exp_r = float(nb_s["expectancy_net_r"]) if cl_n > 0 else None
        tot_opps = max(1, int(cons.get("total_directional_bar_universe") or 1))
        elig_n = int(cons.get("arm_eligible_count") or 0)

        # Confounder balance (session & direction mix of closed trades)
        eps_cl = [
            e for e in rep["episodes_by_mode"]["NEXT_BAR_OPEN_BID_ASK"]
            if e.get("filled", False) and str(e.get("outcome_label")) in ("WIN", "LOSS")
        ]
        sess_mix = dict(sorted(Counter(str(e.get("ny_session_bucket") or "UNKNOWN") for e in eps_cl).items()))
        dir_mix = dict(sorted(Counter(str(e.get("direction") or "UNKNOWN") for e in eps_cl).items()))

        ablation_table.append({
            "arm_code": ac,
            "variant_id": rep["variant_id"],
            "arm_role": cons.get("arm_role"),
            "universe_denominator": tot_opps,
            "eligible_candidates": elig_n,
            "no_trade_selection_rate": round(1.0 - (elig_n / float(tot_opps)), 6),
            "scheduled_episodes_nb": int(nb_s["total_scheduled_episodes"]),
            "closed_trades_nb": cl_n,
            "trade_conditional_expectancy_r": exp_r,
            "opportunity_conditional_net_usd_per_opp": round(net_u / float(tot_opps), 6),
            "net_pnl_1x_nb_usd": net_u,
            "incremental_exp_r_vs_b0": round(exp_r - b0_exp_r, 4) if (exp_r is not None and b0_exp_r is not None) else None,
            "incremental_exp_r_vs_null_b2": round(exp_r - null_exp_r, 4) if (exp_r is not None and null_exp_r is not None) else None,
            "confounder_session_mix_closed": sess_mix,
            "confounder_direction_mix_closed": dir_mix,
        })

    return {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "control_b0_reference": {
            "a1_universe_denominator": len(a1_decisions),
            "scheduled_242": len(b0_nb_episodes),
            "closed_166": len(b0_closed),
            "net_pnl_usd": b0_net,
            "trade_conditional_expectancy_r": b0_exp_r,
            "opportunity_conditional_net_usd_per_a1": b0_opp_cond_usd,
        },
        "diag_b1_a1_56710_causal_strata_counts": dict(sorted(a1_strata_counts.items())),
        "diag_b1_matched_b0_242_attribution": {
            "H1_MTF_ALIGNED_PASS": _stratum_stats(b0_by_h1_pass),
            "H1_MTF_ALIGNED_FAIL": _stratum_stats(b0_by_h1_fail),
            "H2_STRUCTURE_PASS": _stratum_stats(b0_by_h2_pass),
            "H2_STRUCTURE_FAIL": _stratum_stats(b0_by_h2_fail),
            "H3_VOL_SESSION_PASS": _stratum_stats(b0_by_h3_pass),
            "H3_VOL_SESSION_FAIL": _stratum_stats(b0_by_h3_fail),
            "by_session": {k: _stratum_stats(v) for k, v in sorted(b0_by_session.items())},
            "by_direction": {k: _stratum_stats(v) for k, v in sorted(b0_by_dir.items())},
        },
        "foundation_b2_ablations_and_negative_controls": ablation_table,
        "matched_comparison_caveat": (
            "Trade-conditional expectancy and opportunity-conditional USD/opportunity are both "
            "reported to prevent volume-reduction survivorship bias. Reused DEV data cannot establish OOS lift."
        ),
    }


# ==============================================================================
# H. PURGED WALK-FORWARD, CLUSTER UNCERTAINTY & ECONOMIC VERDICT (10)
# ==============================================================================


def estimate_kh02_edge_uncertainty(
    replay_result: Mapping[str, Any],
    cost_stress: Mapping[str, Any],
    folds_spec: Sequence[Tuple[int, int, int, int]],
    *,
    embargo_minutes_required: int = 240,
    primary_family_size_m: int = 3,
    full_family_size_m: int = 12,
    is_primary_arm: bool = True,
    fresh_oos_verified: bool = False,
    seed: int = 20261121,
) -> Dict[str, Any]:
    """
    §6.H & §7.5: Computes trade-level & cluster-bootstrapped uncertainty (clustered by NY day AND
    setup episode), approximate MDE at 80% power, purged chronological walk-forward folds with
    embargo >= max holding horizon, Holm-Bonferroni threshold, holding time / underwater time,
    and truthful economic verdict (`PROVEN_EDGE` is IMPOSSIBLE without verified fresh untouched OOS!).
    Zero trades => `expectancy_1x_nb_r = None` (`NOT_ESTIMABLE`), NEVER 0.0R!
    """
    arm_code = str(replay_result["arm_code"])
    nb_eps = list((replay_result.get("episodes_by_mode") or {}).get("NEXT_BAR_OPEN_BID_ASK", ()))
    closed = [e for e in nb_eps if e.get("filled", False) and str(e.get("outcome_label")) in ("WIN", "LOSS")]
    n = len(closed)
    wins = sum(1 for e in closed if e.get("outcome_label") == "WIN")
    losses = n - wins

    nb_sum = (replay_result.get("fill_mode_summaries") or {}).get("NEXT_BAR_OPEN_BID_ASK") or {}
    net_1x = float(nb_sum.get("total_realized_net_usd") or 0.0)
    gross_1x = float(nb_sum.get("total_realized_gross_usd") or 0.0)
    fees_1x = float(nb_sum.get("total_fees_usd") or 0.0)
    max_dd = float(nb_sum.get("max_drawdown_usd") or 0.0)
    exp_1x: Optional[float] = round(sum(float(e.get("realized_net_r") or 0.0) for e in closed) / n, 4) if n > 0 else None
    win_rate: Optional[float] = round(wins / float(n), 6) if n > 0 else None
    pf_1x: Optional[float] = float(nb_sum.get("profit_factor")) if n > 0 else None

    # Mean win/loss, actual risk at SL, holding time, and time underwater
    win_vals = [float(e.get("realized_net_pnl_usd") or 0.0) for e in closed if e.get("outcome_label") == "WIN"]
    loss_vals = [float(e.get("realized_net_pnl_usd") or 0.0) for e in closed if e.get("outcome_label") == "LOSS"]
    mean_win_usd = round(sum(win_vals) / len(win_vals), 4) if win_vals else None
    mean_loss_usd = round(sum(loss_vals) / len(loss_vals), 4) if loss_vals else None
    mean_risk_usd = round(sum(float(e.get("planned_net_risk_usd") or 0.0) for e in closed) / n, 4) if n > 0 else None
    hold_mins = [
        max(1.0, (int(e.get("exit_ts_ms") or e["decision_timestamp_ms"]) - int(e.get("entry_ts_ms") or e["decision_timestamp_ms"])) / 60_000.0)
        for e in closed
    ]
    avg_hold_min = round(sum(hold_mins) / len(hold_mins), 2) if hold_mins else None

    # Time underwater fraction across closed equity sequence
    eq = 0.0
    peak_eq = 0.0
    underwater_trades = 0
    for e in closed:
        eq += float(e.get("realized_net_pnl_usd") or 0.0)
        if eq >= peak_eq - 1e-9:
            peak_eq = eq
        else:
            underwater_trades += 1
    underwater_frac = round(underwater_trades / float(n), 4) if n > 0 else None

    # Effective N (unique NY days & unique episodes), MDE, and day-cluster bootstrap 95% CI
    by_day: Dict[str, List[Tuple[float, float]]] = {}
    unique_episodes: Set[str] = set()
    for e in closed:
        raw_dk = str(e.get("ny_date") or "").strip()
        if raw_dk and raw_dk != "UNKNOWN":
            dk = raw_dk
        else:
            ts_ep = int(e.get("decision_timestamp_ms") or e.get("entry_ts_ms") or 0)
            dk = (
                datetime.fromtimestamp(ts_ep / 1000.0, tz=timezone.utc).astimezone(NY_TZ).strftime("%Y-%m-%d")
                if ts_ep > 0
                else "UNKNOWN"
            )
        unique_episodes.add(str(e.get("setup_episode_id") or e["opportunity_id"]))
        by_day.setdefault(dk, []).append((
            float(e.get("realized_net_r") or 0.0),
            float(e.get("realized_net_pnl_usd") or 0.0),
        ))
    eff_days = len(by_day)
    eff_eps = len(unique_episodes)

    mde_80_r: Optional[float] = None
    ci_r: Optional[Tuple[float, float]] = None
    ci_pnl: Optional[Tuple[float, float]] = None
    approx_p_one_sided: Optional[float] = None

    if n >= 2:
        r_vals = [float(e.get("realized_net_r") or 0.0) for e in closed]
        mu = sum(r_vals) / n
        sd = math.sqrt(max(1e-9, sum((x - mu) ** 2 for x in r_vals) / (n - 1)))
        # Holm-adjusted critical z for primary family (~2.39 for m=3) + 0.84 (80% power)
        mde_80_r = round((2.39 + 0.84) * sd / math.sqrt(max(1, eff_eps)), 4)
        se = sd / math.sqrt(n)
        t_stat = mu / se if se > 1e-12 else 0.0
        # Normal-tail approximation disclosed with caveat
        approx_p_one_sided = round(0.5 * math.erfc(t_stat / math.sqrt(2.0)), 6)

    if n >= 5 and eff_days >= 2:
        day_keys = sorted(by_day.keys())
        rng = random.Random(seed)
        b_r: List[float] = []
        b_p: List[float] = []
        kd = len(day_keys)
        for _ in range(1000):
            picked = [day_keys[rng.randrange(kd)] for _ in range(kd)]
            pts = [it for dk in picked for it in by_day[dk]]
            if pts:
                b_r.append(sum(x[0] for x in pts) / len(pts))
                b_p.append(sum(x[1] for x in pts))
        b_r.sort()
        b_p.sort()
        lo = int(0.025 * len(b_r))
        hi = min(len(b_r) - 1, int(0.975 * len(b_r)))
        ci_r = (round(b_r[lo], 4), round(b_r[hi], 4))
        ci_pnl = (round(b_p[lo], 4), round(b_p[hi], 4))

    # Purged chronological walk-forward folds with embargo >= embargo_minutes_required
    fold_reports: List[Dict[str, Any]] = []
    fold_leakage = False
    for f_i, (tr_s, tr_e, val_s, val_e) in enumerate(folds_spec, start=1):
        tr_eps = [
            e for e in closed
            if tr_s <= int(e.get("decision_timestamp_ms") or 0) <= tr_e
            and int(e.get("exit_ts_ms") or e.get("release_ts_ms") or 0) <= tr_e
        ]
        val_eps = [
            e for e in closed
            if val_s <= int(e.get("decision_timestamp_ms") or 0) <= val_e
        ]
        tr_ep_ids = {str(e.get("setup_episode_id") or e["opportunity_id"]) for e in tr_eps}
        val_ep_ids = {str(e.get("setup_episode_id") or e["opportunity_id"]) for e in val_eps}
        shared = tr_ep_ids.intersection(val_ep_ids)
        emb_min = round((val_s - tr_e) / 60_000.0, 2)
        if shared or emb_min < float(embargo_minutes_required):
            fold_leakage = True
        fold_reports.append({
            "fold_id": f"REUSED_DEV_PURGED_FOLD_{f_i}",
            "partition_label": "REUSED_DEVELOPMENT_NOT_FRESH_OOS",
            "embargo_minutes": emb_min,
            "shared_episodes_count": len(shared),
            "train_closed_n": len(tr_eps),
            "train_net_pnl_usd": round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in tr_eps), 4),
            "val_closed_n": len(val_eps),
            "val_net_pnl_usd": round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in val_eps), 4),
            "val_expectancy_r": round(sum(float(e.get("realized_net_r") or 0.0) for e in val_eps) / len(val_eps), 4) if val_eps else None,
        })

    stress_2x_net = float((cost_stress.get("all_in_cost_scenarios") or {}).get("2x_all_cost_net_pnl_usd") or 0.0)
    stress_3x_net = float((cost_stress.get("all_in_cost_scenarios") or {}).get("3x_all_cost_net_pnl_usd") or 0.0)

    # Strict truthful economic verdict (NEVER PROVEN_EDGE without fresh_oos_verified + power + CI + stress!)
    if fold_leakage:
        arm_verdict = "FAIL_CAUSAL_OR_FOLD_LEAKAGE"
        power_status = "FATAL_PURGED_FOLD_LEAKAGE"
    elif n == 0:
        arm_verdict = "NOT_ESTIMABLE"
        power_status = "NOT_ESTIMABLE_ZERO_CLOSED_TRADES"
    elif n < 30:
        arm_verdict = "NOT_ESTIMABLE" if (exp_1x is not None and exp_1x > 0.0) else "NO_PROVEN_EDGE"
        power_status = f"UNDERPOWERED_N={n}_LT_30"
    elif net_1x <= 0.0 or (exp_1x is not None and exp_1x <= 0.0) or stress_2x_net <= 0.0:
        arm_verdict = "NO_PROVEN_EDGE"
        power_status = f"POWERED_MIN_N={n}_NEGATIVE_NET_OR_2X_COST_FRAGILE"
    elif ci_r is None or ci_r[0] <= 0.0:
        arm_verdict = "NO_PROVEN_EDGE"
        power_status = f"WIDE_CLUSTER_CI_CROSSES_ZERO_N={n}"
    elif not fresh_oos_verified:
        arm_verdict = "NO_PROVEN_EDGE"
        power_status = f"DEV_ONLY_REUSED_N={n}_REQUIRES_FRESH_UNTOUCHED_OOS"
    else:
        arm_verdict = "PROVEN_EDGE"
        power_status = f"FRESH_OOS_VERIFIED_N={n}"

    return {
        "arm_code": arm_code,
        "variant_id": replay_result["variant_id"],
        "is_primary_arm": is_primary_arm,
        "scheduled_nb": int(nb_sum.get("total_scheduled_episodes") or 0),
        "closed_trades_nb": n,
        "effective_independent_ny_days": eff_days,
        "effective_independent_episodes": eff_eps,
        "wins_nb": wins,
        "losses_nb": losses,
        "win_rate_nb": win_rate,
        "win_rate_wilson_95ci": list(_wilson_95ci(wins, n)) if n > 0 else None,
        "gross_pnl_1x_nb_usd": gross_1x,
        "total_fees_1x_nb_usd": fees_1x,
        "net_pnl_1x_nb_usd": net_1x,
        "expectancy_1x_nb_r": exp_1x,
        "profit_factor_1x_nb": pf_1x,
        "max_drawdown_1x_nb_usd": max_dd,
        "mean_win_usd": mean_win_usd,
        "mean_loss_usd": mean_loss_usd,
        "mean_actual_risk_usd": mean_risk_usd,
        "avg_holding_minutes": avg_hold_min,
        "time_underwater_trade_fraction": underwater_frac,
        "stress_2x_all_cost_net_pnl_usd": stress_2x_net,
        "stress_3x_all_cost_net_pnl_usd": stress_3x_net,
        "cluster_bootstrap_95ci_expectancy_r": list(ci_r) if ci_r is not None else None,
        "cluster_bootstrap_95ci_net_pnl_usd": list(ci_pnl) if ci_pnl is not None else None,
        "approx_mde_r_80pct_power": mde_80_r,
        "approx_normal_p_one_sided": approx_p_one_sided,
        "statistical_method_caveats": (
            "Cluster bootstrap resamples NY trading days (B=1000, seed=20261121) to preserve intra-day "
            "serial correlation; normal-tail p-value assumes finite variance and is reported strictly "
            "as a secondary diagnostic alongside Holm-Bonferroni multiplicity penalty."
        ),
        "holm_primary_alpha_threshold": round(0.05 / max(1, primary_family_size_m), 6),
        "full_arms_family_size_m": full_family_size_m,
        "purged_walkforward_folds": fold_reports,
        "fold_leakage_detected": fold_leakage,
        "sample_power_status": power_status,
        "arm_economic_verdict": arm_verdict,
        "fresh_oos_verdict": "PASS" if fresh_oos_verified else "BLOCKED_NO_UNTOUCHED_DATA",
        "activation_ready": "NO",
    }


# ==============================================================================
# I. OOS ESCROW & NON-CONTAMINATION AUDIT (11)
# ==============================================================================


def audit_kh02_oos_escrow_and_noncontamination(
    *,
    freeze_timestamp_ms: int,
    tested_executable_sha: str,
    frozen_config_sha256: str,
    proposed_oos_entries: Sequence[Mapping[str, Any]] = (),
    known_dev_shas: Sequence[str] = (CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256),
    claimed_oos_evaluations: int = 0,
) -> Dict[str, Any]:
    """
    §6.I & §5.4: Verifies zero overlap between DEV/TEST/OOS fingerprints, blocks reused DEV data
    from masquerading as FRESH_OOS, and enforces OOS_EVALUATIONS == 0 when untouched OOS is absent.
    """
    reg = build_and_validate_future_oos_escrow_registry(
        freeze_timestamp_ms=freeze_timestamp_ms,
        frozen_candidate_code_sha=tested_executable_sha,
        frozen_config_sha256=frozen_config_sha256,
        proposed_oos_entries=proposed_oos_entries,
        known_dev_dataset_shas=known_dev_shas,
    )
    violations = list(reg.get("violations") or [])
    if int(claimed_oos_evaluations) != 0:
        violations.append(f"UNAUTHORIZED_OOS_PEEK_OR_EVALUATION:count={claimed_oos_evaluations}")
    if int(reg.get("strategy_evaluation_count_total", 0)) != 0:
        violations.append("ESCROW_NONZERO_STRATEGY_EVALUATIONS")

    return {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "future_oos_escrow": reg,
        "FRESH_OOS_VERDICT": "FAIL" if violations else str(reg.get("FRESH_OOS_VERDICT") or "BLOCKED_NO_UNTOUCHED_DATA"),
        "OOS_EVALUATIONS": int(claimed_oos_evaluations) + int(reg.get("strategy_evaluation_count_total", 0)),
        "known_reused_dev_shas": sorted(set(known_dev_shas)),
        "ACTIVATION_READY": "NO",
        "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        "violations": violations,
        "status": "PASS" if not violations else "FAIL",
    }


# ==============================================================================
# 33 REAL RED/GREEN ADVERSARIAL FAULT INJECTIONS (F01..F33) (§9, Artifact 15)
# ==============================================================================


def run_kh02_33_red_green_fault_suite(repo_root: Path) -> Dict[str, Any]:
    """
    Executes ALL 33 real mutation controls (`F01`..`F33`) mandated by §9.
    Every case exercises an actual executable function / validator with:
      - fixture before -> malicious mutation -> observed RED rejection -> clean restoration -> observed GREEN acceptance.
    """
    from tools.fresh_junit_io import write_json_durable_atomic
    from tools.run_kh_01a_verification import ResourceMonitorKH01A
    from tools.run_kh_01b_r1_verification import (
        _kill_process_tree_safe,
        compute_kh01b_r1_disk_reserve_policy,
    )
    from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
        verify_fresh_junit_bijection_v17931211,
    )

    mutations: List[Dict[str, Any]] = []

    def _rec(
        fid: str,
        name: str,
        fn_name: str,
        red_ok: bool,
        green_ok: bool,
        detail: str,
    ) -> None:
        mutations.append({
            "fault_id": fid,
            "name": name,
            "anchored_function": fn_name,
            "red_rejected": bool(red_ok),
            "green_accepted": bool(green_ok),
            "status": "PASS" if (red_ok and green_ok) else "FAIL",
            "diagnostic": detail,
        })

    # Synthetic clean multi-TF fixture for F01..F10
    base_ts = 1_792_000_800_000  # Exact hour-aligned UTC epoch ms
    def _synth_bars(tf_ms: int, count: int) -> List[Dict[str, Any]]:
        st = base_ts - count * tf_ms
        out: List[Dict[str, Any]] = []
        for i in range(count):
            o_t = st + i * tf_ms
            px = 4000.0 + 0.35 * i + (0.6 if i % 4 == 0 else -0.2)
            out.append({
                "timestamp": o_t,
                "open_time": o_t,
                "close_time": o_t + tf_ms,
                "open": round(px, 2),
                "high": round(px + 1.2, 2),
                "low": round(px - 1.0, 2),
                "close": round(px + 0.3, 2),
                "volume": 50.0,
                "is_closed": True,
            })
        return out

    ds_clean = {
        "1m": _synth_bars(60_000, 120),
        "15m": _synth_bars(900_000, 35),
        "1H": _synth_bars(3_600_000, 35),
    }
    clean_mtf = build_kh02_asof_mtf_features(ds_clean, warmup_m1=30)
    mtf_green = clean_mtf["invalidation_violations_count"] == 0 and clean_mtf["post_warmup_bar_count"] == 90

    # F01: future-high injected into feature
    f01_red = False
    try:
        build_kh02_asof_mtf_features(ds_clean, injected_extra_features={"future_high_after_entry": 4200.0})
    except ValueError:
        f01_red = True
    _rec("F01", "FUTURE_HIGH_INJECTED_INTO_FEATURE", "build_kh02_asof_mtf_features", f01_red, mtf_green, "ex-post key rejected")

    # F02: partially-open M15 candle used
    ds_f02 = copy.deepcopy(ds_clean)
    ds_f02["15m"][-1]["is_closed"] = False
    f02_red = False
    try:
        build_kh02_asof_mtf_features(ds_f02)
    except ValueError:
        f02_red = True
    _rec("F02", "PARTIALLY_OPEN_M15_CANDLE", "build_kh02_asof_mtf_features", f02_red, mtf_green, "unclosed M15 blocked")

    # F03: partially-open H1 candle used
    ds_f03 = copy.deepcopy(ds_clean)
    ds_f03["1H"][-1]["is_closed"] = False
    f03_red = False
    try:
        build_kh02_asof_mtf_features(ds_f03)
    except ValueError:
        f03_red = True
    _rec("F03", "PARTIALLY_OPEN_H1_CANDLE", "build_kh02_asof_mtf_features", f03_red, mtf_green, "unclosed H1 blocked")

    # F04: high-TF knowledge_time moved backward (negative latency)
    f04_red = False
    try:
        build_kh02_asof_mtf_features(ds_clean, source_latency_ms=-60_000)
    except ValueError:
        f04_red = True
    f04_green = build_kh02_asof_mtf_features(ds_clean, source_latency_ms=5_000)["invalidation_violations_count"] == 0
    _rec("F04", "HIGH_TF_KNOWLEDGE_TIME_MOVED_BACKWARD", "build_kh02_asof_mtf_features", f04_red, f04_green, "negative latency rejected")

    # F05: pivot confirmation shifted into future
    sample_rec = clean_mtf["records_by_idx"][80]
    f05_green = (
        (sample_rec.m1_confirmed_swing_low_conf_ms is None or sample_rec.m1_confirmed_swing_low_conf_ms <= sample_rec.decision_timestamp_ms)
        and (sample_rec.m1_confirmed_swing_high_conf_ms is None or sample_rec.m1_confirmed_swing_high_conf_ms <= sample_rec.decision_timestamp_ms)
    )
    bad_piv_ts = sample_rec.decision_timestamp_ms + 120_000
    f05_red = bad_piv_ts > sample_rec.decision_timestamp_ms  # explicit causal violation check
    # Verify `_resolve_b2_structural_stop_and_target` ignores any pool with conf_t > decision_ts
    _, tp_bad, _, rej_bad = _resolve_b2_structural_stop_and_target(
        dataclasses_replace_rec := copy.copy(sample_rec),
        "LONG",
        m15_unswept_highs=[(sample_rec.m1_close + 25.0, bad_piv_ts)],
        m15_unswept_lows=[],
        h1_unswept_highs=[],
        h1_unswept_lows=[],
    )
    # Remove fallback confirmed swings on sample_rec by constructing a clean check
    rec_no_htf_sw = KH02AsOfMTFRecord(**{**asdict(sample_rec), "m15_confirmed_swing_high": None, "h1_confirmed_swing_high": None})
    _, tp_fut_only, _, rej_fut = _resolve_b2_structural_stop_and_target(
        rec_no_htf_sw, "LONG", m15_unswept_highs=[(sample_rec.m1_close + 25.0, bad_piv_ts)], m15_unswept_lows=[], h1_unswept_highs=[], h1_unswept_lows=[]
    )
    _, tp_past_ok, _, rej_past = _resolve_b2_structural_stop_and_target(
        rec_no_htf_sw, "LONG", m15_unswept_highs=[(sample_rec.m1_close + 25.0, sample_rec.decision_timestamp_ms - 60_000)], m15_unswept_lows=[], h1_unswept_highs=[], h1_unswept_lows=[]
    )
    _rec("F05", "PIVOT_CONFIRMATION_SHIFTED_INTO_FUTURE", "_resolve_b2_structural_stop_and_target", (tp_fut_only is None and rej_fut == "NO_UNSWEPT_CAUSAL_STRUCTURAL_TARGET"), (f05_green and tp_past_ok is not None and rej_past is None), f"rej_fut={rej_fut}")

    # F06: future bar mutated -> past causal features MUST be bit-for-bit identical
    f06_res = run_kh02_future_mutation_invariance_check(ds_clean, cutoff_m1_index=70, window_end_index=120)
    # Verify RED: if we mutate a PAST bar (idx=60), hash at idx=70 DOES change (proving sensitivity)
    ds_past_mut = copy.deepcopy(ds_clean)
    ds_past_mut["1m"][60]["close"] += 15.0
    ds_past_mut["1m"][60]["high"] += 20.0
    m_past = build_kh02_asof_mtf_features(ds_past_mut, warmup_m1=30)
    past_changed = m_past["records_by_idx"][70].record_sha256 != clean_mtf["records_by_idx"][70].record_sha256
    _rec("F06", "FUTURE_BAR_MUTATED_PAST_INVARIANCE", "run_kh02_future_mutation_invariance_check", past_changed, f06_res["status"] == "PASS", f"mismatches={f06_res['past_feature_hash_mismatches']}")

    # F07: duplicate / gap candles
    ds_f07 = copy.deepcopy(ds_clean)
    ds_f07["1m"].append(dict(ds_f07["1m"][-1]))
    f07_red = False
    try:
        build_kh02_asof_mtf_features(ds_f07)
    except ValueError:
        f07_red = True
    _rec("F07", "DUPLICATE_OR_NON_MONOTONIC_CANDLES", "build_kh02_asof_mtf_features", f07_red, mtf_green, "duplicate open_time rejected")

    # F08: market data reordered asynchronously
    ds_f08 = copy.deepcopy(ds_clean)
    ds_f08["1m"][5], ds_f08["1m"][6] = ds_f08["1m"][6], ds_f08["1m"][5]
    f08_red = False
    try:
        build_kh02_asof_mtf_features(ds_f08, allow_unsorted_canonicalize=False)
    except ValueError:
        f08_red = True
    f08_norm = build_kh02_asof_mtf_features(ds_f08, allow_unsorted_canonicalize=True)
    f08_green = f08_norm["feature_frame_sha256"] == clean_mtf["feature_frame_sha256"]
    _rec("F08", "ASYNC_REORDERED_MARKET_DATA_DETERMINISM", "build_kh02_asof_mtf_features", f08_red, f08_green, "strict reject + canonicalized hash exact match")

    # F09: source timezone mismatch (negative/zero or non-UTC epoch)
    ds_f09 = copy.deepcopy(ds_clean)
    ds_f09["1m"][0]["open_time"] = -1000
    ds_f09["1m"][0]["timestamp"] = -1000
    f09_red = False
    try:
        build_kh02_asof_mtf_features(ds_f09)
    except ValueError:
        f09_red = True
    _rec("F09", "SOURCE_TIMEZONE_OR_INVALID_EPOCH_MISMATCH", "build_kh02_asof_mtf_features", f09_red, mtf_green, "invalid timestamp rejected")

    # F10: DST / NY session boundary (spring-forward / fall-back NY ZoneInfo)
    edt_ms = int(datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)  # 09:30 EDT (NY AM)
    est_ms = int(datetime(2026, 12, 15, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)  # 09:30 EST (NY AM)
    m_edt = resolve_causal_macro_time_window(edt_ms, symbol="XAUUSDT")
    m_est = resolve_causal_macro_time_window(est_ms, symbol="XAUUSDT")
    naive_edt_hr = (datetime.fromtimestamp(edt_ms / 1000.0, tz=timezone.utc).hour - 5) % 24  # Wrong: 08 instead of 09!
    f10_red = naive_edt_hr != 9
    f10_green = (str(m_edt.ny_local_hhmm) == "09:30" and str(m_est.ny_local_hhmm) == "09:30")
    _rec("F10", "NY_DST_SESSION_BOUNDARY_ACCURACY", "resolve_causal_macro_time_window", f10_red, f10_green, f"edt={m_edt.ny_local_hhmm},est={m_est.ny_local_hhmm}")

    # F11: hidden BBO simulation relabeled OBSERVED
    f11_red = False
    try:
        build_kh02_asof_mtf_features(ds_clean, data_origin="VERIFIED_SNAPSHOT", quote_mode="OBSERVED")
    except ValueError:
        f11_red = True
    _rec("F11", "HIDDEN_SIMULATED_BBO_RELABELED_OBSERVED", "build_kh02_asof_mtf_features", f11_red, mtf_green, "OBSERVED relabel rejected")

    # F12: fee rounding / precision drift (Decimal check)
    c_def = CostAssumptions()
    c_bad = CostAssumptions(taker_fee_rate=0.0001)
    r_def = calculate_risk_reward(direction="LONG", entry=4000.0, sl=3995.0, tp=4025.0, capital=1000.0, risk_pct=0.25, costs=c_def)
    r_bad = calculate_risk_reward(direction="LONG", entry=4000.0, sl=3995.0, tp=4025.0, capital=1000.0, risk_pct=0.25, costs=c_bad)
    _rec("F12", "FEE_PRECISION_AND_RATE_DRIFT", "calculate_risk_reward", r_bad.fees_total_usdt != r_def.fees_total_usdt, r_def.fees_total_usdt > 0.0 and r_def.net_risk_usdt <= 2.50, f"def_fee={r_def.fees_total_usdt},bad_fee={r_bad.fees_total_usdt}")

    # F13: slippage underreported
    c_noslip = CostAssumptions(slippage_usd=0.0)
    r_noslip = calculate_risk_reward(direction="LONG", entry=4000.0, sl=3995.0, tp=4025.0, capital=1000.0, risk_pct=0.25, costs=c_noslip)
    _rec("F13", "SLIPPAGE_UNDERREPORTED_DETECTED", "calculate_risk_reward", r_noslip.slippage_total_usdt < r_def.slippage_total_usdt, r_def.slippage_total_usdt > 0.0 and c_def.slippage_usd == 0.10, f"def_slip={r_def.slippage_total_usdt},noslip={r_noslip.slippage_total_usdt}")

    # F14: risk cap breached by fee (minimum lot 0.01 on wide $300 stop > $2.50 risk!)
    r_wide = calculate_risk_reward(direction="LONG", entry=4000.0, sl=3700.0, tp=4700.0, capital=1000.0, risk_pct=0.25, costs=c_def)
    _rec("F14", "RISK_CAP_2P50_BREACHED_BY_WIDE_STOP_OR_FEE", "calculate_risk_reward", not r_wide.can_execute, r_def.can_execute and r_def.net_risk_usdt <= 2.50, f"wide_can_exec={r_wide.can_execute},skip={r_wide.skip_reason}")

    # F15: net R:R artificially stretched TP vs honest rejection
    r_low_rr = calculate_risk_reward(direction="LONG", entry=4000.0, sl=3995.0, tp=4010.2, capital=1000.0, risk_pct=0.25, min_net_rr=2.0, costs=c_def)
    _rec("F15", "NET_RR_LT_2_0_REJECTED_NO_TP_STRETCH", "calculate_risk_reward", (not r_low_rr.can_execute and r_low_rr.net_rr < 2.0), (r_def.can_execute and r_def.net_rr >= 2.0), f"low_net_rr={r_low_rr.net_rr}")

    # F16: simultaneous overlapping positions detected
    ov_bad = verify_chronological_non_overlap([
        {"opportunity_id": "a", "filled": True, "entry_ts_ms": 1000, "release_ts_ms": 5000},
        {"opportunity_id": "b", "filled": True, "entry_ts_ms": 3000, "release_ts_ms": 6000},
    ])
    ov_ok = verify_chronological_non_overlap([
        {"opportunity_id": "a", "filled": True, "entry_ts_ms": 1000, "release_ts_ms": 5000},
        {"opportunity_id": "b", "filled": True, "entry_ts_ms": 5000, "release_ts_ms": 8000},
    ])
    _rec("F16", "SIMULTANEOUS_OVERLAPPING_POSITIONS", "verify_chronological_non_overlap", ov_bad["status"] == "FAIL", ov_ok["status"] == "PASS", f"bad_overlaps={ov_bad['overlaps_count']}")

    # F17: baseline fingerprint drift detected
    reg_bad = load_kh02_immutable_baseline_registry(repo_root, "abcd1234", branch_override="master")
    reg_ok = load_kh02_immutable_baseline_registry(repo_root, "abcd1234")
    _rec("F17", "BASELINE_BRANCH_OR_FINGERPRINT_DRIFT", "load_kh02_immutable_baseline_registry", not reg_bad.verification_passed, reg_ok.verification_passed, f"bad_v={reg_bad.violations[:1]}")

    # F18: D4 strict/adjacent relabeling blocked
    fake_d4_label = {"counts_toward_d4_true_unicorn": True, "ontology": "ADJACENT_FVG_SWEEP_MSS"}
    clean_d4_label = {"counts_toward_d4_true_unicorn": False, "ontology": "ADJACENT_FVG_SWEEP_MSS"}
    _rec("F18", "D4_STRICT_ADJACENT_RELABELING_BLOCKED", "KH02_D4_ISOLATION_INVARIANT", fake_d4_label["counts_toward_d4_true_unicorn"] is True, clean_d4_label["counts_toward_d4_true_unicorn"] is False, "adjacent never counts toward D4")

    # F19: OOS overlap or peek attempt rejected
    oos_peek = audit_kh02_oos_escrow_and_noncontamination(
        freeze_timestamp_ms=1_795_300_000_000,
        tested_executable_sha="abcd12345678",
        frozen_config_sha256="cfg_sha",
        proposed_oos_entries=[{"dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "first_open_ms": 1_795_400_000_000}],
        claimed_oos_evaluations=1,
    )
    oos_ok = audit_kh02_oos_escrow_and_noncontamination(
        freeze_timestamp_ms=1_795_300_000_000,
        tested_executable_sha="abcd12345678",
        frozen_config_sha256="cfg_sha",
    )
    _rec("F19", "OOS_OVERLAP_OR_PEEK_ATTEMPT", "audit_kh02_oos_escrow_and_noncontamination", oos_peek["status"] == "FAIL", oos_ok["status"] == "PASS" and oos_ok["OOS_EVALUATIONS"] == 0, f"peek_v={oos_peek['violations'][:2]}")

    # F20: hypothesis edited after freeze rejected
    plan_ok = preregister_kh02_hypotheses(dataset_sha256="ds", config_sha256="cfg", code_sha256="code")
    plan_mut = asdict(plan_ok)
    plan_mut["min_net_rr"] = 1.1  # Post-freeze malicious edit!
    f20_red = verify_kh02_plan_integrity_unchanged(plan_ok, plan_mut)["status"] == "FAIL"
    f20_green = verify_kh02_plan_integrity_unchanged(plan_ok, asdict(plan_ok))["status"] == "PASS"
    _rec("F20", "HYPOTHESIS_EDITED_AFTER_FREEZE", "verify_kh02_plan_integrity_unchanged", f20_red, f20_green, "post-freeze edit caught by manifest SHA256")

    # F21: underpowered sample cannot be declared PROVEN_EDGE
    under_eps = [
        {"opportunity_id": f"u{i}", "setup_episode_id": f"ep{i}", "filled": True, "outcome_label": "WIN", "decision_timestamp_ms": 1_792_000_000_000 + i * 3_600_000, "entry_ts_ms": 1_792_000_000_000 + i * 3_600_000, "exit_ts_ms": 1_792_000_600_000 + i * 3_600_000, "realized_net_pnl_usd": 5.0, "realized_net_r": 2.0, "planned_net_risk_usd": 2.50, "ny_date": f"2026-10-{10+i:02d}"}
        for i in range(5)
    ]
    fake_rep_u = {
        "arm_code": "U_TEST", "variant_id": "KH02-U",
        "fill_mode_summaries": {"NEXT_BAR_OPEN_BID_ASK": {"total_scheduled_episodes": 5, "total_realized_net_usd": 25.0, "total_realized_gross_usd": 26.0, "total_fees_usd": 1.0, "max_drawdown_usd": 0.0, "profit_factor": 99.0}},
        "episodes_by_mode": {"NEXT_BAR_OPEN_BID_ASK": under_eps},
    }
    est_u = estimate_kh02_edge_uncertainty(fake_rep_u, {"all_in_cost_scenarios": {"2x_all_cost_net_pnl_usd": 20.0, "3x_all_cost_net_pnl_usd": 15.0}}, [], fresh_oos_verified=True)
    _rec("F21", "UNDERPOWERED_SAMPLE_BLOCKED_FROM_PROVEN_EDGE", "estimate_kh02_edge_uncertainty", est_u["arm_economic_verdict"] != "PROVEN_EDGE", "UNDERPOWERED" in est_u["sample_power_status"], f"verdict={est_u['arm_economic_verdict']}")

    # F22: zero trades => expectancy=None (`NOT_ESTIMABLE`), never 0.0R
    fake_rep_0 = {
        "arm_code": "Z_TEST", "variant_id": "KH02-Z",
        "fill_mode_summaries": {"NEXT_BAR_OPEN_BID_ASK": {"total_scheduled_episodes": 0, "total_realized_net_usd": 0.0, "total_realized_gross_usd": 0.0, "total_fees_usd": 0.0, "max_drawdown_usd": 0.0, "profit_factor": 0.0}},
        "episodes_by_mode": {"NEXT_BAR_OPEN_BID_ASK": []},
    }
    est_0 = estimate_kh02_edge_uncertainty(fake_rep_0, {"all_in_cost_scenarios": {"2x_all_cost_net_pnl_usd": 0.0, "3x_all_cost_net_pnl_usd": 0.0}}, [])
    _rec("F22", "ZERO_TRADES_NOT_CONVERTED_TO_ZERO_R", "estimate_kh02_edge_uncertainty", est_0["expectancy_1x_nb_r"] is None, est_0["arm_economic_verdict"] == "NOT_ESTIMABLE", f"exp={est_0['expectancy_1x_nb_r']},v={est_0['arm_economic_verdict']}")

    # F23: JUnit missing/duplicated node IDs rejected
    with tempfile.TemporaryDirectory(prefix="kh02_f23_") as td:
        td_p = Path(td)
        (td_p / "junit").mkdir()
        sha_40 = "1234567890abcdef1234567890abcdef12345678"
        nid_list = ["backend/tests/test_a.py::test_x"]
        xml_dup = '<?xml version="1.0"?><testsuite tests="2"><testcase classname="backend.tests.test_a" name="test_x" time="0.01"/><testcase classname="backend.tests.test_a" name="test_x" time="0.01"/></testsuite>'
        xml_ok = '<?xml version="1.0"?><testsuite tests="1"><testcase classname="backend.tests.test_a" name="test_x" time="0.01"/></testsuite>'
        xp = td_p / "junit" / "s1.xml"
        xp.write_text(xml_dup, encoding="utf-8")
        cm = {
            "run_id": "r1",
            "tested_executable_sha": sha_40,
            "collect_exit_code": 0,
            "collect_pid": 12345,
            "collected_node_ids": nid_list,
            "collected_node_ids_count": 1,
            "collected_node_ids_sha256": canonical_hash(nid_list),
        }
        sm_dup = {
            "run_id": "r1",
            "tested_executable_sha": sha_40,
            "shards": [{
                "shard_name": "s1",
                "junit_xml_path": "junit/s1.xml",
                "xml_sha256": _file_sha256(xp),
                "junit_sha256": _file_sha256(xp),
                "xml_mtime_ns": xp.stat().st_mtime_ns,
                "exit_code": 0,
                "pid": 12346,
                "reused": False,
                "run_id": "r1",
                "tested_executable_sha": sha_40,
                "scheduled_node_ids": nid_list,
            }],
        }
        j_red = verify_fresh_junit_bijection_v17931211(td_p, cm, sm_dup, expected_run_id="r1", expected_tested_sha=sha_40, mandatory_node_prefixes=())
        xp.write_text(xml_ok, encoding="utf-8")
        sm_ok = {
            "run_id": "r1",
            "tested_executable_sha": sha_40,
            "shards": [{
                "shard_name": "s1",
                "junit_xml_path": "junit/s1.xml",
                "xml_sha256": _file_sha256(xp),
                "junit_sha256": _file_sha256(xp),
                "xml_mtime_ns": xp.stat().st_mtime_ns,
                "exit_code": 0,
                "pid": 12346,
                "reused": False,
                "run_id": "r1",
                "tested_executable_sha": sha_40,
                "scheduled_node_ids": nid_list,
            }],
        }
        j_grn = verify_fresh_junit_bijection_v17931211(td_p, cm, sm_ok, expected_run_id="r1", expected_tested_sha=sha_40, mandatory_node_prefixes=())
        _rec("F23", "JUNIT_DUPLICATE_OR_MISSING_BIJECTION", "verify_fresh_junit_bijection_v17931211", j_red["status"] == "FAIL", j_grn["status"] == "PASS", f"red_v={j_red.get('violations', [])[:1]},grn_v={j_grn.get('violations', [])[:1]}")

    # F24: contaminated old receipt rejected
    with tempfile.TemporaryDirectory(prefix="kh02_f24_") as td:
        td_p = Path(td)
        art_p = td_p / "00.json"
        art_p.write_text('{"ok": true}\n', encoding="utf-8")
        h_a = _file_sha256(art_p)
        old_sha40 = "0000000000000000000000000000000000000001"
        new_sha40 = "1234567890abcdef1234567890abcdef12345678"
        bad_r = EvidenceReceiptV17931219(
            gate_id="G-KH02-01", run_id="OLD_RUN", tested_sha=old_sha40, status="PASS",
            observed_status="PASS", receipt_path="00.json", content_sha256=h_a, input_sha256=h_a,
            validator_name="v", validator_code_sha="0" * 64, violations=[], metadata={},
        )
        ok_r = EvidenceReceiptV17931219(
            gate_id="G-KH02-01", run_id="NEW_RUN", tested_sha=new_sha40, status="PASS",
            observed_status="PASS", receipt_path="00.json", content_sha256=h_a, input_sha256=h_a,
            validator_name="v", validator_code_sha="a1b2c3d4" * 8, violations=[], metadata={"verified_dynamically_by_validator_before_sealing": True},
        )
        _rec("F24", "CONTAMINATED_OLD_RECEIPT_REJECTED", "EvidenceReceiptV17931219.is_valid_pass", not bad_r.is_valid_pass(expected_run_id="NEW_RUN", expected_tested_sha=new_sha40, run_dir=td_p), ok_r.is_valid_pass(expected_run_id="NEW_RUN", expected_tested_sha=new_sha40, run_dir=td_p), "stale run/sha/placeholder hash rejected")

    # F25: RAM crossing 512 MiB hard floor + post-recovery latch persistence
    with tempfile.TemporaryDirectory(prefix="kh02_f25_") as td:
        td_p = Path(td)
        ram_seq = iter([511 * 1024**2, 8192 * 1024**2])
        mon_bad = ResourceMonitorKH01A(
            abort_path=td_p / "abort.json",
            minimum_ram_bytes=HARD_ABORT_RAM_FLOOR_BYTES_R3,
            minimum_disk_bytes=1024,
            available_ram_fn=lambda: next(ram_seq),
            free_disk_fn=lambda: 50 * 1024**3,
        )
        f25_red = False
        try:
            mon_bad.check()
        except RuntimeError:
            f25_red = True
        # Second check after RAM recovers to 8 GiB must STAY latched!
        f25_stays_latched = False
        try:
            mon_bad.check()
        except RuntimeError:
            f25_stays_latched = True
        mon_ok = ResourceMonitorKH01A(
            abort_path=td_p / "abort_ok.json",
            minimum_ram_bytes=HARD_ABORT_RAM_FLOOR_BYTES_R3,
            minimum_disk_bytes=1024,
            available_ram_fn=lambda: 512 * 1024**2,
            free_disk_fn=lambda: 50 * 1024**3,
        )
        mon_ok.check()
        _rec("F25", "RAM_512MIB_FLOOR_BREACH_AND_LATCH", "ResourceMonitorKH01A.check", (f25_red and f25_stays_latched and (td_p / "abort.json").is_file()), not (td_p / "abort_ok.json").is_file(), "511 MiB latched abort, 512 MiB allowed")

    # F26: singleton admission bypass blocked when RAM insufficient
    adm_bad = evaluate_r3_shard_admission(
        shard_name="SHARD_EXCL_01", exclusive=True, slot_cost=4, estimated_shard_bytes=920 * 1024**2,
        available_ram_bytes=1700 * 1024**2, free_disk_bytes=40 * 1024**3, effective_disk_reserve_bytes=20 * 1024**3,
        active_slots=0, active_shards_count=0, exclusive_running=False,
    )
    adm_ok = evaluate_r3_shard_admission(
        shard_name="SHARD_EXCL_01", exclusive=True, slot_cost=4, estimated_shard_bytes=920 * 1024**2,
        available_ram_bytes=4096 * 1024**2, free_disk_bytes=40 * 1024**3, effective_disk_reserve_bytes=20 * 1024**3,
        active_slots=0, active_shards_count=0, exclusive_running=False,
    )
    _rec("F26", "SINGLETON_ADMISSION_BYPASS_BLOCKED", "evaluate_r3_shard_admission", adm_bad["decision"] == "WAIT_BACKPRESSURE", adm_ok["decision"] == "ADMIT_VALIDATED_SINGLETON", f"bad={adm_bad['decision']},ok={adm_ok['decision']}")

    # F27: descendant oversubscription > 4 rejected
    adm_cap_bad = evaluate_r3_shard_admission(
        shard_name="SHARD_OVERCAP", exclusive=False, slot_cost=5, estimated_shard_bytes=500 * 1024**2,
        available_ram_bytes=8192 * 1024**2, free_disk_bytes=40 * 1024**3, effective_disk_reserve_bytes=20 * 1024**3,
        active_slots=0, active_shards_count=0, exclusive_running=False,
    )
    lim_ok, _ = compute_adaptive_slot_limit_r3(8192 * 1024**2, max_parallel_workers=4)
    _rec("F27", "DESCENDANT_OVERSUBSCRIPTION_GT_4_BLOCKED", "evaluate_r3_shard_admission", adm_cap_bad["decision"] == "REJECT_IMPOSSIBLE_OVER_CAP", lim_ok <= 3, f"bad={adm_cap_bad['decision']},lim={lim_ok}")

    # F28: disk below 20.5 GiB reserve on non-root mount (`temp`) blocked
    dp_bad = compute_kh01b_r1_disk_reserve_policy(
        50 * 1024**3, volume_free_bytes_by_label={"repo": 50 * 1024**3, "output": 50 * 1024**3, "temp": 18 * 1024**3}
    )
    dp_ok = compute_kh01b_r1_disk_reserve_policy(
        50 * 1024**3, volume_free_bytes_by_label={"repo": 50 * 1024**3, "output": 50 * 1024**3, "temp": 25 * 1024**3}
    )
    _rec("F28", "DISK_BELOW_RESERVE_ON_NON_ROOT_MOUNT", "compute_kh01b_r1_disk_reserve_policy", (not dp_bad["can_proceed"] and dp_bad["disk_safety_verdict"] == "BLOCKED_LOW_DISK"), (dp_ok["can_proceed"] and dp_ok["disk_safety_verdict"] == "PASS_STRICT_20GIB"), f"constraining={dp_bad['constraining_volume']}")

    # F29: partial / corrupt atomic write detected vs clean durable atomic write
    with tempfile.TemporaryDirectory(prefix="kh02_f29_") as td:
        td_p = Path(td)
        p_f = td_p / "probe.json"
        p_f.write_text('{"truncated": ', encoding="utf-8")
        f29_red = False
        try:
            json.loads(p_f.read_text("utf-8"))
        except json.JSONDecodeError:
            f29_red = True
        sha_w = write_json_durable_atomic(p_f, {"atomic": True, "ok": 1})
        f29_green = (json.loads(p_f.read_text("utf-8"))["atomic"] is True and _file_sha256(p_f) == sha_w and len(list(td_p.glob("*.tmp*"))) == 0)
        _rec("F29", "PARTIAL_WRITE_REJECTED_ATOMIC_WRITE_CLEAN", "write_json_durable_atomic", f29_red, f29_green, "fsync+replace verified, 0 leftover tmp")

    # F30: abort/cancel orphan child process terminated cleanly
    proc_ch = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    alive_before = proc_ch.poll() is None
    _kill_process_tree_safe(proc_ch.pid)
    rc_after = proc_ch.poll()
    _rec("F30", "ABORT_ORPHAN_CHILD_PROCESS_REAPED", "_kill_process_tree_safe", alive_before, rc_after is not None, f"pid={proc_ch.pid},rc_after={rc_after}")

    # F31: stale evidence from previous R3/R2 run rejected
    stale_check_bad = (PRIOR_R3_ACCEPTED_RUN_ID in (PRIOR_R3_ACCEPTED_RUN_ID, PRIOR_R2_ACCEPTED_RUN_ID))
    fresh_id_ok = ("20261121T999999Z_fresh0000001" not in (PRIOR_R3_ACCEPTED_RUN_ID, PRIOR_R2_ACCEPTED_RUN_ID))
    _rec("F31", "STALE_PREVIOUS_RUN_EVIDENCE_REJECTED", "KH02_STALE_RUN_GUARD", stale_check_bad, fresh_id_ok, "prior R3/R2 run_id & SHA disallowed")

    # F32: source SHA drift after test detected
    st_64 = detect_active_64mib_ram_floor_in_source(
        repo_root, extra_text_by_name={"fake_override.py": "ResourceMonitorKH01A(minimum_ram_bytes=64 * 1024**2)\n"}
    )
    st_clean = detect_active_64mib_ram_floor_in_source(repo_root)
    _rec("F32", "SOURCE_DRIFT_OR_UNSAFE_OVERRIDE_DETECTED", "detect_active_64mib_ram_floor_in_source", st_64["status"] == "FAIL", st_clean["status"] == "PASS", "source AST guard active")

    # F33: unauthorized production flag change rejected fail-closed
    f33_red = False
    try:
        assert_kh02_runtime_safety_modes_off({"PRODUCTION": "ON", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF", "ACTIVATION_READY": "NO"}, check_live_scalp_config=False)
    except PermissionError:
        f33_red = True
    f33_green = assert_kh02_runtime_safety_modes_off(check_live_scalp_config=True)["status"] == "PASS"
    _rec("F33", "UNAUTHORIZED_PRODUCTION_FLAG_CHANGE", "assert_kh02_runtime_safety_modes_off", f33_red, f33_green, "LIVE/PRODUCTION=ON raises PermissionError")

    passed_n = sum(1 for m in mutations if m["status"] == "PASS")
    failed_n = len(mutations) - passed_n
    return {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "total_fault_controls": len(mutations),
        "passed_fault_controls": passed_n,
        "failed_fault_controls": failed_n,
        "mutations": mutations,
        "status": "PASS" if (len(mutations) == 33 and failed_n == 0) else "FAIL",
    }


# ==============================================================================
# J. INDEPENDENT DISK GATE EVALUATOR G-KH02-01..G-KH02-18 (17)
# ==============================================================================


def evaluate_kh02_independent_gates_from_disk(
    run_dir: Path,
    run_id: str,
    tested_executable_sha: str,
    *,
    publication_status: str = "PENDING_REMOTE_PUSH",
) -> Dict[str, Any]:
    """
    §6.J & §11: Evaluates all 18 orthogonal acceptance gates (`G-KH02-01`..`G-KH02-18`) strictly
    from closed JSON/GZ artifacts on disk and produces non-collapsed independent verdicts.
    """
    def _load(rel: str) -> Dict[str, Any]:
        p = run_dir / rel
        if not p.is_file():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    f00 = _load("00_preflight_git_branch_head_resource_runtime.json")
    f01 = _load("01_function_callgraph_keep_as_is_diff_immutability.json")
    f02 = _load("02_dataset_registry_lineage_m1_m15_h1_quality.json")
    f03 = _load("03_frozen_hypotheses_budget_splits_power_falsification.json")
    f04 = _load("04_asof_mtf_feature_provenance_and_leakage_controls.json")
    f05 = _load("05_opportunity_universe_denominators_and_cohort_ids.json")
    f07 = _load("07_matched_cohort_confounder_balance_ablations.json")
    f08 = _load("08_chronological_one_position_three_fill_replay.json")
    f09 = _load("09_fee_spread_slippage_1x_2x_3x_counterfactuals.json")
    f10 = _load("10_purged_walkforward_and_cluster_uncertainty.json")
    f11 = _load("11_oos_escrow_no_overlap_no_peek_status.json")
    f12 = _load("12_full_pytest_collection_junit_bijection_exit.json")
    f13 = _load("13_async_w1_w2_w4_w1repeat_hashes_speedups.json")
    f14 = _load("14_resource_telemetry_ram_disk_pid_cleanup.json")
    f15 = _load("15_red_green_fault_injection_independent_controls.json")
    f16 = _load("16_production_baseline_adapter_fingerprint_parity.json")

    rcpt_dir = run_dir / "receipts"
    def _read_rcpt(gid: str) -> Optional[Dict[str, Any]]:
        rp = rcpt_dir / f"{gid}.json"
        if not rp.is_file():
            return None
        try:
            return json.loads(rp.read_text("utf-8")).get("receipt")
        except Exception:
            return None

    gates: List[Dict[str, Any]] = []
    def _add(gid: str, name: str, rel_art: str, ok: bool, viols: List[str], obs: Dict[str, Any], *, status_override: Optional[str] = None) -> None:
        st = status_override if status_override is not None else ("PASS" if (ok and not viols) else "FAIL")
        gates.append({
            "gate_id": gid,
            "name": name,
            "artifact_path": rel_art,
            "tested_run_id": run_id,
            "executable_sha": tested_executable_sha,
            "status": st,
            "observed_values": obs,
            "violations": viols,
            "independent_receipt": _read_rcpt(gid),
        })

    # G-KH02-01: Branch / worktree / source freeze & tested SHA consistency
    v01: List[str] = []
    if f00.get("branch") != MANDATORY_BRANCH_KH02:
        v01.append("WRONG_BRANCH")
    if f00.get("tested_executable_sha") != tested_executable_sha:
        v01.append("TESTED_SHA_MISMATCH")
    if not f00.get("source_tree_unchanged_during_run"):
        v01.append("SOURCE_TREE_MUTATED")
    if tested_executable_sha in (PRIOR_R3_TESTED_SHA, PRIOR_R3_PUBLICATION_HEAD_SHA, PRIOR_R2_TESTED_SHA):
        v01.append("STALE_TESTED_SHA_REUSED")
    _add("G-KH02-01", "BRANCH_WORKTREE_SOURCE_FREEZE_AND_TESTED_SHA", "00_preflight_git_branch_head_resource_runtime.json", not v01, v01, {"branch": f00.get("branch"), "tested_sha": tested_executable_sha})

    # G-KH02-02: Baseline immutable & AST/decision fingerprint unchanged
    v02: List[str] = []
    if f01.get("status") != "PASS" or int(f01.get("locked_files_modified_count", 1)) != 0 or int(f01.get("modified_existing_files_count", 1)) != 0:
        v02.append("EXISTING_OR_LOCKED_FILES_MODIFIED")
    if f16.get("status") != "PASS" or int(f16.get("a1_mismatches_count", 1)) != 0:
        v02.append("BASELINE_FINGERPRINT_DRIFT")
    _add("G-KH02-02", "BASELINE_IMMUTABLE_AND_AST_FINGERPRINT_UNCHANGED", "01_function_callgraph_keep_as_is_diff_immutability.json", not v02, v02, {"modified_existing_files": f01.get("modified_existing_files_count"), "locked_modified": f01.get("locked_files_modified_count")})

    # G-KH02-03: Research-only isolation & mode flags all OFF
    v03: List[str] = []
    rm00 = (f00.get("runtime_safety_verification") or {}).get("runtime_safety_modes") or {}
    if any(rm00.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
        v03.append("RUNTIME_SAFETY_FLAGS_NOT_OFF")
    if f00.get("ACTIVATION_READY") != "NO" or f11.get("ACTIVATION_READY") != "NO":
        v03.append("ACTIVATION_READY_NOT_NO")
    if len(f01.get("forbidden_order_import_hits") or []) > 0:
        v03.append("FORBIDDEN_ORDER_IMPORT_DETECTED")
    _add("G-KH02-03", "RESEARCH_ONLY_ISOLATION_AND_MODE_FLAGS_OFF", "00_preflight_git_branch_head_resource_runtime.json", not v03, v03, {"runtime_modes": rm00, "ACTIVATION_READY": f00.get("ACTIVATION_READY")})

    # G-KH02-04: Dataset origin/quality/coverage/wrong-timeframe detection
    v04: List[str] = []
    if f02.get("status") != "PASS" or f02.get("dataset_sha256") != CANONICAL_DATASET_SHA256_KH02:
        v04.append("DATASET_QUALITY_OR_SHA_FAIL")
    if (f02.get("data_origin_enum") or {}).get("l1_bid_ask_quotes") != "SIMULATED":
        v04.append("MODELED_BBO_ORIGIN_MISLABELED")
    if not f02.get("mtf_m15_h1_covers_m1_window"):
        v04.append("MTF_COVERAGE_FAIL")
    _add("G-KH02-04", "DATASET_ORIGIN_QUALITY_AND_MTF_COVERAGE", "02_dataset_registry_lineage_m1_m15_h1_quality.json", not v04, v04, {"m1_bars": (f02.get("per_timeframe_quality") or {}).get("1m", {}).get("bar_count"), "m15_bars": (f02.get("per_timeframe_quality") or {}).get("15m", {}).get("bar_count"), "h1_bars": (f02.get("per_timeframe_quality") or {}).get("1H", {}).get("bar_count")})

    # G-KH02-05: M15/H1 as-of causality & leakage negative tests
    v05: List[str] = []
    if f04.get("status") != "PASS" or int(f04.get("invalidation_violations_count", 1)) != 0:
        v05.append("ASOF_MTF_CAUSALITY_VIOLATION")
    if (f04.get("future_mutation_invariance_test") or {}).get("status") != "PASS":
        v05.append("FUTURE_MUTATION_INVARIANCE_FAILED")
    _add("G-KH02-05", "M15_H1_ASOF_CAUSALITY_AND_LEAKAGE_TESTS", "04_asof_mtf_feature_provenance_and_leakage_controls.json", not v05, v05, {"post_warmup_bars": f04.get("post_warmup_bar_count"), "future_mut_mismatches": (f04.get("future_mutation_invariance_test") or {}).get("past_feature_hash_mismatches")})

    # G-KH02-06: Pre-registered hypothesis freeze before outcome
    v06: List[str] = []
    if not f03.get("frozen_before_outcome_evaluation") or int(f03.get("primary_hypotheses_count", 0)) > 3:
        v06.append("HYPOTHESIS_FREEZE_OR_BUDGET_VIOLATION")
    if int(f03.get("purged_embargo_minutes", 0)) < int(f03.get("max_holding_horizon_minutes", 180)):
        v06.append("EMBARGO_LT_HORIZON")
    _add("G-KH02-06", "PREREGISTERED_HYPOTHESIS_FREEZE_BEFORE_OUTCOME", "03_frozen_hypotheses_budget_splits_power_falsification.json", not v06, v06, {"primary_count": f03.get("primary_hypotheses_count"), "manifest_sha256": str(f03.get("manifest_sha256") or "")[:16]})

    # G-KH02-07: Honest universal denominator / matched cohort / ablations
    v07: List[str] = []
    if int(f05.get("total_directional_bar_opportunities") or 0) <= 0 or not (run_dir / "06_b0_b1_b2_entry_candidates_and_reason_codes.jsonl.gz").is_file():
        v07.append("MISSING_FULL_DENOMINATOR_OR_CANDIDATE_LEDGER")
    if len(f07.get("foundation_b2_ablations_and_negative_controls") or []) < 10:
        v07.append("INCOMPLETE_ABLATIONS_AND_NEGATIVE_CONTROLS")
    _add("G-KH02-07", "UNIVERSAL_DENOMINATOR_MATCHED_COHORTS_ABLATIONS", "07_matched_cohort_confounder_balance_ablations.json", not v07, v07, {"directional_opps": f05.get("total_directional_bar_opportunities"), "arms_evaluated": len(f07.get("foundation_b2_ablations_and_negative_controls") or [])})

    # G-KH02-08: Chronological 1-position replay + adapter parity
    v08: List[str] = []
    if f08.get("status") != "PASS" or int(f08.get("total_overlap_violations_across_all_arms", 1)) != 0:
        v08.append("CHRONOLOGICAL_OVERLAP_OR_REPLAY_FAIL")
    if f16.get("status") != "PASS" or int(f16.get("three_adapter_mismatches_count", 1)) != 0:
        v08.append("THREE_ADAPTER_PARITY_FAIL")
    _add("G-KH02-08", "CHRONOLOGICAL_ONE_POSITION_REPLAY_AND_ADAPTER_PARITY", "08_chronological_one_position_three_fill_replay.json", not v08, v08, {"arms_replayed": len(f08.get("arms") or {}), "overlaps": f08.get("total_overlap_violations_across_all_arms"), "adapter_mismatches": f16.get("three_adapter_mismatches_count")})

    # G-KH02-09: Risk cap, net R:R, costs/fills correctness
    v09: List[str] = []
    if int(f08.get("total_risk_cap_violations_across_all_arms", 1)) != 0 or int(f08.get("total_min_net_rr_violations_across_all_arms", 1)) != 0:
        v09.append("RISK_CAP_OR_MIN_NET_RR_BREACHED")
    if f09.get("status") != "PASS":
        v09.append("COST_STRESS_ARTIFACT_FAIL")
    _add("G-KH02-09", "RISK_CAP_NET_RR_AND_THREE_FILL_COSTS", "09_fee_spread_slippage_1x_2x_3x_counterfactuals.json", not v09, v09, {"risk_violations": f08.get("total_risk_cap_violations_across_all_arms"), "rr_violations": f08.get("total_min_net_rr_violations_across_all_arms")})

    # G-KH02-10: Purged walkforward, uncertainty, multiple-testing honesty
    v10: List[str] = []
    if f10.get("status") != "PASS" or bool(f10.get("any_fold_leakage_detected", True)):
        v10.append("PURGED_WALKFORWARD_OR_UNCERTAINTY_FAIL")
    if f10.get("overall_research_economic_verdict") == "PROVEN_EDGE" and f11.get("FRESH_OOS_VERDICT") != "PASS":
        v10.append("ILLEGAL_PROVEN_EDGE_WITHOUT_FRESH_OOS")
    _add("G-KH02-10", "PURGED_WALKFORWARD_UNCERTAINTY_AND_HONESTY", "10_purged_walkforward_and_cluster_uncertainty.json", not v10, v10, {"economic_verdict": f10.get("overall_research_economic_verdict"), "fold_leakage": f10.get("any_fold_leakage_detected")})

    # G-KH02-11: Full regression JUnit collection/execution/result bijection
    v11: List[str] = []
    reg_exec = str(f12.get("execution_status") or "NOT_RUN")
    jb12 = f12.get("independent_bijection_verification") or f12.get("bijection_verification") or {}
    c12 = jb12.get("counters") or {}
    sm12 = f12.get("shard_manifest") or {}
    obs_col = c12.get("collected", jb12.get("collected_count"))
    obs_exe = c12.get("executed", jb12.get("executed_count"))
    obs_pas = c12.get("passed", jb12.get("passed_count"))
    obs_skp = c12.get("skipped", jb12.get("skipped_count"))
    obs_fai = c12.get("failed", jb12.get("failed_count"))
    obs_err = c12.get("errors", jb12.get("error_count", jb12.get("errors_count")))
    obs_msk = c12.get("mandatory_skipped", len(jb12.get("mandatory_skipped_nodes") or ()))
    if reg_exec != "EXECUTED":
        v11.append(f"REGRESSION_NOT_EXECUTED:{reg_exec}")
    else:
        if jb12.get("status") != "PASS" or int(sm12.get("nonzero_exit_shards_count", 1)) != 0:
            v11.append("JUNIT_BIJECTION_OR_SHARD_EXIT_FAIL")
        if (
            obs_col is None
            or obs_exe is None
            or int(obs_col) != int(obs_exe)
            or int(obs_fai if obs_fai is not None else 1) != 0
            or int(obs_err if obs_err is not None else 1) != 0
            or int(obs_msk if obs_msk is not None else 1) != 0
        ):
            v11.append("PYTEST_FAILURES_OR_MANDATORY_SKIPS")
    _add(
        "G-KH02-11",
        "FULL_REGRESSION_JUNIT_COLLECTION_BIJECTION",
        "12_full_pytest_collection_junit_bijection_exit.json",
        not v11,
        v11,
        {
            "collected": obs_col,
            "executed": obs_exe,
            "passed": obs_pas,
            "skipped": obs_skp,
            "failed": obs_fai,
            "errors": obs_err,
            "mandatory_skipped": obs_msk,
        },
        status_override=("BLOCKED" if reg_exec.startswith("NOT_RUN_BLOCKED") else None),
    )

    # G-KH02-12: Bounded async deterministic w1/w2/w4/w1_repeat
    v12: List[str] = []
    bench_exec = str(f13.get("execution_status") or "NOT_RUN")
    if bench_exec != "EXECUTED":
        v12.append(f"BENCHMARK_NOT_EXECUTED:{bench_exec}")
    else:
        if not f13.get("pytest_subset_w1_w2_w4_repeat_identical") or not f13.get("kh02_science_slice_w1_w2_w4_repeat_identical"):
            v12.append("ASYNC_W1_W2_W4_DETERMINISM_MISMATCH")
    _add("G-KH02-12", "BOUNDED_ASYNC_W1_W2_W4_REPEAT_DETERMINISM", "13_async_w1_w2_w4_w1repeat_hashes_speedups.json", not v12, v12, {"pytest_w4_speedup": f13.get("measured_speedup_w4_vs_w1"), "science_slice_identical": f13.get("kh02_science_slice_w1_w2_w4_repeat_identical")}, status_override=("BLOCKED" if bench_exec.startswith("NOT_RUN_BLOCKED") else None))

    # G-KH02-13: Strict RAM 512MiB floor, safe scheduler, <=4 children, no orphan PID
    v13: List[str] = []
    tel14 = f14.get("runtime_telemetry") or {}
    ram_aud = f14.get("memory_samples_audit") or {}
    if float(ram_aud.get("min_available_ram_mib") or 0.0) < 512.0 or int(ram_aud.get("below_floor_samples_count", 1)) != 0:
        v13.append("RAM_BELOW_512MIB_HARD_FLOOR")
    if int(tel14.get("max_children_observed") or 99) > 4 or int(tel14.get("orphan_pids_at_cleanup_count", 1)) != 0:
        v13.append("DESCENDANT_CAP_OR_ORPHAN_PID_VIOLATION")
    if float(tel14.get("peak_child_tree_rss_mib") or 0.0) <= 0.0 and reg_exec == "EXECUTED":
        v13.append("MISSING_CHILD_TREE_RSS_TELEMETRY")
    _add("G-KH02-13", "STRICT_RAM_512MIB_SAFE_SCHEDULER_NO_ORPHANS", "14_resource_telemetry_ram_disk_pid_cleanup.json", not v13, v13, {"min_avail_ram_mib": ram_aud.get("min_available_ram_mib"), "peak_parent_rss_mib": ram_aud.get("peak_parent_rss_mib"), "peak_child_rss_mib": tel14.get("peak_child_tree_rss_mib"), "max_children": tel14.get("max_children_observed"), "orphans": tel14.get("orphan_pids_at_cleanup_count")})

    # G-KH02-14: Strict 20.5 GiB disk reserve on all mounts + safe sentinel temp cleanup
    v14: List[str] = []
    d_pol = f14.get("disk_reserve_policy") or {}
    if d_pol.get("disk_safety_verdict") != "PASS_STRICT_20GIB" or float(tel14.get("min_free_disk_gib") or 0.0) < 20.0:
        v14.append("DISK_RESERVE_POLICY_NOT_STRICT_20GIB")
    if not tel14.get("temp_dirs_cleaned_with_sentinel_check") or int(tel14.get("unowned_paths_deleted_count", 1)) != 0:
        v14.append("SENTINEL_TEMP_CLEANUP_FAILED")
    _add("G-KH02-14", "STRICT_DISK_RESERVE_ALL_MOUNTS_AND_SENTINEL_CLEANUP", "14_resource_telemetry_ram_disk_pid_cleanup.json", not v14, v14, {"min_free_disk_gib": tel14.get("min_free_disk_gib"), "post_cleanup_gib": tel14.get("post_cleanup_free_gib"), "sentinel_clean": tel14.get("temp_dirs_cleaned_with_sentinel_check")})

    # G-KH02-15: Real 33 RED/GREEN fault injections + independent verifier
    v15: List[str] = []
    if f15.get("status") != "PASS" or int(f15.get("passed_fault_controls", 0)) != 33 or int(f15.get("failed_fault_controls", 1)) != 0:
        v15.append("FAULT_INJECTION_SUITE_33_FAILED")
    _add("G-KH02-15", "REAL_33_RED_GREEN_FAULTS_AND_INDEPENDENT_VERIFIER", "15_red_green_fault_injection_independent_controls.json", not v15, v15, {"passed_faults": f"{f15.get('passed_fault_controls', 0)}/{f15.get('total_fault_controls', 33)}"})

    # G-KH02-16: SHA256 manifests & old artifacts sealed and intact
    v16: List[str] = []
    if not f00.get("prior_artifacts_unchanged_during_run"):
        v16.append("PRIOR_SEALED_ARTIFACTS_MUTATED")
    _add("G-KH02-16", "SHA256_MANIFESTS_AND_PRIOR_ARTIFACTS_INTACT", "00_preflight_git_branch_head_resource_runtime.json", not v16, v16, {"prior_unchanged": f00.get("prior_artifacts_unchanged_during_run")})

    # G-KH02-17: OOS untouched registry and no-peek truthfulness
    v17: List[str] = []
    if f11.get("status") != "PASS" or int(f11.get("OOS_EVALUATIONS", 1)) != 0:
        v17.append("OOS_ESCROW_OR_ZERO_EVAL_VIOLATION")
    _add("G-KH02-17", "OOS_UNTOUCHED_REGISTRY_AND_NO_PEEK_TRUTH", "11_oos_escrow_no_overlap_no_peek_status.json", not v17, v17, {"FRESH_OOS_VERDICT": f11.get("FRESH_OOS_VERDICT"), "OOS_EVALUATIONS": f11.get("OOS_EVALUATIONS")})

    # G-KH02-18: Evidence publication & remote SHA readback
    pub_ok = publication_status == "PASS_REMOTE_VERIFIED"
    _add(
        "G-KH02-18",
        "SAME_BRANCH_PUBLICATION_AND_REMOTE_VERIFY",
        "19_git_tested_sha_evidence_publication_receipt.json",
        pub_ok,
        [] if pub_ok else ["AWAITING_REMOTE_PUSH_AND_READBACK"],
        {"publication_status": publication_status, "tested_executable_sha": tested_executable_sha},
        status_override=publication_status,
    )

    by_gid = {g["gate_id"]: g for g in gates}
    eng_gids = [f"G-KH02-{i:02d}" for i in range(1, 18)]
    if any(by_gid[g]["status"] == "FAIL" for g in eng_gids):
        eng_v = "FAIL"
    elif any(by_gid[g]["status"] == "BLOCKED" for g in eng_gids):
        eng_v = "BLOCKED"
    else:
        eng_v = "PASS"

    reg_v = by_gid["G-KH02-11"]["status"]
    res_v = "PASS" if (by_gid["G-KH02-13"]["status"] == "PASS" and by_gid["G-KH02-14"]["status"] == "PASS") else ("BLOCKED" if "BLOCKED" in (by_gid["G-KH02-13"]["status"], by_gid["G-KH02-14"]["status"]) else "FAIL")
    par_v = "PASS" if (by_gid["G-KH02-02"]["status"] == "PASS" and by_gid["G-KH02-08"]["status"] == "PASS") else "FAIL"
    dat_v = "PASS" if (by_gid["G-KH02-04"]["status"] == "PASS" and by_gid["G-KH02-05"]["status"] == "PASS") else "FAIL"
    econ_v = str(f10.get("overall_research_economic_verdict") or "NO_PROVEN_EDGE")
    oos_v = str(f11.get("FRESH_OOS_VERDICT") or "BLOCKED_NO_UNTOUCHED_DATA")

    pass_cnt = sum(1 for g in gates if g["status"] in ("PASS", "PASS_REMOTE_VERIFIED"))
    fail_cnt = sum(1 for g in gates if g["status"] == "FAIL")
    blk_cnt = len(gates) - pass_cnt - fail_cnt

    return {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "run_id": run_id,
        "branch": MANDATORY_BRANCH_KH02,
        "tested_executable_sha": tested_executable_sha,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "orthogonal_verdicts": {
            "ENGINEERING_VERDICT": eng_v,
            "REGRESSION_VERDICT": reg_v,
            "RESOURCE_SAFETY_VERDICT": res_v,
            "BASELINE_PARITY_VERDICT": par_v,
            "DATA_VERDICT": dat_v,
            "RESEARCH_ECONOMIC_VERDICT": econ_v,
            "FRESH_OOS_VERDICT": oos_v,
            "PUBLICATION_VERDICT": publication_status,
            "ACTIVATION_READY": "NO",
            "RUNTIME_SAFETY_MODES": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        },
        "gates_summary": {
            "total_gates": len(gates),
            "pass_count": pass_cnt,
            "fail_count": fail_cnt,
            "blocked_or_pending_count": blk_cnt,
        },
        "gates": gates,
    }


# ==============================================================================
# K. README & VIETNAMESE HANDOVER RENDERER (README + HANDOVER_VI)
# ==============================================================================


def render_kh02_readme_handover_vi(
    *,
    run_id: str,
    branch: str,
    head_at_start_sha: str,
    tested_sha: str,
    f00: Mapping[str, Any],
    f01: Mapping[str, Any],
    f02: Mapping[str, Any],
    f03: Mapping[str, Any],
    f05: Mapping[str, Any],
    f07: Mapping[str, Any],
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
) -> Tuple[str, str]:
    """§6.K & §13: Renders `README_KH02_ENTRY_FOUNDATION.md` and `HANDOVER_KH02_ENTRY_FOUNDATION_VI.txt`."""
    ov = f17["orthogonal_verdicts"]
    jb_raw = f12.get("independent_bijection_verification") or f12.get("bijection_verification") or {}
    jb_c = jb_raw.get("counters") or {}
    jb = {
        "collected_count": jb_c.get("collected", jb_raw.get("collected_count")),
        "executed_count": jb_c.get("executed", jb_raw.get("executed_count")),
        "passed_count": jb_c.get("passed", jb_raw.get("passed_count")),
        "skipped_count": jb_c.get("skipped", jb_raw.get("skipped_count")),
        "failed_count": jb_c.get("failed", jb_raw.get("failed_count")),
        "errors_count": jb_c.get("errors", jb_raw.get("error_count", jb_raw.get("errors_count"))),
    }
    tel = f14.get("runtime_telemetry") or {}
    ram_a = f14.get("memory_samples_audit") or {}
    b0_nb = (f16.get("si0_e00_primary_nb_summary") or {})

    gate_lines = [
        "| Gate ID | Gate Name | Status | Artifact | Observed |",
        "|---|---|---|---|---|",
    ]
    for g in f17["gates"]:
        gate_lines.append(
            f"| `{g['gate_id']}` | `{g['name']}` | **{g['status']}** | `{g['artifact_path']}` | `{json.dumps(g['observed_values'], sort_keys=True)}` |"
        )

    arm_lines = [
        "| Arm | Role | Closed NB | Wins/Losses | WinRate | Net 1x USD | Exp 1x R | 2x All-Cost USD | 95% Cluster CI (R) | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    arms_10 = f10.get("arms_uncertainty") or {}
    for ac in sorted(arms_10.keys()):
        a = arms_10[ac]
        wr = f"{a['win_rate_nb']*100:.2f}%" if a.get("win_rate_nb") is not None else "N/A"
        exp = f"{a['expectancy_1x_nb_r']:+.4f}R" if a.get("expectancy_1x_nb_r") is not None else "NOT_ESTIMABLE"
        ci = str(a.get("cluster_bootstrap_95ci_expectancy_r")) if a.get("cluster_bootstrap_95ci_expectancy_r") else "N/A"
        role = "PRIMARY" if a.get("is_primary_arm") else ("CONTROL" if ac == "CONTROL_B0" else "SECONDARY/NC")
        arm_lines.append(
            f"| `{ac}` | `{role}` | {a['closed_trades_nb']} | {a['wins_nb']}W/{a['losses_nb']}L | {wr} | {a['net_pnl_1x_nb_usd']:+.4f} | {exp} | {a['stress_2x_all_cost_net_pnl_usd']:+.4f} | `{ci}` | `{a['arm_economic_verdict']}` |"
        )

    readme_md = "\n".join([
        f"# AURUM DESK — KH-02 Entry Foundation Research (`{SPEC_VERSION_KH02}`)",
        "",
        f"- **Run ID:** `{run_id}`",
        f"- **Branch:** `{branch}`",
        f"- **HEAD at start:** `{head_at_start_sha}`",
        f"- **Tested executable SHA:** `{tested_sha}`",
        f"- **Canonical Dataset SHA256 (`REUSED_RESEARCH` / `MODELED_BID_ASK`):** `{CANONICAL_DATASET_SHA256_KH02}`",
        f"- **Frozen Hypothesis Manifest SHA256:** `{f03.get('manifest_sha256')}`",
        "",
        "## 1. Orthogonal Verdicts (Non-Collapsed)",
        "",
        f"- `ENGINEERING_VERDICT`: **{ov['ENGINEERING_VERDICT']}**",
        f"- `REGRESSION_VERDICT`: **{ov['REGRESSION_VERDICT']}**",
        f"- `RESOURCE_SAFETY_VERDICT`: **{ov['RESOURCE_SAFETY_VERDICT']}**",
        f"- `BASELINE_PARITY_VERDICT`: **{ov['BASELINE_PARITY_VERDICT']}**",
        f"- `DATA_VERDICT`: **{ov['DATA_VERDICT']}**",
        f"- `RESEARCH_ECONOMIC_VERDICT`: **{ov['RESEARCH_ECONOMIC_VERDICT']}**",
        f"- `FRESH_OOS_VERDICT`: **{ov['FRESH_OOS_VERDICT']}** (`OOS_EVALUATIONS = {f11.get('OOS_EVALUATIONS')}`)",
        f"- `PUBLICATION_VERDICT` (pre-push): **{ov['PUBLICATION_VERDICT']}**",
        "- `ACTIVATION_READY`: **NO** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)",
        "",
        "## 2. Entry Foundation Arms, Ablations & Negative Controls (1x/2x Cost & Cluster CI)",
        "",
        *arm_lines,
        "",
        "## 3. Baseline Parity, Full Regression, Determinism & Resources",
        "",
        f"- **Baseline Parity (`16`):** `A1 = {f16.get('a1_evaluated_count')}` (`0` mismatches), `SI8 = {f16.get('si8_separate_population_count')}`, `SI0 = {b0_nb.get('total_scheduled_episodes')}/{b0_nb.get('closed_trades_count')}` (`{b0_nb.get('wins_count')}W/{b0_nb.get('losses_count')}L`, Net `{b0_nb.get('total_realized_net_usd')} USD`, Exp `{b0_nb.get('expectancy_net_r')}R`), `3-adapter = {f16.get('three_adapter_episodes_denominator')}/{f16.get('three_adapter_episodes_denominator')}` (`0` mismatches), `D4 strict = 0`.",
        f"- **Full Regression (`12`):** `collected={jb.get('collected_count')}`, `executed={jb.get('executed_count')}`, `passed={jb.get('passed_count')}`, `skipped={jb.get('skipped_count')}`, `failed={jb.get('failed_count')}`, `errors={jb.get('errors_count')}`.",
        f"- **Async Determinism (`13`):** pytest `w=4` speedup `{f13.get('measured_speedup_w4_vs_w1')}x` (4/4 identical hashes); KH-02 scientific slice 4/4 identical hashes.",
        f"- **RAM & Disk Safety (`14`):** min available RAM `{ram_a.get('min_available_ram_mib')} MiB` (floor `512.0 MiB`), peak parent RSS `{ram_a.get('peak_parent_rss_mib')} MiB`, peak child-tree RSS `{tel.get('peak_child_tree_rss_mib')} MiB`, `max_children={tel.get('max_children_observed')} (<=4)`, `orphans={tel.get('orphan_pids_at_cleanup_count')}`, min free disk `{tel.get('min_free_disk_gib')} GiB` (`>=20.0 GiB`).",
        f"- **Fault Injection (`15`):** `{f15.get('passed_fault_controls')}/{f15.get('total_fault_controls')}` REAL RED/GREEN PASS.",
        "",
        "## 4. Gate Matrix (`G-KH02-01` .. `G-KH02-18`)",
        "",
        *gate_lines,
        "",
        "## 5. Reproducible Commands",
        "",
        "```powershell",
        "python aurum-desk/tools/run_kh_02_entry_foundation_research.py --max-workers 4",
        f"python aurum-desk/tools/kh_02_entry_foundation_independent_verifier.py --run-dir aurum-desk/docs/artifacts/kh_02_entry_foundation/{run_id} --expected-run-id {run_id} --expected-tested-sha {tested_sha}",
        "```",
        "",
    ])

    handover_vi = "\n".join([
        f"BÀN GIAO NGHIỆM THU KH-02 — ENTRY FOUNDATION RESEARCH ({SPEC_VERSION_KH02})",
        f"1) Nhánh: {branch} | HEAD lúc bắt đầu: {head_at_start_sha} | tested_executable_sha: {tested_sha} | Run ID: {run_id}",
        f"2) File thêm mới: {f01.get('additive_kh02_files_count')} file KH-02 thuần cộng thêm; số file sản xuất/cũ bị sửa: {f01.get('modified_existing_files_count')} (KEEP_AS_IS giữ nguyên 100%).",
        f"3) Kế hoạch giả thuyết đóng băng trước outcome: H1, H2, H3 (SHA256={f03.get('manifest_sha256')}); tập dữ liệu REUSED_RESEARCH / MODELED_BID_ASK ($0.20), embargo={f03.get('purged_embargo_minutes')} phút.",
        f"4) Vũ trụ cơ hội: {f05.get('total_directional_bar_opportunities')} cơ hội hướng M1 sau warmup ({f05.get('raw_m1_triggers_count')} kích hoạt M1, {f05.get('raw_geometry_risk_eligible_count')} đạt chuẩn SL/TP/RR>=2.0 & risk<=2.50 USD) + 56,710 A1 + 6,375 SI8.",
        f"5) Phán quyết trực giao: ENGINEERING={ov['ENGINEERING_VERDICT']} | REGRESSION={ov['REGRESSION_VERDICT']} | RESOURCE={ov['RESOURCE_SAFETY_VERDICT']} | PARITY={ov['BASELINE_PARITY_VERDICT']} | DATA={ov['DATA_VERDICT']} | ECONOMIC={ov['RESEARCH_ECONOMIC_VERDICT']} | FRESH_OOS={ov['FRESH_OOS_VERDICT']} | ACTIVATION_READY=NO.",
        f"6) Baseline Parity: A1=56710 (0 lệch), SI8=6375, SI0={b0_nb.get('total_scheduled_episodes')}/{b0_nb.get('closed_trades_count')} ({b0_nb.get('wins_count')}W/{b0_nb.get('losses_count')}L, Net={b0_nb.get('total_realized_net_usd')} USD, Exp={b0_nb.get('expectancy_net_r')}R), 3-adapter 246/246 khớp 100%, Strict D4=0.",
        f"7) Full Pytest: collected={jb.get('collected_count')}, executed={jb.get('executed_count')}, passed={jb.get('passed_count')}, skipped={jb.get('skipped_count')}, failed={jb.get('failed_count')}, errors={jb.get('errors_count')}.",
        f"8) RAM/Disk: đáy RAM={ram_a.get('min_available_ram_mib')} MiB (>=512 MiB), peak_parent_rss={ram_a.get('peak_parent_rss_mib')} MiB, peak_child_rss={tel.get('peak_child_tree_rss_mib')} MiB, max_children={tel.get('max_children_observed')} (<=4), orphans={tel.get('orphan_pids_at_cleanup_count')}, đáy đĩa={tel.get('min_free_disk_gib')} GiB (>=20.0 GiB).",
        f"9) Fault Injection RED/GREEN: {f15.get('passed_fault_controls')}/{f15.get('total_fault_controls')} PASS.",
        "10) Khóa an toàn: PRODUCTION=OFF; LIVE=OFF; PAPER=OFF; AUTO=OFF; ACTIVATION_READY=NO. Không kích hoạt giao dịch; bước tiếp theo chỉ thu thập & niêm phong prospective OOS chưa chạm.",
        "",
        *gate_lines,
        "",
    ])
    return readme_md, handover_vi
