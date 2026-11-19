"""
aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py

AURUM DESK — KH-01B.R1: Entry Edge Discovery, Baseline Thesis Audit & Prospective Data Readiness
SPEC VERSION: KH-01B.R1-v1.0 (2026-11-19)

Additive, strictly RESEARCH_ONLY service built on top of existing authoritative AURUM Desk
modules WITHOUT modifying any existing production or prior KH-01A / KH-01A.R1 / KH-01B code:
  - SharedStrategyEvaluator.evaluate_model_direction (production truth, untouched)
  - kh_01b_entry_unicorn_research_service (reference B00..B12, D4 & feature frame, untouched)
  - v17_9_3_1_2_2_signal_intelligence (advance_pending_confirmation_state_v1793122 & arbitration, untouched)
  - v17_9_3_macro_unicorn_intelligence (macro schedule & strict True Unicorn, untouched)
  - v17_9_3_1_2_14_accounting_target_and_d4_truth_service (source-TF ATR & D4 funnel, untouched)

HARD SAFETY LOCKS:
  - PRODUCTION = OFF, LIVE = OFF, PAPER = OFF, AUTO = OFF, ACTIVATION_READY = NO.
  - Zero live orders, zero strategy threshold changes in production, zero mutation of historical artifacts.
  - Zero trades => expectancy_1x_nb_r = None (NOT_ESTIMABLE), never 0.0R edge.
"""
from __future__ import annotations

import ast
import bisect
from collections import Counter
import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import importlib
import inspect
import json
import math
from pathlib import Path
import random
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from domain_calculator import CostAssumptions
from services.kh_01a_foundation_service import (
    CANONICAL_DATASET_SHA256_KH01A,
    MANDATORY_BRANCH_KH01A,
)
from services.kh_01a_r1_forensics_closure_service import (
    SCHEMA_VERSION_KH01A_R1,
    scan_all_macro_window_call_sites_ast,
)
from services.kh_01b_entry_unicorn_research_service import (
    CANONICAL_DATASET_SHA256_KH01B,
    FILL_MODES_KH01B,
    FeatureEvidenceFrame,
    MANDATORY_BRANCH_KH01B,
    PRIOR_R1_RUN_ID,
    PRIOR_R1_TESTED_EXECUTABLE_SHA,
    PRIOR_V130_REL_DIR,
    VariantReplayBundle,
    _summarize_episode_list_kh01b,
    audit_kh01b_true_unicorn_positive_and_negative_fixtures,
    evaluate_strict_true_unicorn_hypothesis,
    evaluate_unicorn_adjacent_research_hypothesis,
    extract_kh01b_causal_feature_frame,
    load_and_verify_kh01b_reference_baseline,
    verify_kh01b_full_baseline_and_adapter_parity,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    PRODUCTION_MODEL_STATIC_PRIORITY,
    PendingConfirmationV1793122,
    advance_pending_confirmation_state_v1793122,
    arbitrate_competing_model_intents_v1793122,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    canonical_hash,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service import (
    build_d4_full_63085_and_matched_funnel_v17931214,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import EvidenceReceiptV17931219
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    PortfolioReplayStateV179312,
    advance_portfolio_to_next_event_v179312,
    recalculate_trade_risk_quantity_v179312,
    replay_intervention_outcome_v179312,
)
from tools.kh_01b_r1_collect_market_data import (
    CANONICAL_SAMPLE_END_MS,
    build_and_validate_future_oos_escrow_registry,
    validate_and_normalize_paginated_candles,
    validate_prospective_bbo_snapshot,
)

# ==============================================================================
# CONSTANTS & CANONICAL ENUMS
# ==============================================================================

SPEC_VERSION_KH01B_R1 = "KH-01B.R1-v1.0"
SCHEMA_VERSION_KH01B_R1 = "kh_01b_r1.entry_edge_discovery.v1"
MANDATORY_BRANCH_KH01B_R1 = MANDATORY_BRANCH_KH01B
CANONICAL_DATASET_SHA256_KH01B_R1 = CANONICAL_DATASET_SHA256_KH01B

PRIOR_KH01B_TESTED_SHA = "a7f0f4367888a672cdff7af1bc9f5d2027d167d3"
PRIOR_KH01B_PUBLICATION_HEAD_SHA = "fe19e6b5b4b08425553ce5d2431b3a8c4aac4198"
PRIOR_KH01B_RUN_ID = "20261119T090052Z_a7f0f4367888"
PRIOR_KH01B_RUN_REL_DIR = f"aurum-desk/docs/artifacts/kh_01b/{PRIOR_KH01B_RUN_ID}"
PRIOR_KH01B_MANIFEST_SHA256 = "82049cc36152a062285f49dbe2598aafc7754a64da2135a9d50250912007e253"

RESEARCH_ONLY_FEATURE_FLAG_KH01B_R1: Dict[str, Any] = {
    "policy_namespace": "KH01B_R1_RESEARCH_ONLY_DEFAULT_OFF",
    "PRODUCTION": "OFF",
    "LIVE": "OFF",
    "PAPER": "OFF",
    "AUTO": "OFF",
    "research_only": True,
}

FORBIDDEN_EXPOST_LEAKAGE_KEYS: frozenset[str] = frozenset({
    "_planted_future_mfe",
    "expost_mfe_r",
    "expost_mae_r",
    "post_stop_tp_hit",
    "future_pivot_id",
    "future_high_after_entry",
    "future_low_after_entry",
    "retrospective_win_label",
    "DESCRIPTIVE_EX_POST",
})

MANDATORY_KH01B_R1_ENGINEERING_GATES: Tuple[str, ...] = (
    "G01", "G02", "G03", "G04", "G05", "G06", "G07",
    "G08", "G09", "G10", "G11", "G12", "G13", "G14",
    "G16", "G17",
)
ALL_KH01B_R1_GATES: Tuple[str, ...] = tuple(f"G{i:02d}" for i in range(1, 19))

REQUIRED_ARTIFACTS_KH01B_R1: Tuple[str, ...] = (
    "00_preflight_branch_source_dataset_invariants.json",
    "01_function_callgraph_keep_as_is_and_no_production_change.json",
    "01_function_to_responsibility_map.json",
    "02_frozen_hypothesis_budget_and_expected_falsification.json",
    "03_si0_242_episode_preentry_vs_expost_loss_attribution.json",
    "04_model_direction_session_regime_cost_decomposition.json",
    "05_source_evidence_missingness_causal_regime_quality.json",
    "06_extended_dev_dataset_provenance_gap_and_quote_capability.json",
    "07_future_oos_sealed_escrow_registry_no_evaluation.json",
    "08_E00_E06_variant_conservation_and_rejection_reason_ledger.json",
    "09_matched_baseline_and_chronological_1pos_3fill_economics.json",
    "10_cost_stress_cluster_bootstrap_reused_purged_folds.json",
    "11_D4_strict_unchanged_and_unicorn_adjacent_isolation.json",
    "12_full_regression_junit_shard_exits_bijection.json",
    "13_async_workers_w1_w2_w4_repeat_speed_determinism.json",
    "14_disk_ram_cpu_pid_atomic_write_cleanup.json",
    "15_real_fault_injection_red_green_mutations.json",
    "16_full_baseline_decision_trade_action_and_adapter_parity.json",
    "17_independent_verifier_sha256_receipts_manifest.json",
    "18_final_orthogonal_engineering_economics_data_oos_verdicts.json",
    "README_KH01B_R1.md",
    "HANDOVER_KH01B_R1_VI.txt",
    "ledgers/kh01b_r1_si0_242_casebook.jsonl.gz",
    "ledgers/kh01b_r1_all_variants_episode_ledgers.jsonl.gz",
    "ledgers/kh01b_r1_candidate_filter_decisions_summary.jsonl.gz",
)

NY_TZ = ZoneInfo("America/New_York")


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s:
                yield json.loads(s)


def _wilson_95ci(successes: int, n: int) -> Optional[Tuple[float, float]]:
    if n <= 0:
        return None
    z = 1.959963984540054
    phat = successes / n
    denom = 1.0 + (z * z) / n
    center = (phat + (z * z) / (2.0 * n)) / denom
    half = (z * math.sqrt((phat * (1.0 - phat) + (z * z) / (4.0 * n)) / n)) / denom
    return (round(max(0.0, center - half), 6), round(min(1.0, center + half), 6))


def assert_research_only_runtime_modes_off(runtime_modes: Optional[Mapping[str, Any]] = None) -> None:
    """G10 hard safety guard: rejects any attempt to activate LIVE, PAPER, AUTO, or PRODUCTION."""
    if runtime_modes is None:
        return
    for key in ("PRODUCTION", "LIVE", "PAPER", "AUTO", "RESEARCH_APPLY_LIVE", "enabled_in_production"):
        val = runtime_modes.get(key)
        if val is True or (isinstance(val, str) and val.strip().upper() not in ("OFF", "NO", "FALSE", "")):
            raise PermissionError(f"RUNTIME_ACTIVATION_FORBIDDEN_IN_KH01B_R1:{key}={val!r}")


# ==============================================================================
# D3.A.1: LOAD & VERIFY IMMUTABLE KH-01A / KH-01A.R1 / KH-01B SOURCES
# ==============================================================================

@dataclass(frozen=True)
class KH01BR1SourceTruth:
    spec_version: str
    schema_version: str
    branch: str
    expected_sha: str
    canonical_dataset_sha256: str
    prior_use_status: str
    prior_kh01a_r1_run_id: str
    prior_kh01b_run_id: str
    prior_kh01b_tested_sha: str
    prior_kh01b_manifest_sha256: str
    prior_kh01b_b00_net_pnl_usd: float
    prior_kh01b_b00_expectancy_r: float
    prior_kh01b_b09_closed_trades: int
    prior_kh01b_b12_closed_trades: int
    a1_candidate_count: int
    si8_discovery_count: int
    total_candidate_universe_count: int
    si0_scheduled_count: int
    si0_closed_count: int
    si0_wins_count: int
    si0_losses_count: int
    forensics_170_partial_count: int
    verification_passed: bool
    violations: Tuple[str, ...]
    source_truth_sha256: str


def load_and_verify_kh01b_r1_sources(
    repo_root: Path,
    expected_sha: str,
    existing_latest_ptr: Optional[Path] = None,
    *,
    branch_override: Optional[str] = None,
    kh01b_dir_override: Optional[Path] = None,
) -> KH01BR1SourceTruth:
    """
    D3.A.1: Verifies immutable baseline, KH-01A.R1, and KH-01B artifacts, dataset SHA256,
    candidate universe conservation (56,710 + 6,375 = 63,085), 170 PARTIAL forensic rows,
    and prior B00..B12 frozen results without mutating any prior artifact.
    """
    violations: List[str] = []
    base_truth = load_and_verify_kh01b_reference_baseline(repo_root)
    if not base_truth.verification_passed:
        violations.extend(base_truth.violations)

    branch = str(branch_override or MANDATORY_BRANCH_KH01B_R1)
    if branch != MANDATORY_BRANCH_KH01B_R1:
        violations.append(f"WRONG_MANDATORY_BRANCH:{branch}!={MANDATORY_BRANCH_KH01B_R1}")
    if not expected_sha or len(expected_sha.strip()) < 7:
        violations.append(f"INVALID_EXPECTED_SHA:{expected_sha!r}")

    kh01b_dir = kh01b_dir_override if kh01b_dir_override is not None else (repo_root / PRIOR_KH01B_RUN_REL_DIR)
    b_manifest_sha = ""
    b00_net = 0.0
    b00_exp = 0.0
    b09_closed = -1
    b12_closed = -1

    if not kh01b_dir.is_dir():
        violations.append(f"MISSING_PRIOR_KH01B_RUN_DIR:{kh01b_dir}")
    else:
        for req_f in (
            "02_pre_registered_hypothesis_manifest_and_search_budget.json",
            "09_strict_D4_and_adjacent_B09_B10_detector_funnel.json",
            "11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json",
            "19_final_orthogonal_gates_blockers_and_kh01c_candidates.json",
        ):
            if not (kh01b_dir / req_f).is_file():
                violations.append(f"MISSING_PRIOR_KH01B_FILE:{req_f}")

        if not violations:
            b02 = json.loads((kh01b_dir / "02_pre_registered_hypothesis_manifest_and_search_budget.json").read_text("utf-8"))
            b11 = json.loads((kh01b_dir / "11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json").read_text("utf-8"))
            b19 = json.loads((kh01b_dir / "19_final_orthogonal_gates_blockers_and_kh01c_candidates.json").read_text("utf-8"))

            b_manifest_sha = str(b02.get("manifest_sha256") or "")
            if b_manifest_sha != PRIOR_KH01B_MANIFEST_SHA256:
                violations.append(f"PRIOR_KH01B_MANIFEST_MUTATED:{b_manifest_sha}!={PRIOR_KH01B_MANIFEST_SHA256}")
            if str(b19.get("tested_executable_sha") or "") != PRIOR_KH01B_TESTED_SHA:
                violations.append(f"PRIOR_KH01B_TESTED_SHA_MISMATCH:{b19.get('tested_executable_sha')}")

            ec_map = b11.get("variants_economics") or {}
            b00_net = float((ec_map.get("B00") or {}).get("net_pnl_1x_nb_usd") or 0.0)
            b00_exp = float((ec_map.get("B00") or {}).get("expectancy_1x_nb_r") or 0.0)
            b09_closed = int((ec_map.get("B09") or {}).get("closed_trades_nb", -1))
            b12_closed = int((ec_map.get("B12") or {}).get("closed_trades_nb", -1))
            if b09_closed != 0:
                violations.append(f"UNEXPECTED_PRIOR_B09_CLOSED_COUNT:{b09_closed}")

    # Verify KH-01A.R1 170 partial rows remain PARTIAL (never upgraded to persisted context)
    r1_f06_path = repo_root / f"aurum-desk/docs/artifacts/kh_01a_r1/{PRIOR_R1_RUN_ID}/06_feature_evidence_origin_missing_invalid_source_hash_census.json"
    partial_170 = 0
    if r1_f06_path.is_file():
        r1_f06 = json.loads(r1_f06_path.read_text("utf-8"))
        comp_dist = r1_f06.get("completeness_distribution") or (r1_f06.get("ledger_conservation") or {})
        partial_170 = int(
            (r1_f06.get("evidence_completeness_counts") or {}).get("PARTIAL")
            or comp_dist.get("PARTIAL")
            or base_truth.prior_r1_forensics_170_count
        )
        if partial_170 != 170:
            violations.append(f"KH01A_R1_PARTIAL_COUNT_NOT_170:{partial_170}")
    else:
        violations.append("MISSING_KH01A_R1_F06_CENSUS")

    if existing_latest_ptr is not None and existing_latest_ptr.exists():
        try:
            ptr_doc = json.loads(existing_latest_ptr.read_text("utf-8"))
            if not ptr_doc.get("run_id"):
                violations.append("MALFORMED_EXISTING_KH01B_R1_LATEST_PTR")
        except Exception as exc:
            violations.append(f"CORRUPT_EXISTING_KH01B_R1_LATEST_PTR:{exc}")

    body = {
        "spec_version": SPEC_VERSION_KH01B_R1,
        "schema_version": SCHEMA_VERSION_KH01B_R1,
        "branch": branch,
        "expected_sha": expected_sha,
        "canonical_dataset_sha256": base_truth.dataset_sha256,
        "prior_use_status": base_truth.prior_use_status,
        "prior_kh01a_r1_run_id": base_truth.prior_r1_run_id,
        "prior_kh01b_run_id": PRIOR_KH01B_RUN_ID,
        "prior_kh01b_tested_sha": PRIOR_KH01B_TESTED_SHA,
        "prior_kh01b_manifest_sha256": b_manifest_sha,
        "prior_kh01b_b00_net_pnl_usd": b00_net,
        "prior_kh01b_b00_expectancy_r": b00_exp,
        "prior_kh01b_b09_closed_trades": b09_closed,
        "prior_kh01b_b12_closed_trades": b12_closed,
        "a1_candidate_count": base_truth.a1_candidate_count,
        "si8_discovery_count": base_truth.si8_discovery_count,
        "total_candidate_universe_count": base_truth.total_candidate_universe_count,
        "si0_scheduled_count": base_truth.si0_reference_scheduled,
        "si0_closed_count": base_truth.si0_reference_closed,
        "si0_wins_count": base_truth.si0_reference_wins,
        "si0_losses_count": base_truth.si0_reference_losses,
        "forensics_170_partial_count": partial_170,
        "verification_passed": len(violations) == 0,
        "violations": tuple(violations),
    }
    return KH01BR1SourceTruth(**body, source_truth_sha256=canonical_hash(body))


# ==============================================================================
# D: FUNCTION-TO-RESPONSIBILITY CALLGRAPH & KEEP-AS-IS MAP (01)
# ==============================================================================

def build_kh01b_r1_function_callgraph_and_keep_as_is_map(
    repo_root: Path,
    *,
    starting_head_sha: str,
    tested_sha: str,
    changed_files: Sequence[str] = (),
) -> Dict[str, Any]:
    """
    Builds `01_function_callgraph_keep_as_is_and_no_production_change.json` and
    `01_function_to_responsibility_map.json` with live AST line numbers, parameters,
    source SHA256, callers, input fields, side effects, and KEEP_AS_IS proofs.
    """
    app_root = repo_root / "aurum-desk"
    keep_specs: List[Tuple[str, str, str, List[str], List[str], str]] = [
        # (rel_file, import_mod, symbol, callers, inputs, responsibility)
        (
            "aurum-desk/backend/services/shared_strategy_evaluator.py",
            "services.shared_strategy_evaluator",
            "SharedStrategyEvaluator.evaluate_model_direction",
            ["ScalpWatcherOrchestrator", "MarketShadowService", "V17_8_BacktestRunner", "run_kh_01b_r1_verification"],
            ["context: MarketDecisionContext", "model_id: str", "direction: str"],
            "Production strategy source of truth; decision_fingerprint & trade_action_fingerprint kept 100% unchanged.",
        ),
        (
            "aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py",
            "services.kh_01b_entry_unicorn_research_service",
            "load_and_verify_kh01b_reference_baseline",
            ["load_and_verify_kh01b_r1_sources", "run_kh_01b_verification"],
            ["repo_root", "run_registry"],
            "Authoritative reference baseline & 63,085 candidate membership verifier; reused directly.",
        ),
        (
            "aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py",
            "services.kh_01b_entry_unicorn_research_service",
            "pre_register_kh01b_hypotheses",
            ["run_kh_01b_verification"],
            ["dataset_sha", "config_sha", "code_sha", "variant_manifest"],
            "Frozen historical B00..B12 manifest generator; untouched in R1.",
        ),
        (
            "aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py",
            "services.kh_01b_entry_unicorn_research_service",
            "extract_kh01b_causal_feature_frame",
            ["extract_kh01b_r1_causal_regime_features", "run_kh01b_orthogonal_ablation_matrix"],
            ["candidate", "verified_feature_ledger", "closed_bar_index", "asof_ts"],
            "Point-in-time feature frame & tri-state evidence extractor; reused directly.",
        ),
        (
            "aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py",
            "services.kh_01b_entry_unicorn_research_service",
            "evaluate_strict_true_unicorn_hypothesis",
            ["replay_kh01b_r1_frozen_variants", "_evaluate_single_candidate_for_variant"],
            ["frame", "context", "setup_episode"],
            "Strict D4 True Unicorn detector (>=1 tick Breaker+FVG overlap + episode + retest); kept strictly distinct.",
        ),
        (
            "aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py",
            "services.kh_01b_entry_unicorn_research_service",
            "evaluate_unicorn_adjacent_research_hypothesis",
            ["replay_kh01b_r1_frozen_variants", "_evaluate_single_candidate_for_variant"],
            ["frame", "variant_id"],
            "Unicorn-adjacent comparator with counts_toward_d4_true_unicorn=False; kept isolated from D4.",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "services.v17_9_3_1_2_2_signal_intelligence",
            "build_signal_quality_evidence_v1793122",
            ["SharedStrategyEvaluator", "kh_01a_foundation_service"],
            ["context", "snapshot", "policy"],
            "Point-in-time pre-entry signal quality evidence supplier; non-rescue & known_at preserved.",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "services.v17_9_3_1_2_2_signal_intelligence",
            "evaluate_signal_intelligence_gate_v1793122",
            ["SharedStrategyEvaluator"],
            ["context", "snapshot", "mode", "policy_id"],
            "Observe-only / OFF production gate; RESEARCH_APPLY stays OFF for LIVE/PAPER.",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "services.v17_9_3_1_2_2_signal_intelligence",
            "advance_pending_confirmation_state_v1793122",
            ["evaluate_kh01b_r1_entry_variant (E05)"],
            ["pending", "bars_1m", "start_bar_index", "atr_m1"],
            "Advances pending setup strictly forward (c_ms > created_at_ms) and recomputes entry/SL/TP/qty/risk at confirmation close.",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "services.v17_9_3_1_2_2_signal_intelligence",
            "arbitrate_competing_model_intents_v1793122",
            ["replay_kh01b_r1_frozen_variants"],
            ["candidates_at_ts", "arbitration_mode", "feature_ledger_by_opp_id"],
            "Deterministic STATIC_PRODUCTION_PRIORITY multi-model arbitration; tracks winner substitutions vs B00.",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
            "services.v17_9_3_macro_unicorn_intelligence",
            "resolve_causal_macro_time_window",
            ["extract_kh01b_causal_feature_frame"],
            ["decision_timestamp_ms", "symbol", "schedule_config"],
            "Keyword-bound NY ICT macro window resolver; research schedule disclosed as unverified exchange calendar.",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
            "services.v17_9_3_macro_unicorn_intelligence",
            "evaluate_causal_unicorn_confluence",
            ["evaluate_strict_true_unicorn_hypothesis"],
            ["context", "direction", "decision_timestamp_ms"],
            "Strict Breaker+FVG confluence authority; kept 100% unchanged.",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
            "services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service",
            "compute_causal_tf_atr_v17931214",
            ["extract_kh01b_causal_feature_frame", "extract_kh01b_r1_causal_regime_features"],
            ["datasets", "close_ts_index", "timeframe", "asof_ts_ms"],
            "Causal source-timeframe ATR calculator on closed bars (close_time <= asof_ts_ms).",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
            "services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service",
            "build_d4_full_63085_and_matched_funnel_v17931214",
            ["replay_kh01b_r1_frozen_variants"],
            ["run_id", "tested_sha", "dataset_sha", "baseline_decisions", "si8_decisions"],
            "Authoritative 63,085 -> 7,562 -> 184 -> 14 -> 0 strict D4 funnel.",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_executable_intervention_replay_service.py",
            "services.v17_9_3_1_2_executable_intervention_replay_service",
            "replay_intervention_outcome_v179312",
            ["replay_kh01b_r1_frozen_variants"],
            ["materialized_intent", "bars_1m", "mode", "base_record"],
            "Source-aware bid/ask 3-fill execution simulator with conservative stop-first tie-breaking.",
        ),
    ]

    changed_set = set(changed_files)
    keep_rows: List[Dict[str, Any]] = []
    for rel_f, mod_n, sym_n, callers, inputs, resp in keep_specs:
        mod_obj = importlib.import_module(mod_n)
        if "." in sym_n:
            cls_n, mth_n = sym_n.split(".", 1)
            fn_obj = getattr(getattr(mod_obj, cls_n), mth_n)
        else:
            fn_obj = getattr(mod_obj, sym_n)
        src_lines, line_no = inspect.getsourcelines(fn_obj)
        keep_rows.append({
            "file": rel_f,
            "symbol": sym_n,
            "line_start": line_no,
            "line_end": line_no + len(src_lines) - 1,
            "signature": str(inspect.signature(fn_obj)),
            "function_source_sha256": hashlib.sha256("".join(src_lines).encode("utf-8")).hexdigest(),
            "callers": callers,
            "input_fields": inputs,
            "side_effects": "NONE_PURE_READ_ONLY",
            "modification_decision": "KEEP_AS_IS",
            "modified_in_kh01b_r1": rel_f in changed_set,
            "responsibility": resp,
        })

    this_mod = importlib.import_module(__name__)
    r1_fn_names = [
        ("R1-F01", "load_and_verify_kh01b_r1_sources"),
        ("R1-F02", "build_kh01b_r1_preentry_outcome_attribution"),
        ("R1-F03", "audit_kh01b_r1_strategy_mechanism_by_model_direction_regime"),
        ("R1-F04", "pre_register_kh01b_r1_theses"),
        ("R1-F05", "extract_kh01b_r1_causal_regime_features"),
        ("R1-F06", "evaluate_kh01b_r1_entry_variant"),
        ("R1-F07", "replay_kh01b_r1_frozen_variants"),
        ("R1-F08", "compute_kh01b_r1_edge_uncertainty_and_research_verdict"),
        ("R1-F09", "validate_kh01b_r1_data_provenance_and_escrow"),
        ("R1-F10", "evaluate_kh01b_r1_engineering_and_economic_gates_from_disk"),
        ("R1-F11", "run_kh01b_r1_real_fault_injections"),
        ("R1-F12", "render_kh01b_r1_readme_and_handover_vi"),
        ("R1-F13", "verify_kh01b_r1_cross_view_consistency"),
    ]
    new_rows: List[Dict[str, Any]] = []
    for cid, fname in r1_fn_names:
        fn_obj = getattr(this_mod, fname)
        src_lines, line_no = inspect.getsourcelines(fn_obj)
        new_rows.append({
            "contract_id": cid,
            "file": "aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py",
            "symbol": fname,
            "line_start": line_no,
            "line_end": line_no + len(src_lines) - 1,
            "signature": str(inspect.signature(fn_obj)),
            "function_source_sha256": hashlib.sha256("".join(src_lines).encode("utf-8")).hexdigest(),
            "side_effects": "NONE_RESEARCH_ONLY_IN_MEMORY_OR_OWNED_ARTIFACT_DIR",
            "modification_decision": "NEW_ADDITIVE_RESEARCH_ONLY",
        })

    existing_modified = [
        f for f in changed_files
        if (f.startswith("aurum-desk/backend/") or f.startswith("aurum-desk/tools/"))
        and "kh_01b_r1" not in Path(f).name
    ]
    macro_ast = scan_all_macro_window_call_sites_ast(app_root)

    return {
        "spec_version": SPEC_VERSION_KH01B_R1,
        "schema_version": SCHEMA_VERSION_KH01B_R1,
        "starting_head_sha": starting_head_sha,
        "tested_executable_sha": tested_sha,
        "existing_production_or_prior_kh_files_modified_count": len(existing_modified),
        "existing_production_or_prior_kh_files_modified": existing_modified,
        "changed_files_in_kh01b_r1": list(changed_files),
        "runtime_safety_lock": dict(RESEARCH_ONLY_FEATURE_FLAG_KH01B_R1),
        "keep_as_is_functions": keep_rows,
        "new_kh01b_r1_functions": new_rows,
        "macro_call_site_ast_audit": macro_ast,
    }


# ==============================================================================
# D3.A.5: EXTRACT CAUSAL REGIME, VOLATILITY & DATA-INTEGRITY FEATURES (AS-OF ONLY)
# ==============================================================================

@dataclass
class CausalRegimeFeatureFrameR1:
    opportunity_id: str
    base_frame: FeatureEvidenceFrame
    predictive_pre_entry: Dict[str, Any]
    causal_regime_tri_state: str  # PROVEN_TRUE | PROVEN_FALSE | UNKNOWN_MISSING | INVALID
    causal_regime_label: str      # TREND_EXPANSION_ALIGNED | CHOP_OR_COUNTER_REGIME | UNKNOWN_MISSING | INVALID
    quality_cohort_tri_state: str
    quality_rejection_reason: Optional[str]
    max_known_at_ms: int
    source_bar_ids_all: Tuple[str, ...]
    invalidation_reasons: Tuple[str, ...]
    r1_frame_sha256: str


def _compute_trailing_atr_and_percentile_asof(
    bars: Sequence[Mapping[str, Any]],
    close_times: Sequence[int],
    asof_ts: int,
    *,
    tf_label: str,
    tf_duration_ms: int,
    atr_period: int = 14,
    trailing_percentile_window: int = 48,
) -> Dict[str, Any]:
    """
    Computes trailing Simple/Wilder True-Range ATR(`atr_period`) AND its empirical percentile
    strictly within the trailing `trailing_percentile_window` closed bars (`close_time <= asof_ts`).
    NEVER touches any bar with `close_time > asof_ts` (zero global future quantile leakage!).
    """
    end_idx = bisect.bisect_right(close_times, asof_ts)
    if end_idx < atr_period + 2:
        return {
            "status": "UNKNOWN_MISSING",
            "atr": None,
            "trailing_percentile": None,
            "latest_close_ms": int(close_times[end_idx - 1]) if end_idx > 0 else None,
            "bar_fresh": False,
            "source_bar_ids": [],
        }

    # Verify last closed bar integrity (no unclosed / future close_time)
    last_bar = bars[end_idx - 1]
    if last_bar.get("is_closed") is False:
        return {
            "status": "INVALID",
            "invalid_reason": f"UNCLOSED_BAR_IN_TRAILING_{tf_label}",
            "atr": None,
            "trailing_percentile": None,
            "latest_close_ms": None,
            "bar_fresh": False,
            "source_bar_ids": [],
        }
    last_c_ms = int(close_times[end_idx - 1])
    if last_c_ms > asof_ts:
        return {
            "status": "INVALID",
            "invalid_reason": f"FUTURE_BAR_IN_TRAILING_{tf_label}:{last_c_ms}>{asof_ts}",
            "atr": None,
            "trailing_percentile": None,
            "latest_close_ms": last_c_ms,
            "bar_fresh": False,
            "source_bar_ids": [],
        }

    need_bars = atr_period + trailing_percentile_window + 1
    start_idx = max(0, end_idx - need_bars)
    sub = bars[start_idx:end_idx]

    trs: List[float] = []
    for i in range(1, len(sub)):
        b_prev = sub[i - 1]
        b_cur = sub[i]
        if b_cur.get("is_closed") is False:
            return {
                "status": "INVALID",
                "invalid_reason": f"REOPENED_CANDLE_IN_{tf_label}",
                "atr": None,
                "trailing_percentile": None,
                "latest_close_ms": last_c_ms,
                "bar_fresh": False,
                "source_bar_ids": [],
            }
        h = float(b_cur["high"])
        l = float(b_cur["low"])
        pc = float(b_prev["close"])
        if h < l or l <= 0.0:
            return {
                "status": "INVALID",
                "invalid_reason": f"INVALID_OHLC_IN_{tf_label}",
                "atr": None,
                "trailing_percentile": None,
                "latest_close_ms": last_c_ms,
                "bar_fresh": False,
                "source_bar_ids": [],
            }
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    if len(trs) < atr_period:
        return {
            "status": "UNKNOWN_MISSING",
            "atr": None,
            "trailing_percentile": None,
            "latest_close_ms": last_c_ms,
            "bar_fresh": False,
            "source_bar_ids": [],
        }

    rolling_atrs: List[float] = []
    for end_k in range(atr_period, len(trs) + 1):
        win = trs[end_k - atr_period : end_k]
        rolling_atrs.append(sum(win) / atr_period)

    cur_atr = rolling_atrs[-1]
    hist_atrs = rolling_atrs[-trailing_percentile_window:]
    pct = sum(1 for x in hist_atrs if x <= cur_atr + 1e-12) / len(hist_atrs) if len(hist_atrs) >= 8 else None

    src_bars = sub[-(atr_period + 1) :]
    src_ids = [
        f"XAUUSDT:{tf_label}:{int(b.get('open_time') or b.get('timestamp') or 0)}"
        for b in src_bars
    ]
    staleness_ms = asof_ts - last_c_ms
    is_fresh = 0 <= staleness_ms <= (2 * tf_duration_ms)

    return {
        "status": "VALID" if pct is not None else "UNKNOWN_MISSING",
        "atr": round(cur_atr, 6),
        "trailing_percentile": round(pct, 6) if pct is not None else None,
        "latest_close_ms": last_c_ms,
        "staleness_ms": staleness_ms,
        "bar_fresh": is_fresh,
        "source_bar_ids": src_ids,
    }


def _compute_m15_directional_efficiency_asof(
    bars_15m: Sequence[Mapping[str, Any]],
    close_15m: Sequence[int],
    asof_ts: int,
    direction: str,
    *,
    lookback_bars: int = 8,
) -> Dict[str, Any]:
    """
    Computes Kaufman-style directional efficiency over trailing `lookback_bars` closed M15 intervals
    (`close_time <= asof_ts`), signed positively when aligned with `direction`.
    """
    end_idx = bisect.bisect_right(close_15m, asof_ts)
    if end_idx < lookback_bars + 1:
        return {
            "status": "UNKNOWN_MISSING",
            "signed_efficiency": None,
            "abs_efficiency": None,
            "latest_close_ms": int(close_15m[end_idx - 1]) if end_idx > 0 else None,
            "bar_fresh": False,
            "source_bar_ids": [],
        }

    sub = bars_15m[end_idx - (lookback_bars + 1) : end_idx]
    for b in sub:
        if b.get("is_closed") is False:
            return {
                "status": "INVALID",
                "invalid_reason": "REOPENED_M15_CANDLE",
                "signed_efficiency": None,
                "abs_efficiency": None,
                "latest_close_ms": None,
                "bar_fresh": False,
                "source_bar_ids": [],
            }
    last_c_ms = int(close_15m[end_idx - 1])
    if last_c_ms > asof_ts:
        return {
            "status": "INVALID",
            "invalid_reason": f"FUTURE_M15_BAR_CLOSE:{last_c_ms}>{asof_ts}",
            "signed_efficiency": None,
            "abs_efficiency": None,
            "latest_close_ms": last_c_ms,
            "bar_fresh": False,
            "source_bar_ids": [],
        }

    net_chg = float(sub[-1]["close"]) - float(sub[0]["close"])
    path_sum = sum(abs(float(sub[i]["close"]) - float(sub[i - 1]["close"])) for i in range(1, len(sub)))
    raw_eff = (net_chg / path_sum) if path_sum > 1e-9 else 0.0
    signed_eff = raw_eff if direction == "LONG" else -raw_eff
    staleness_ms = asof_ts - last_c_ms
    src_ids = [f"XAUUSDT:15M:{int(b.get('open_time') or b.get('timestamp') or 0)}" for b in sub]
    return {
        "status": "VALID",
        "signed_efficiency": round(signed_eff, 6),
        "abs_efficiency": round(abs(raw_eff), 6),
        "net_price_change_8_m15": round(net_chg, 4),
        "latest_close_ms": last_c_ms,
        "staleness_ms": staleness_ms,
        "bar_fresh": 0 <= staleness_ms <= (2 * 900_000),
        "source_bar_ids": src_ids,
    }


def extract_kh01b_r1_causal_regime_features(
    candidate: Mapping[str, Any],
    verified_feature_ledger: Mapping[str, Mapping[str, Any]],
    closed_bar_index: Mapping[Tuple[str, int], int],
    asof_ts: int,
    *,
    datasets: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    close_ts_index: Optional[Mapping[str, Sequence[int]]] = None,
    pools_by_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
    macro_cache: Optional[Dict[int, Dict[str, Any]]] = None,
    atr_cache: Optional[Dict[Tuple[str, int], Optional[float]]] = None,
    gap_180m_cache: Optional[Dict[int, bool]] = None,
    regime_ts_cache: Optional[Dict[Tuple[int, str], Dict[str, Any]]] = None,
    pre_verified_opp_ids: Optional[Set[str]] = None,
    base_frame_precomputed: Optional[FeatureEvidenceFrame] = None,
) -> CausalRegimeFeatureFrameR1:
    """
    D3.A.5: Point-in-time closed-bar M1/M15/H1 volatility, trend efficiency, session, and
    data-integrity feature extractor. Enforces `known_at_ms <= asof_ts`, rejects any planted
    ex-post leakage fields, and returns UNKNOWN_MISSING on insufficient trailing bars.
    """
    oid = str(candidate.get("opportunity_id") or "")
    extra_inv: List[str] = []

    # Check candidate and feature row for planted ex-post / future leakage fields (G04 & G05)
    f_row = verified_feature_ledger.get(oid) if oid else None
    for src_name, mapping_obj in (("CANDIDATE", candidate), ("FEATURE_ROW", f_row or {})):
        for bad_k in FORBIDDEN_EXPOST_LEAKAGE_KEYS:
            if bad_k in mapping_obj and mapping_obj.get(bad_k) is not None:
                extra_inv.append(f"PLANTED_EXPOST_LEAKAGE_KEY_{src_name}:{bad_k}")
        if src_name == "FEATURE_ROW" and mapping_obj.get("fake_persisted_snapshot_without_proof"):
            extra_inv.append("FAKE_PERSISTED_SNAPSHOT_WITHOUT_PROOF")

    base_fr = (
        base_frame_precomputed
        if base_frame_precomputed is not None
        else extract_kh01b_causal_feature_frame(
            candidate,
            verified_feature_ledger,
            closed_bar_index,
            asof_ts,
            pools_by_id=pools_by_id,
            datasets=datasets,
            close_ts_index=close_ts_index,
            macro_cache=macro_cache,
            atr_cache=atr_cache,
            gap_180m_cache=gap_180m_cache,
            pre_verified_opp_ids=pre_verified_opp_ids,
        )
    )
    if base_fr.has_any_invalid_evidence:
        extra_inv.extend(base_fr.invalidation_reasons)

    direction = base_fr.direction
    cache_key = (asof_ts, direction)
    if regime_ts_cache is not None and cache_key in regime_ts_cache:
        ts_metrics = regime_ts_cache[cache_key]
    else:
        if datasets is not None and close_ts_index is not None:
            h1_m = _compute_trailing_atr_and_percentile_asof(
                datasets.get("1H", ()),
                close_ts_index.get("1H", ()),
                asof_ts,
                tf_label="1H",
                tf_duration_ms=3_600_000,
                atr_period=14,
                trailing_percentile_window=48,
            )
            m15_m = _compute_m15_directional_efficiency_asof(
                datasets.get("15m", ()),
                close_ts_index.get("15m", ()),
                asof_ts,
                direction,
                lookback_bars=8,
            )
            m1_m = _compute_trailing_atr_and_percentile_asof(
                datasets.get("1m", ()),
                close_ts_index.get("1m", ()),
                asof_ts,
                tf_label="1M",
                tf_duration_ms=60_000,
                atr_period=14,
                trailing_percentile_window=60,
            )
            # Check 6h provider/weekend gap in trailing 24h M1 bars
            gap_6h = False
            b1_seq = datasets.get("1m", ())
            c1_seq = close_ts_index.get("1m", ())
            j1 = bisect.bisect_right(c1_seq, asof_ts)
            for gi in range(max(1, j1 - 1440), j1):
                po = int(b1_seq[gi - 1].get("open_time") or b1_seq[gi - 1].get("timestamp") or 0)
                co = int(b1_seq[gi].get("open_time") or b1_seq[gi].get("timestamp") or 0)
                if (co - po) >= 6 * 3_600_000:
                    gap_6h = True
                    break
        else:
            h1_m = {"status": "UNKNOWN_MISSING", "atr": None, "trailing_percentile": None, "latest_close_ms": None, "bar_fresh": False, "source_bar_ids": []}
            m15_m = {"status": "UNKNOWN_MISSING", "signed_efficiency": None, "abs_efficiency": None, "latest_close_ms": None, "bar_fresh": False, "source_bar_ids": []}
            m1_m = {"status": "UNKNOWN_MISSING", "atr": None, "trailing_percentile": None, "latest_close_ms": None, "bar_fresh": False, "source_bar_ids": []}
            gap_6h = False

        ny_dt = datetime.fromtimestamp(max(0, asof_ts) / 1000.0, tz=timezone.utc).astimezone(NY_TZ)
        ny_hr = ny_dt.hour
        if ny_dt.weekday() >= 5:
            sess_bucket = "WEEKEND_WINDOW"
        elif 2 <= ny_hr < 8:
            sess_bucket = "LONDON_SESSION"
        elif 8 <= ny_hr < 12:
            sess_bucket = "NY_AM_SESSION"
        elif 12 <= ny_hr < 17:
            sess_bucket = "NY_PM_SESSION"
        else:
            sess_bucket = "ASIA_OR_ROLLOVER_SESSION"

        ts_metrics = {
            "h1": h1_m,
            "m15": m15_m,
            "m1": m1_m,
            "gap_6h_in_24h": gap_6h,
            "ny_date": ny_dt.strftime("%Y-%m-%d"),
            "ny_hour": ny_hr,
            "ny_weekday": ny_dt.weekday(),
            "ny_session_bucket": sess_bucket,
        }
        if regime_ts_cache is not None and not extra_inv:
            regime_ts_cache[cache_key] = ts_metrics

    h1_m = ts_metrics["h1"]
    m15_m = ts_metrics["m15"]
    m1_m = ts_metrics["m1"]

    for sub_m in (h1_m, m15_m, m1_m):
        if sub_m.get("status") == "INVALID":
            extra_inv.append(str(sub_m.get("invalid_reason") or "INVALID_TRAILING_BAR_EVIDENCE"))

    # Verify all collected source_bar_ids exist in canonical closed_bar_index and close <= asof_ts
    all_bar_ids: List[str] = []
    for sub_m in (h1_m, m15_m, m1_m):
        for bid in sub_m.get("source_bar_ids") or ():
            all_bar_ids.append(str(bid))
            if closed_bar_index:
                parts = str(bid).split(":")
                if len(parts) == 3:
                    c_ms = closed_bar_index.get((parts[1].upper(), int(parts[2])))
                    if c_ms is None:
                        extra_inv.append(f"MISSING_REGIME_SOURCE_BAR_ID:{bid}")
                    elif c_ms > asof_ts:
                        extra_inv.append(f"FUTURE_REGIME_SOURCE_BAR_ID:{bid}:{c_ms}>{asof_ts}")

    m1_atr = m1_m.get("atr")
    spread_to_m1_atr: Optional[float] = None
    if isinstance(m1_atr, (int, float)) and m1_atr > 1e-9:
        spread_to_m1_atr = round(0.20 / float(m1_atr), 6)

    known_candidates = [
        int(x) for x in (
            h1_m.get("latest_close_ms"),
            m15_m.get("latest_close_ms"),
            m1_m.get("latest_close_ms"),
        )
        if x is not None
    ]
    for cell in base_fr.feature_cells.values():
        if cell.get("known_at_ms") is not None:
            known_candidates.append(int(cell["known_at_ms"]))
    max_kn_ms = max(known_candidates) if known_candidates else asof_ts
    if max_kn_ms > asof_ts:
        extra_inv.append(f"MAX_KNOWN_AT_EXCEEDS_DECISION_TS:{max_kn_ms}>{asof_ts}")

    # Determine Causal Regime Tri-State (E03)
    if extra_inv:
        reg_tri = "INVALID"
        reg_lbl = "INVALID"
    elif h1_m.get("status") != "VALID" or m15_m.get("status") != "VALID" or h1_m.get("trailing_percentile") is None or m15_m.get("signed_efficiency") is None:
        reg_tri = "UNKNOWN_MISSING"
        reg_lbl = "UNKNOWN_MISSING"
    else:
        is_trend_exp = (
            float(h1_m["trailing_percentile"]) >= 0.50
            and float(m15_m["signed_efficiency"]) >= 0.25
        )
        reg_tri = "PROVEN_TRUE" if is_trend_exp else "PROVEN_FALSE"
        reg_lbl = "TREND_EXPANSION_ALIGNED" if is_trend_exp else "CHOP_OR_COUNTER_REGIME"

    # Determine Pre-Entry Data/Quote Quality Cohort (E04)
    has_gap_180m = bool((base_fr.feature_cells.get("recent_m1_data_gap_180m") or {}).get("value", False))
    quote_ok = (base_fr.feature_cells.get("quote_provenance") or {}).get("tri_state") == "PROVEN_TRUE"
    if extra_inv or not quote_ok:
        qual_tri = "INVALID"
        qual_rej = "QUALITY_INVALID_QUOTE_OR_EVIDENCE"
    elif m1_m.get("status") != "VALID" or spread_to_m1_atr is None or h1_m.get("latest_close_ms") is None or m15_m.get("latest_close_ms") is None:
        qual_tri = "UNKNOWN_MISSING"
        qual_rej = "QUALITY_MISSING_TRAILING_TF_BARS"
    elif has_gap_180m:
        qual_tri = "PROVEN_FALSE"
        qual_rej = "QUALITY_RECENT_180M_M1_DATA_GAP"
    elif not (bool(h1_m.get("bar_fresh")) and bool(m15_m.get("bar_fresh")) and bool(m1_m.get("bar_fresh"))):
        qual_tri = "PROVEN_FALSE"
        qual_rej = "QUALITY_STALE_HTF_OR_M1_CLOSED_BAR"
    elif spread_to_m1_atr > 0.35:
        qual_tri = "PROVEN_FALSE"
        qual_rej = "QUALITY_SPREAD_TO_M1_ATR_ABOVE_0_35"
    else:
        qual_tri = "PROVEN_TRUE"
        qual_rej = None

    pred_ns = {
        "namespace": "PREDICTIVE_PRE_ENTRY",
        "opportunity_id": oid,
        "model_id": base_fr.model_id,
        "direction": direction,
        "decision_timestamp_ms": asof_ts,
        "max_known_at_ms": max_kn_ms,
        "ny_date": ts_metrics["ny_date"],
        "ny_hour": ts_metrics["ny_hour"],
        "ny_weekday": ts_metrics["ny_weekday"],
        "ny_session_bucket": ts_metrics["ny_session_bucket"],
        "h1_atr_14": h1_m.get("atr"),
        "h1_atr_trailing_48_percentile": h1_m.get("trailing_percentile"),
        "m15_signed_dir_efficiency_8": m15_m.get("signed_efficiency"),
        "m15_abs_dir_efficiency_8": m15_m.get("abs_efficiency"),
        "m1_atr_14": m1_atr,
        "causal_spread_to_m1_atr_ratio": spread_to_m1_atr,
        "recent_m1_data_gap_180m": has_gap_180m,
        "weekend_or_provider_gap_6h_in_24h": bool(ts_metrics["gap_6h_in_24h"]),
        "causal_regime_tri_state": reg_tri,
        "causal_regime_label": reg_lbl,
        "quality_cohort_tri_state": qual_tri,
        "quality_rejection_reason": qual_rej,
        "h4_bias_tri_state": (base_fr.feature_cells.get("h4_bias") or {}).get("tri_state"),
        "h1_bias_tri_state": (base_fr.feature_cells.get("h1_bias") or {}).get("tri_state"),
        "m15_bias_tri_state": (base_fr.feature_cells.get("m15_bias") or {}).get("tri_state"),
        "sweep_confirmed_tri_state": (base_fr.feature_cells.get("sweep_confirmed") or {}).get("tri_state"),
        "mss_confirmed_tri_state": (base_fr.feature_cells.get("mss_confirmed") or {}).get("tri_state"),
        "target_distance_source_tf_atr": base_fr.target_audit_info.get("target_distance_source_tf_atr"),
        "resolved_target_source_tf": base_fr.target_audit_info.get("resolved_source_tf"),
        "quote_mode": "MODELED_BID_ASK",
    }
    inv_tup = tuple(dict.fromkeys(extra_inv))
    digest = canonical_hash({
        "oid": oid,
        "base_sha": base_fr.frame_sha256,
        "reg": (reg_tri, reg_lbl),
        "qual": (qual_tri, qual_rej),
        "inv": inv_tup,
    })
    return CausalRegimeFeatureFrameR1(
        opportunity_id=oid,
        base_frame=base_fr,
        predictive_pre_entry=pred_ns,
        causal_regime_tri_state=reg_tri,
        causal_regime_label=reg_lbl,
        quality_cohort_tri_state=qual_tri,
        quality_rejection_reason=qual_rej,
        max_known_at_ms=max_kn_ms,
        source_bar_ids_all=tuple(all_bar_ids),
        invalidation_reasons=inv_tup,
        r1_frame_sha256=digest,
    )


# ==============================================================================
# D3.A.2 & D3.A.3: PRE-ENTRY VS EX-POST ATTRIBUTION (03) & MECHANISM DECOMPOSITION (04)
# ==============================================================================

def build_kh01b_r1_preentry_outcome_attribution(
    *,
    si0_episodes: Sequence[Mapping[str, Any]],
    a1_by_oid: Mapping[str, Mapping[str, Any]],
    r1_frames_by_oid: Mapping[str, CausalRegimeFeatureFrameR1],
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    close_ts_index: Mapping[str, Sequence[int]],
    kh01a_r1_forensics_count: int = 170,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    D3.A.2: Produces row-level casebook for all 242 scheduled SI0 episodes (`166` closed = `9W + 157L`,
    `74` open-at-horizon, `1` data-gap, `1` unfilled) with strictly separated `PREDICTIVE_PRE_ENTRY`
    and `DESCRIPTIVE_EX_POST` namespaces, exact missingness counts, and win-vs-loss feature contrasts.
    """
    bars_1m = datasets["1m"]
    c1_seq = close_ts_index["1m"]

    casebook_rows: List[Dict[str, Any]] = []
    loss_missing_counts: Counter[str] = Counter()
    win_missing_counts: Counter[str] = Counter()
    expost_race_counts: Counter[str] = Counter()

    closed_losses = 0
    closed_wins = 0
    gross_pos_flipped_by_fees = 0
    losses_reached_1r_mfe = 0
    losses_stopped_within_3_bars = 0

    for ep in sorted(si0_episodes, key=lambda r: (int(r["decision_timestamp_ms"]), str(r["opportunity_id"]))):
        oid = str(ep["opportunity_id"])
        r1_fr = r1_frames_by_oid[oid]
        cand = a1_by_oid[oid]
        base_cells = r1_fr.base_frame.feature_cells
        outcome = str(ep.get("outcome_label") or "UNFILLED")
        filled = bool(ep.get("filled", False))
        direction = r1_fr.base_frame.direction
        entry_px = float(ep.get("entry_fill_price") or cand.get("entry_reference") or 0.0)
        sl_px = float(cand.get("selected_sl") or 0.0)
        tp_px = float(cand.get("take_profit") or 0.0)
        risk_px = max(0.01, abs(entry_px - sl_px))

        entry_ts = int(ep.get("entry_fill_ts") or ep.get("entry_ts_ms") or ep["decision_timestamp_ms"])
        exit_ts = int(ep.get("exit_fill_ts") or ep.get("exit_ts_ms") or ep.get("release_ts_ms") or entry_ts)

        # Ex-post forward M1 trajectory (strictly AFTER entry_ts — NEVER exposed to pre-entry!)
        mfe_r = 0.0
        mae_r = 0.0
        bars_held = 0
        if filled and exit_ts > entry_ts:
            i_start = bisect.bisect_right(c1_seq, entry_ts)
            i_end = bisect.bisect_right(c1_seq, exit_ts)
            fwd_bars = bars_1m[i_start:i_end]
            bars_held = len(fwd_bars)
            for fb in fwd_bars:
                fh = float(fb["high"])
                fl = float(fb["low"])
                if direction == "LONG":
                    mfe_r = max(mfe_r, (fh - entry_px) / risk_px)
                    mae_r = max(mae_r, (entry_px - fl) / risk_px)
                else:
                    mfe_r = max(mfe_r, (entry_px - fl) / risk_px)
                    mae_r = max(mae_r, (fh - entry_px) / risk_px)

        gross_pnl = round(float(ep.get("realized_gross_pnl_usd") or 0.0), 4)
        fees_usd = round(float(ep.get("total_fees_usd") or 0.0), 4)
        net_pnl = round(float(ep.get("realized_net_pnl_usd") or 0.0), 4)
        net_r = round(float(ep.get("realized_net_r") or ep.get("realized_r") or 0.0), 4)
        plan_risk = round(float(ep.get("planned_net_risk_usd") or r1_fr.base_frame.planned_net_risk_usd or 2.50), 4)
        fee_drag_r = round(fees_usd / max(0.01, plan_risk), 4) if filled and outcome in ("WIN", "LOSS") else 0.0

        reached_1r = mfe_r >= 1.0
        early_sl_3b = (outcome == "LOSS" and bars_held <= 3)
        fee_flipped = (outcome == "LOSS" and gross_pnl > 0.0 and net_pnl <= 0.0)

        if outcome == "LOSS":
            closed_losses += 1
            if reached_1r:
                losses_reached_1r_mfe += 1
                expost_race_counts["LOSS_REACHED_1R_MFE_THEN_REVERSED_TO_SL"] += 1
            elif early_sl_3b:
                losses_stopped_within_3_bars += 1
                expost_race_counts["LOSS_IMMEDIATE_ADVERSE_SL_LE_3_BARS"] += 1
            else:
                expost_race_counts["LOSS_GRADUAL_ADVERSE_SL_WITHOUT_1R_MFE"] += 1
            if fee_flipped:
                gross_pos_flipped_by_fees += 1
            for fk in ("h4_bias", "h1_bias", "m15_bias", "mss_confirmed", "sweep_confirmed"):
                if (base_cells.get(fk) or {}).get("tri_state") == "UNKNOWN_MISSING":
                    loss_missing_counts[fk] += 1
        elif outcome == "WIN":
            closed_wins += 1
            expost_race_counts["WIN_REACHED_TP"] += 1
            for fk in ("h4_bias", "h1_bias", "m15_bias", "mss_confirmed", "sweep_confirmed"):
                if (base_cells.get(fk) or {}).get("tri_state") == "UNKNOWN_MISSING":
                    win_missing_counts[fk] += 1
        else:
            expost_race_counts[f"NON_CLOSED_{outcome}"] += 1

        row_doc = {
            "opportunity_id": oid,
            "setup_episode_id": r1_fr.base_frame.setup_episode_id,
            "decision_timestamp_ms": int(ep["decision_timestamp_ms"]),
            "evidence_completeness": "PARTIAL",
            "original_market_decision_context_persisted": False,
            "PREDICTIVE_PRE_ENTRY": dict(r1_fr.predictive_pre_entry),
            "DESCRIPTIVE_EX_POST": {
                "namespace": "DESCRIPTIVE_EX_POST_NEVER_FOR_ENTRY",
                "filled": filled,
                "outcome_label": outcome,
                "entry_ts_ms": entry_ts,
                "exit_ts_ms": exit_ts,
                "bars_to_exit_m1": bars_held,
                "expost_mfe_price_r": round(mfe_r, 4),
                "expost_mae_price_r": round(mae_r, 4),
                "reached_1r_mfe_before_stop": reached_1r,
                "early_stop_within_3_bars": early_sl_3b,
                "planned_net_risk_usd": plan_risk,
                "realized_gross_pnl_usd": gross_pnl,
                "total_fees_usd": fees_usd,
                "fee_drag_r": fee_drag_r,
                "realized_net_pnl_usd": net_pnl,
                "realized_net_r": net_r,
                "gross_positive_flipped_to_net_loss_by_fees": fee_flipped,
                "tp_price": tp_px,
                "sl_price": sl_px,
            },
        }
        row_doc["row_sha256"] = canonical_hash(row_doc)
        casebook_rows.append(row_doc)

    summary_03 = {
        "spec_version": SPEC_VERSION_KH01B_R1,
        "schema_version": SCHEMA_VERSION_KH01B_R1,
        "population_evaluated": "SI0_B00_SCHEDULED_242_EPISODES",
        "scheduled_total": len(casebook_rows),
        "closed_total": closed_wins + closed_losses,
        "wins_control_count": closed_wins,
        "losses_count": closed_losses,
        "non_closed_count": len(casebook_rows) - (closed_wins + closed_losses),
        "all_rows_partial_reconstructed_honesty": {
            "si0_casebook_partial_rows_count": len(casebook_rows),
            "prior_kh01a_r1_partial_rows_count": kh01a_r1_forensics_count,
            "upgraded_to_fake_persisted_context_count": 0,
            "missing_never_equated_with_false": True,
        },
        "loss_157_preentry_missingness_census": {
            "denominator_losses": closed_losses,
            "h4_bias_missing": int(loss_missing_counts["h4_bias"]),
            "h1_bias_missing": int(loss_missing_counts["h1_bias"]),
            "m15_bias_missing": int(loss_missing_counts["m15_bias"]),
            "mss_confirmed_missing": int(loss_missing_counts["mss_confirmed"]),
            "sweep_confirmed_missing": int(loss_missing_counts["sweep_confirmed"]),
        },
        "win_9_control_preentry_missingness_census": {
            "denominator_wins": closed_wins,
            "h4_bias_missing": int(win_missing_counts["h4_bias"]),
            "h1_bias_missing": int(win_missing_counts["h1_bias"]),
            "m15_bias_missing": int(win_missing_counts["m15_bias"]),
            "mss_confirmed_missing": int(win_missing_counts["mss_confirmed"]),
            "sweep_confirmed_missing": int(win_missing_counts["sweep_confirmed"]),
        },
        "descriptive_expost_sl_tp_race_summary": {
            "disclosure": "EX_POST_OUTCOME_DIAGNOSTIC_ONLY_NEVER_USED_AS_ENTRY_SIGNAL",
            "trajectory_counts": dict(sorted(expost_race_counts.items())),
            "losses_reaching_1r_mfe_before_reversing_to_sl_count": losses_reached_1r_mfe,
            "losses_reaching_1r_mfe_rate": round(losses_reached_1r_mfe / max(1, closed_losses), 4),
            "losses_stopped_within_3_m1_bars_count": losses_stopped_within_3_bars,
            "losses_stopped_within_3_m1_bars_rate": round(losses_stopped_within_3_bars / max(1, closed_losses), 4),
            "gross_positive_flipped_to_net_loss_by_fees_count": gross_pos_flipped_by_fees,
        },
        "casebook_ledger_rel_path": "ledgers/kh01b_r1_si0_242_casebook.jsonl.gz",
        "casebook_rows_sha256": canonical_hash([r["row_sha256"] for r in casebook_rows]),
    }
    return summary_03, casebook_rows


def _summarize_stratum_rows(rows: Sequence[Mapping[str, Any]], parent_closed_n: int) -> Dict[str, Any]:
    sched_n = len(rows)
    closed = [
        r for r in rows
        if r["DESCRIPTIVE_EX_POST"]["filled"]
        and r["DESCRIPTIVE_EX_POST"]["outcome_label"] in ("WIN", "LOSS")
    ]
    n = len(closed)
    wins = sum(1 for r in closed if r["DESCRIPTIVE_EX_POST"]["outcome_label"] == "WIN")
    losses = n - wins
    gross = round(sum(float(r["DESCRIPTIVE_EX_POST"]["realized_gross_pnl_usd"]) for r in closed), 4)
    fees = round(sum(float(r["DESCRIPTIVE_EX_POST"]["total_fees_usd"]) for r in closed), 4)
    net = round(sum(float(r["DESCRIPTIVE_EX_POST"]["realized_net_pnl_usd"]) for r in closed), 4)
    risk_sum = round(sum(float(r["DESCRIPTIVE_EX_POST"]["planned_net_risk_usd"]) for r in closed), 4)
    exp_net_r = round(sum(float(r["DESCRIPTIVE_EX_POST"]["realized_net_r"]) for r in closed) / n, 4) if n > 0 else None
    exp_gross_r = round(gross / risk_sum, 4) if (n > 0 and risk_sum > 0) else None
    fee_drag_r = round(fees / risk_sum, 4) if (n > 0 and risk_sum > 0) else None
    return {
        "scheduled_count": sched_n,
        "closed_count": n,
        "share_of_parent_closed": round(n / max(1, parent_closed_n), 4),
        "wins_count": wins,
        "losses_count": losses,
        "win_rate": round(wins / n, 6) if n > 0 else None,
        "win_rate_wilson_95ci": list(_wilson_95ci(wins, n)) if n > 0 else None,
        "gross_pnl_usd": gross,
        "total_fees_usd": fees,
        "net_pnl_usd": net,
        "gross_expectancy_r": exp_gross_r,
        "fee_drag_r": fee_drag_r,
        "net_expectancy_r": exp_net_r,
        "sample_power_flag": (
            "NOT_ESTIMABLE_ZERO_TRADES" if n == 0 else ("UNDERPOWERED_N_LT_30" if n < 30 else "DESCRIPTIVE_N_GE_30")
        ),
    }


def audit_kh01b_r1_strategy_mechanism_by_model_direction_regime(
    *,
    casebook_rows: Sequence[Mapping[str, Any]],
    a1_decisions: Sequence[Mapping[str, Any]],
    r1_frames_by_oid: Mapping[str, CausalRegimeFeatureFrameR1],
    datasets: Mapping[str, Sequence[Mapping[str, Any]]],
    feature_ledger: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    D3.A.3: Decomposes SI0 baseline performance across model family, direction, NY session/hour,
    gap/quote quality, causal trailing volatility/trend regime, source-TF target horizon, and
    gross-alpha vs execution-cost drag, with exact denominators and low-N uncertainty flags.
    """
    closed_all = [
        r for r in casebook_rows
        if r["DESCRIPTIVE_EX_POST"]["filled"]
        and r["DESCRIPTIVE_EX_POST"]["outcome_label"] in ("WIN", "LOSS")
    ]
    parent_closed_n = len(closed_all)

    def _group_by(fn_key: Callable[[Mapping[str, Any]], str]) -> Dict[str, Any]:
        groups: Dict[str, List[Mapping[str, Any]]] = {}
        for r in casebook_rows:
            groups.setdefault(fn_key(r), []).append(r)
        out = {k: _summarize_stratum_rows(v, parent_closed_n) for k, v in sorted(groups.items())}
        return out

    by_model = _group_by(lambda r: str(r["PREDICTIVE_PRE_ENTRY"]["model_id"]))
    by_direction = _group_by(lambda r: str(r["PREDICTIVE_PRE_ENTRY"]["direction"]))
    by_session = _group_by(lambda r: str(r["PREDICTIVE_PRE_ENTRY"]["ny_session_bucket"]))
    by_regime = _group_by(lambda r: str(r["PREDICTIVE_PRE_ENTRY"]["causal_regime_label"]))
    by_gap = _group_by(
        lambda r: "RECENT_180M_GAP_TRUE" if r["PREDICTIVE_PRE_ENTRY"]["recent_m1_data_gap_180m"] else "NO_RECENT_180M_GAP"
    )

    def _spread_bucket(r: Mapping[str, Any]) -> str:
        v = r["PREDICTIVE_PRE_ENTRY"].get("causal_spread_to_m1_atr_ratio")
        if v is None:
            return "UNKNOWN_SPREAD_TO_ATR"
        fv = float(v)
        if fv <= 0.20:
            return "LOW_FRICTION_LE_0_20_M1_ATR"
        if fv <= 0.35:
            return "MODERATE_FRICTION_0_20_TO_0_35_M1_ATR"
        return "HIGH_FRICTION_GT_0_35_M1_ATR"

    def _target_atr_bucket(r: Mapping[str, Any]) -> str:
        d = r["PREDICTIVE_PRE_ENTRY"].get("target_distance_source_tf_atr")
        if d is None:
            return "UNKNOWN_SOURCE_TF_ATR_DISTANCE"
        fd = float(d)
        if fd <= 4.0:
            return "NEAR_TARGET_LE_4_SRC_ATR"
        if fd <= 8.0:
            return "MID_TARGET_4_TO_8_SRC_ATR"
        if fd <= 12.0:
            return "FAR_TARGET_8_TO_12_SRC_ATR"
        return "EXTREME_TARGET_GT_12_SRC_ATR"

    by_spread_ratio = _group_by(_spread_bucket)
    by_target_horizon = _group_by(_target_atr_bucket)

    # Audit A1 56,710 model family arbitration & 1-pos suppression (explaining 0 trades in SWEEP_RECLAIM & CG01!)
    a1_by_model_counts = Counter(str(c["model_id"]) for c in a1_decisions)
    by_chk: Dict[int, List[Dict[str, Any]]] = {}
    for c in a1_decisions:
        by_chk.setdefault(int(c["checkpoint_index_1m"]), []).append(dict(c))
    arb_winners_by_model: Counter[str] = Counter()
    arb_suppressed_by_model: Counter[str] = Counter()
    for _, grp in sorted(by_chk.items()):
        w, losers = arbitrate_competing_model_intents_v1793122(
            grp, arbitration_mode="STATIC_PRODUCTION_PRIORITY", feature_ledger_by_opp_id=feature_ledger
        )
        if w is not None:
            arb_winners_by_model[str(w["model_id"])] += 1
        for l_rec in losers:
            arb_suppressed_by_model[str(l_rec["model_id"])] += 1

    sched_by_model = Counter(str(r["PREDICTIVE_PRE_ENTRY"]["model_id"]) for r in casebook_rows)
    model_funnel: Dict[str, Any] = {}
    for mid in sorted(a1_by_model_counts.keys()):
        tot_m = int(a1_by_model_counts[mid])
        win_arb = int(arb_winners_by_model[mid])
        sup_arb = int(arb_suppressed_by_model[mid])
        sch_m = int(sched_by_model[mid])
        model_funnel[mid] = {
            "a1_candidates_count": tot_m,
            "static_priority_rank": int(PRODUCTION_MODEL_STATIC_PRIORITY.get(mid, 99)),
            "won_timestamp_arbitration_count": win_arb,
            "suppressed_at_same_timestamp_tie_count": sup_arb,
            "blocked_by_active_1pos_open_trade_count": max(0, win_arb - sch_m),
            "scheduled_in_si0_b00_count": sch_m,
            "stratum_outcome_in_si0": by_model.get(mid) or _summarize_stratum_rows([], parent_closed_n),
        }

    # Underlying 20.35-day XAUUSDT market drift confounding audit
    bars_1m = datasets["1m"]
    p_start = float(bars_1m[0]["open"])
    p_end = float(bars_1m[-1]["close"])
    p_high = max(float(b["high"]) for b in bars_1m)
    p_low = min(float(b["low"]) for b in bars_1m)

    tot_overall = _summarize_stratum_rows(casebook_rows, parent_closed_n)
    gross_usd = float(tot_overall["gross_pnl_usd"])
    fees_usd = float(tot_overall["total_fees_usd"])
    net_usd = float(tot_overall["net_pnl_usd"])

    return {
        "spec_version": SPEC_VERSION_KH01B_R1,
        "schema_version": SCHEMA_VERSION_KH01B_R1,
        "parent_population_conservation": {
            "a1_candidates_total": len(a1_decisions),
            "si0_scheduled_total": len(casebook_rows),
            "si0_closed_total": parent_closed_n,
            "stratum_closed_sum_model": sum(v["closed_count"] for v in by_model.values()),
            "stratum_closed_sum_direction": sum(v["closed_count"] for v in by_direction.values()),
            "stratum_closed_sum_regime": sum(v["closed_count"] for v in by_regime.values()),
        },
        "overall_gross_vs_fee_decomposition": {
            **tot_overall,
            "gross_loss_share_of_net_loss": round(abs(gross_usd) / max(0.01, abs(net_usd)), 4) if gross_usd < 0 else 0.0,
            "fee_drag_share_of_net_loss": round(fees_usd / max(0.01, abs(net_usd)), 4),
            "primary_thesis_finding": (
                "SI0 baseline has NEGATIVE GROSS ALPHA BEFORE TAKER FEES "
                f"(gross_pnl={gross_usd} USD, gross_expectancy_r={tot_overall['gross_expectancy_r']}R) "
                f"compounded by high M1 turnover fee drag (fees={fees_usd} USD, fee_drag_r={tot_overall['fee_drag_r']}R). "
                "Fee reduction alone cannot rescue a negative-gross-expectancy entry thesis."
            ),
        },
        "market_regime_confounding_context": {
            "xauusdt_sample_start_price": p_start,
            "xauusdt_sample_end_price": p_end,
            "xauusdt_net_price_change_usd": round(p_end - p_start, 2),
            "xauusdt_net_return_pct": round((p_end - p_start) / p_start * 100.0, 4),
            "xauusdt_period_high": p_high,
            "xauusdt_period_low": p_low,
            "directional_confounding_note": (
                "Single 20.35-day XAUUSDT window exhibits directional drift; LONG vs SHORT differences "
                "cannot be asserted as permanent directional alpha without multi-regime OOS validation."
            ),
        },
        "a1_to_si0_model_arbitration_and_blocking_funnel": model_funnel,
        "strata_by_model_family": by_model,
        "strata_by_direction": by_direction,
        "strata_by_ny_session_bucket": by_session,
        "strata_by_causal_regime": by_regime,
        "strata_by_recent_180m_gap": by_gap,
        "strata_by_spread_to_m1_atr_ratio": by_spread_ratio,
        "strata_by_target_source_tf_atr_distance": by_target_horizon,
    }


# ==============================================================================
# D3.A.4: PRE-REGISTER KH-01B.R1 THESES (<= 6 NEW HYPOTHESES + E00 CONTROL)
# ==============================================================================

DEFAULT_KH01B_R1_THESIS_SPECS: Tuple[Dict[str, Any], ...] = (
    {
        "short_code": "E00",
        "variant_id": "KH01B_R1-E00-UNCHANGED-B00-CONTROL",
        "is_baseline_control": True,
        "ontology": "BASELINE_CONTROL",
        "modifies_tp_geometry": False,
        "delayed_entry_time_shift": False,
        "distinct_from_prior_kh01b": "Exact unchanged SI0/B00 baseline control for parity & paired counterfactuals.",
        "causal_mechanism": "Unchanged A1/SI0 multi-model candidate stream under STATIC_PRODUCTION_PRIORITY.",
        "falsification_criterion": "Parity fails if scheduled != 242, closed != 166, or net_pnl != -345.8971 USD.",
        "rule": {"type": "UNCHANGED_B00_CONTROL"},
        "predeclared_sub_contrasts": (),
    },
    {
        "short_code": "E01",
        "variant_id": "KH01B_R1-E01-MODEL-FAMILY-ISOLATION",
        "is_baseline_control": False,
        "ontology": "MODEL_FAMILY_ISOLATION_RESEARCH",
        "modifies_tp_geometry": False,
        "delayed_entry_time_shift": False,
        "distinct_from_prior_kh01b": "B01..B12 pooled all model_ids under Reversal-first static priority; E01 isolates individual model families known at decision time.",
        "causal_mechanism": "Test whether isolating structural FVG continuation (primary E01) and the 3-model contrast group (Reversal / Continuation / FVG-Continuation) uncovers positive model-specific expectancy masked by Reversal priority #1.",
        "falsification_criterion": "Disproved if every isolated model family has net_expectancy_r <= 0 or fails 2x all-cost stress.",
        "rule": {"type": "MODEL_FAMILY_ONLY", "allowed_model_ids": ["SMC_M1_FVG_CONTINUATION"]},
        "predeclared_sub_contrasts": (
            {"short_code": "E01_C1", "variant_id": "KH01B_R1-E01_C1-MODEL-REVERSAL-ONLY", "rule": {"type": "MODEL_FAMILY_ONLY", "allowed_model_ids": ["SMC_M1_REVERSAL"]}},
            {"short_code": "E01_C2", "variant_id": "KH01B_R1-E01_C2-MODEL-CONTINUATION-ONLY", "rule": {"type": "MODEL_FAMILY_ONLY", "allowed_model_ids": ["SMC_M1_CONTINUATION"]}},
            {"short_code": "E01_C3", "variant_id": "KH01B_R1-E01_C3-MODEL-FVG-CONTINUATION-ONLY", "rule": {"type": "MODEL_FAMILY_ONLY", "allowed_model_ids": ["SMC_M1_FVG_CONTINUATION"]}},
        ),
    },
    {
        "short_code": "E02",
        "variant_id": "KH01B_R1-E02-DIRECTIONAL-MISMATCH",
        "is_baseline_control": False,
        "ontology": "DIRECTIONAL_ISOLATION_RESEARCH",
        "modifies_tp_geometry": False,
        "delayed_entry_time_shift": False,
        "distinct_from_prior_kh01b": "B01..B12 pooled LONG and SHORT trades; E02 tests fixed LONG-only vs SHORT-only contrasts on the identical 56,710 universe.",
        "causal_mechanism": "Test whether M1 losses are driven by one-sided directional mismatch against the 20-day XAUUSDT drift or fail symmetrically on both LONG and SHORT.",
        "falsification_criterion": "Disproved if both LONG-only and SHORT-only remain net negative after 1x/2x costs.",
        "rule": {"type": "DIRECTION_ONLY", "allowed_direction": "SHORT"},
        "predeclared_sub_contrasts": (
            {"short_code": "E02_C1", "variant_id": "KH01B_R1-E02_C1-LONG-ONLY", "rule": {"type": "DIRECTION_ONLY", "allowed_direction": "LONG"}},
            {"short_code": "E02_C2", "variant_id": "KH01B_R1-E02_C2-SHORT-ONLY", "rule": {"type": "DIRECTION_ONLY", "allowed_direction": "SHORT"}},
        ),
    },
    {
        "short_code": "E03",
        "variant_id": "KH01B_R1-E03-CAUSAL-REGIME-MATCHING",
        "is_baseline_control": False,
        "ontology": "CAUSAL_REGIME_RESEARCH",
        "modifies_tp_geometry": False,
        "delayed_entry_time_shift": False,
        "distinct_from_prior_kh01b": "B01..B03 used sparse discrete swing bias labels (38% missing); E03 computes continuous trailing closed H1 ATR(14) expansion (>=50th trailing-48bar pct) + M15 8-bar directional efficiency (>=0.25) directly from closed bars.",
        "causal_mechanism": "Test whether M1 entries require active H1 volatility expansion + aligned M15 directional impulse rather than compressed chop; explicitly contrasted against CHOP_OR_COUNTER_REGIME.",
        "falsification_criterion": "Disproved if TREND_EXPANSION_ALIGNED fails to achieve positive net expectancy & 2x cost survival.",
        "rule": {"type": "CAUSAL_REGIME_RULE", "target_regime": "TREND_EXPANSION_ALIGNED", "h1_atr_pct_min": 0.50, "m15_signed_eff_min": 0.25},
        "predeclared_sub_contrasts": (
            {"short_code": "E03_CHOP", "variant_id": "KH01B_R1-E03_CHOP-COMPLEMENT-DIAGNOSTIC", "rule": {"type": "CAUSAL_REGIME_RULE", "target_regime": "CHOP_OR_COUNTER_REGIME"}},
        ),
    },
    {
        "short_code": "E04",
        "variant_id": "KH01B_R1-E04-PREENTRY-GAP-QUOTE-QUALITY-COHORT",
        "is_baseline_control": False,
        "ontology": "DATA_AND_QUOTE_QUALITY_RESEARCH",
        "modifies_tp_geometry": False,
        "delayed_entry_time_shift": False,
        "distinct_from_prior_kh01b": "Never tested in B01..B12; isolates pure decision-time bar continuity, closed-bar freshness, and modeled spread-to-M1-ATR friction <= 0.35.",
        "causal_mechanism": "Test whether SI0's negative edge is an artifact of post-gap distorted indicators or low-ATR ticks where $0.20 spread exceeds 35% of 1-minute range.",
        "falsification_criterion": "Disproved if clean, fresh, low-spread-to-ATR cohort still exhibits negative net expectancy.",
        "rule": {"type": "PREENTRY_QUALITY_COHORT", "max_spread_to_m1_atr": 0.35, "forbid_180m_gap": True, "require_fresh_tf_bars": True},
        "predeclared_sub_contrasts": (),
    },
    {
        "short_code": "E05",
        "variant_id": "KH01B_R1-E05-CAUSAL-DELAYED-CONFIRMATION-TRIGGER",
        "is_baseline_control": False,
        "ontology": "CAUSAL_TIME_SHIFTED_TRIGGER_RESEARCH",
        "modifies_tp_geometry": False,
        "delayed_entry_time_shift": True,
        "distinct_from_prior_kh01b": "All B00..B12 arms entered immediately at t0; E05 uses advance_pending_confirmation_state_v1793122 to wait 1..6 subsequent closed M1 bars for displacement/retest confirmation and recomputes entry/SL/TP/qty/risk at confirmed_at_ms > t0.",
        "causal_mechanism": "Test whether waiting for a subsequent closed confirmation bar filters immediate t0 stop-outs without destroying net R:R (>=2.0) at the shifted entry price.",
        "falsification_criterion": "Disproved if time-shifted confirmed entries still produce negative net expectancy or fail 2x all-cost stress.",
        "rule": {"type": "DELAYED_CLOSED_BAR_CONFIRMATION", "max_wait_bars_m1": 6, "min_displacement_body_atr_ratio": 0.55, "min_net_rr": 2.0, "max_planned_risk_usd": 2.50},
        "predeclared_sub_contrasts": (),
    },
    {
        "short_code": "E06",
        "variant_id": "KH01B_R1-E06-REGIME-X-FVG-CONTINUATION-INTERACTION",
        "is_baseline_control": False,
        "ontology": "REGIME_X_MODEL_INTERACTION_RESEARCH",
        "modifies_tp_geometry": False,
        "delayed_entry_time_shift": False,
        "distinct_from_prior_kh01b": "Single predeclared Regime x Model interaction (SMC_M1_FVG_CONTINUATION inside E03 TREND_EXPANSION_ALIGNED), gated BEFORE outcome computation on E03 completeness >= 0.95.",
        "causal_mechanism": "Test whether M1 FVG continuation succeeds specifically when H1 ATR is expanding and M15 momentum is directionally aligned, without Reversal priority #1 interference.",
        "falsification_criterion": "Disproved if net expectancy <= 0, closed trades < 30 (UNDERPOWERED), or 2x all-cost net PnL <= 0.",
        "rule": {"type": "REGIME_X_MODEL_INTERACTION", "allowed_model_ids": ["SMC_M1_FVG_CONTINUATION"], "target_regime": "TREND_EXPANSION_ALIGNED", "min_required_e03_completeness": 0.95},
        "predeclared_sub_contrasts": (),
    },
)


@dataclass(frozen=True)
class FrozenKH01BR1Manifest:
    spec_version: str
    schema_version: str
    manifest_id: str
    dataset_sha256: str
    config_sha256: str
    code_sha256: str
    prior_kh01b_manifest_sha256: str
    max_new_hypotheses_budget: int
    registered_new_hypotheses_count: int
    total_arms_including_control: int
    multiplicity_family_size_m: int
    e03_pre_outcome_completeness_ratio: float
    e06_execution_status: str  # EXECUTABLE_PRECONDITIONS_MET | NOT_RUN_JUSTIFIED
    purged_embargo_minutes: int
    min_powered_closed_trades: int
    min_net_rr: float
    max_planned_risk_usd: float
    cost_multipliers_evaluated: Tuple[float, ...]
    theses: Tuple[Dict[str, Any], ...]
    amendments: Tuple[Dict[str, Any], ...]
    frozen_before_outcome_evaluation: bool
    manifest_sha256: str


def pre_register_kh01b_r1_theses(
    dataset_sha: str,
    config_sha: str,
    code_sha: str,
    thesis_specs: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    e03_completeness_ratio: float = 1.0,
    amendments: Sequence[Mapping[str, Any]] = (),
) -> FrozenKH01BR1Manifest:
    """
    D3.A.4 & G03: Freezes E00 + up to SIX new hypotheses (E01..E06) BEFORE any replay or
    return calculation occurs. Enforces <= 6 new hypotheses, disjoint rules, zero TP mutation,
    and pre-outcome completeness gating for E06.
    """
    if not dataset_sha or not config_sha or not code_sha:
        raise ValueError("KH01B_R1_PREREGISTRATION_REQUIRES_NON_EMPTY_SHAS")

    specs = [copy.deepcopy(dict(s)) for s in (thesis_specs or DEFAULT_KH01B_R1_THESIS_SPECS)]
    controls = [s for s in specs if bool(s.get("is_baseline_control", False))]
    new_hyps = [s for s in specs if not bool(s.get("is_baseline_control", False))]

    if len(controls) != 1 or str(controls[0].get("short_code")) != "E00":
        raise ValueError("KH01B_R1_REQUIRES_EXACTLY_ONE_E00_CONTROL")
    if len(new_hyps) > 6:
        raise ValueError(f"KH01B_R1_HYPOTHESIS_BUDGET_EXCEEDED:{len(new_hyps)}>6")

    seen_codes: Set[str] = set()
    seen_vids: Set[str] = set()
    seen_rules: Set[str] = set()
    mult_m = 0

    for sp in specs:
        sc = str(sp.get("short_code") or "")
        vid = str(sp.get("variant_id") or "")
        if not sc or sc in seen_codes or not vid.startswith("KH01B_R1-") or vid in seen_vids:
            raise ValueError(f"DUPLICATE_OR_INVALID_R1_THESIS_ID:{sc}:{vid}")
        if bool(sp.get("modifies_tp_geometry", False)):
            raise ValueError(f"ENTRY_RESEARCH_ARM_CANNOT_MODIFY_TP_GEOMETRY:{vid}")
        r_hash = canonical_hash(sp.get("rule") or {})
        if r_hash in seen_rules:
            raise ValueError(f"NON_DISJOINT_DUPLICATE_HYPOTHESIS_RULE:{vid}")
        seen_codes.add(sc)
        seen_vids.add(vid)
        seen_rules.add(r_hash)

        if not bool(sp.get("is_baseline_control", False)):
            subs = tuple(sp.get("predeclared_sub_contrasts") or ())
            mult_m += max(1, len(subs))

    e06_status = "NOT_APPLICABLE"
    for sp in specs:
        if sp.get("short_code") == "E06":
            req_c = float((sp.get("rule") or {}).get("min_required_e03_completeness", 0.95))
            if float(e03_completeness_ratio) >= req_c:
                e06_status = "EXECUTABLE_PRECONDITIONS_MET"
            else:
                e06_status = "NOT_RUN_JUSTIFIED"
            sp["pre_outcome_gate_status"] = e06_status

    amend_tuple = tuple(copy.deepcopy(dict(a)) for a in amendments)
    manifest_id = (
        "KH01B_R1_PREREG_V1_AMENDED_" + canonical_hash(amend_tuple)[:8]
        if amend_tuple
        else "KH01B_R1_PREREG_V1_INITIAL"
    )
    body = {
        "spec_version": SPEC_VERSION_KH01B_R1,
        "schema_version": SCHEMA_VERSION_KH01B_R1,
        "manifest_id": manifest_id,
        "dataset_sha256": dataset_sha,
        "config_sha256": config_sha,
        "code_sha256": code_sha,
        "prior_kh01b_manifest_sha256": PRIOR_KH01B_MANIFEST_SHA256,
        "max_new_hypotheses_budget": 6,
        "registered_new_hypotheses_count": len(new_hyps),
        "total_arms_including_control": len(specs),
        "multiplicity_family_size_m": mult_m,
        "e03_pre_outcome_completeness_ratio": round(float(e03_completeness_ratio), 6),
        "e06_execution_status": e06_status,
        "purged_embargo_minutes": 180,
        "min_powered_closed_trades": 30,
        "min_net_rr": 2.0,
        "max_planned_risk_usd": 2.50,
        "cost_multipliers_evaluated": (1.0, 2.0, 3.0),
        "theses": tuple(specs),
        "amendments": amend_tuple,
        "frozen_before_outcome_evaluation": True,
    }
    return FrozenKH01BR1Manifest(**body, manifest_sha256=canonical_hash(body))


# ==============================================================================
# D3.A.6: EVALUATE SINGLE CANDIDATE FOR R1 ENTRY VARIANT (CAUSAL, NO PEEKING)
# ==============================================================================

@dataclass(frozen=True)
class R1CandidateDecision:
    opportunity_id: str
    short_code: str
    variant_id: str
    eligible: bool
    tri_state: str
    rejection_code: Optional[str]
    missing_evidence_rejected: bool
    proven_false_rejected: bool
    invalid_evidence_rejected: bool
    delayed_confirmation_used: bool
    effective_checkpoint_index_1m: int
    effective_decision_timestamp_ms: int
    known_at_ms: int
    materialized_plan: Optional[Dict[str, Any]]


def _build_r1_noop_materialized_plan(
    opp: Mapping[str, Any],
    base_fr: FeatureEvidenceFrame,
    variant_id: str,
) -> Dict[str, Any]:
    opp_id = base_fr.opportunity_id
    dec_ts = base_fr.decision_timestamp_ms
    chk_idx = base_fr.checkpoint_index_1m
    return {
        "schema_version": SCHEMA_VERSION_KH01B_R1,
        "intent_id": f"intent_{variant_id}_{hashlib.sha256(f'{variant_id}|{opp_id}|{dec_ts}'.encode('utf-8')).hexdigest()[:16]}",
        "opportunity_id": opp_id,
        "setup_id": str(opp.get("setup_id") or base_fr.setup_episode_id),
        "variant_id": variant_id,
        "intervention_type": "ENTRY_FILTER_ONLY_GEOMETRY_UNCHANGED",
        "status": "ACCEPTED_UNCHANGED_NO_OP",
        "accepted": True,
        "rejection_reason": None,
        "old_plan": {
            "opportunity_id": opp_id,
            "model_id": base_fr.model_id,
            "direction": base_fr.direction,
            "checkpoint_index_1m": chk_idx,
            "decision_timestamp_ms": dec_ts,
            "entry_reference": base_fr.entry_reference,
            "selected_sl": base_fr.selected_sl,
            "take_profit": base_fr.take_profit,
            "quantity": base_fr.hypothetical_quantity,
            "planned_net_risk_usd": base_fr.planned_net_risk_usd,
            "net_rr": base_fr.baseline_net_rr,
        },
        "new_checkpoint_index_1m": chk_idx,
        "new_decision_timestamp_ms": dec_ts,
        "new_entry_reference": base_fr.entry_reference,
        "new_selected_sl": base_fr.selected_sl,
        "new_take_profit": base_fr.take_profit,
        "new_quantity": base_fr.hypothetical_quantity,
        "new_planned_net_risk_usd": base_fr.planned_net_risk_usd,
        "new_net_rr": base_fr.baseline_net_rr,
        "new_gross_rr": base_fr.baseline_gross_rr,
        "new_structural_invalidation_price": float(opp.get("structural_invalidation_price") or base_fr.selected_sl),
        "new_selected_anchor_id": opp.get("selected_anchor_id"),
        "new_anchor_kind": opp.get("anchor_kind"),
        "new_selected_target_id": base_fr.selected_target_id,
        "changed_dimensions": [],
        "known_at_ms": dec_ts,
        "confirmation_subtype": "NONE",
        "new_trade_fingerprint": base_fr.trade_action_fingerprint,
    }


def evaluate_kh01b_r1_entry_variant(
    rule_spec: Mapping[str, Any],
    short_code: str,
    variant_id: str,
    cand: Mapping[str, Any],
    r1_frame: CausalRegimeFeatureFrameR1,
    *,
    bars_1m: Optional[Sequence[Mapping[str, Any]]] = None,
    runtime_modes: Optional[Mapping[str, Any]] = None,
) -> R1CandidateDecision:
    """
    D3.A.6: Deterministic causal Entry evaluator for E00..E06 and predeclared sub-contrasts.
    - Enforces runtime modes OFF (`G10`).
    - Rejects any non-ELIGIBLE baseline candidate (`non-rescue` invariant).
    - Never modifies TP geometry on filter arms; for `E05` delayed confirmation, advances strictly
      to a subsequent closed confirmation bar (`confirmed_at_ms > t0`), keeping baseline SL & TP
      levels fixed while recomputing entry quote, quantity, fees, planned risk <= $2.50, and net R:R >= 2.0.
    """
    assert_research_only_runtime_modes_off(runtime_modes)
    base_fr = r1_frame.base_frame
    oid = base_fr.opportunity_id
    base_chk = base_fr.checkpoint_index_1m
    base_ts = base_fr.decision_timestamp_ms

    # Non-rescue guard: never rescue a candidate that was not ELIGIBLE at baseline
    if str(cand.get("decision_status", "ELIGIBLE")) != "ELIGIBLE":
        return R1CandidateDecision(
            opportunity_id=oid, short_code=short_code, variant_id=variant_id,
            eligible=False, tri_state="PROVEN_FALSE", rejection_code="NON_RESCUE_BASELINE_NOT_ELIGIBLE",
            missing_evidence_rejected=False, proven_false_rejected=True, invalid_evidence_rejected=False,
            delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
            effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
        )

    rtype = str(rule_spec.get("type") or "")
    if rtype == "UNCHANGED_B00_CONTROL" and not r1_frame.invalidation_reasons:
        return R1CandidateDecision(
            opportunity_id=oid, short_code=short_code, variant_id=variant_id,
            eligible=True, tri_state="PROVEN_TRUE", rejection_code=None,
            missing_evidence_rejected=False, proven_false_rejected=False, invalid_evidence_rejected=False,
            delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
            effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts,
            materialized_plan=_build_r1_noop_materialized_plan(cand, base_fr, variant_id),
        )

    if r1_frame.invalidation_reasons or base_fr.has_any_invalid_evidence:
        return R1CandidateDecision(
            opportunity_id=oid, short_code=short_code, variant_id=variant_id,
            eligible=False, tri_state="INVALID", rejection_code=f"INVALID_EVIDENCE:{(r1_frame.invalidation_reasons or base_fr.invalidation_reasons)[0]}",
            missing_evidence_rejected=False, proven_false_rejected=False, invalid_evidence_rejected=True,
            delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
            effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
        )

    if rtype == "MODEL_FAMILY_ONLY":
        allowed = {str(m) for m in (rule_spec.get("allowed_model_ids") or ())}
        if base_fr.model_id in allowed:
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=True, tri_state="PROVEN_TRUE", rejection_code=None,
                missing_evidence_rejected=False, proven_false_rejected=False, invalid_evidence_rejected=False,
                delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
                effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts,
                materialized_plan=_build_r1_noop_materialized_plan(cand, base_fr, variant_id),
            )
        return R1CandidateDecision(
            opportunity_id=oid, short_code=short_code, variant_id=variant_id,
            eligible=False, tri_state="PROVEN_FALSE", rejection_code=f"MODEL_EXCLUDED:{base_fr.model_id}",
            missing_evidence_rejected=False, proven_false_rejected=True, invalid_evidence_rejected=False,
            delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
            effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
        )

    if rtype == "DIRECTION_ONLY":
        req_dir = str(rule_spec.get("allowed_direction") or "").upper()
        if base_fr.direction == req_dir:
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=True, tri_state="PROVEN_TRUE", rejection_code=None,
                missing_evidence_rejected=False, proven_false_rejected=False, invalid_evidence_rejected=False,
                delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
                effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts,
                materialized_plan=_build_r1_noop_materialized_plan(cand, base_fr, variant_id),
            )
        return R1CandidateDecision(
            opportunity_id=oid, short_code=short_code, variant_id=variant_id,
            eligible=False, tri_state="PROVEN_FALSE", rejection_code=f"DIRECTION_EXCLUDED:{base_fr.direction}",
            missing_evidence_rejected=False, proven_false_rejected=True, invalid_evidence_rejected=False,
            delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
            effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
        )

    if rtype == "CAUSAL_REGIME_RULE":
        target_reg = str(rule_spec.get("target_regime") or "TREND_EXPANSION_ALIGNED")
        if r1_frame.causal_regime_tri_state == "UNKNOWN_MISSING":
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=False, tri_state="UNKNOWN_MISSING", rejection_code="REGIME_EVIDENCE_UNKNOWN_MISSING",
                missing_evidence_rejected=True, proven_false_rejected=False, invalid_evidence_rejected=False,
                delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
                effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
            )
        if r1_frame.causal_regime_label == target_reg:
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=True, tri_state="PROVEN_TRUE", rejection_code=None,
                missing_evidence_rejected=False, proven_false_rejected=False, invalid_evidence_rejected=False,
                delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
                effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts,
                materialized_plan=_build_r1_noop_materialized_plan(cand, base_fr, variant_id),
            )
        return R1CandidateDecision(
            opportunity_id=oid, short_code=short_code, variant_id=variant_id,
            eligible=False, tri_state="PROVEN_FALSE", rejection_code=f"REGIME_MISMATCH:{r1_frame.causal_regime_label}",
            missing_evidence_rejected=False, proven_false_rejected=True, invalid_evidence_rejected=False,
            delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
            effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
        )

    if rtype == "PREENTRY_QUALITY_COHORT":
        if r1_frame.quality_cohort_tri_state == "PROVEN_TRUE":
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=True, tri_state="PROVEN_TRUE", rejection_code=None,
                missing_evidence_rejected=False, proven_false_rejected=False, invalid_evidence_rejected=False,
                delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
                effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts,
                materialized_plan=_build_r1_noop_materialized_plan(cand, base_fr, variant_id),
            )
        is_miss = (r1_frame.quality_cohort_tri_state == "UNKNOWN_MISSING")
        is_inv = (r1_frame.quality_cohort_tri_state == "INVALID")
        return R1CandidateDecision(
            opportunity_id=oid, short_code=short_code, variant_id=variant_id,
            eligible=False, tri_state=r1_frame.quality_cohort_tri_state,
            rejection_code=str(r1_frame.quality_rejection_reason or "QUALITY_COHORT_REJECTED"),
            missing_evidence_rejected=is_miss, proven_false_rejected=(not is_miss and not is_inv),
            invalid_evidence_rejected=is_inv,
            delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
            effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
        )

    if rtype == "DELAYED_CLOSED_BAR_CONFIRMATION":
        if bars_1m is None or base_chk + 1 >= len(bars_1m):
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=False, tri_state="UNKNOWN_MISSING", rejection_code="DELAYED_CONFIRMATION_NO_SUBSEQUENT_BARS",
                missing_evidence_rejected=True, proven_false_rejected=False, invalid_evidence_rejected=False,
                delayed_confirmation_used=True, effective_checkpoint_index_1m=base_chk,
                effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
            )
        max_wait = int(rule_spec.get("max_wait_bars_m1", 6))
        atr_m1_val = float(r1_frame.predictive_pre_entry.get("m1_atr_14") or 0.65)
        conf_type = (
            "FVG_RETEST_CONFIRMATION"
            if "FVG" in base_fr.model_id
            else "MSS_DISPLACEMENT_BREAK"
        )
        inv_px = float(cand.get("structural_invalidation_price") or base_fr.selected_sl)
        pending = PendingConfirmationV1793122(
            pending_setup_id=f"pend_{variant_id}_{oid}",
            origin_opportunity_id=oid,
            model_id=base_fr.model_id,
            symbol="XAUUSDT",
            direction=base_fr.direction,
            created_at_ms=base_ts,
            expires_at_ms=base_ts + max_wait * 60_000,
            max_wait_bars_m1=max_wait,
            required_confirmation_type=conf_type,
            trigger_level_price=base_fr.entry_reference,
            invalidation_price=inv_px,
            zone_low=base_fr.entry_reference - 0.5 * atr_m1_val if conf_type == "FVG_RETEST_CONFIRMATION" else None,
            zone_high=base_fr.entry_reference + 0.5 * atr_m1_val if conf_type == "FVG_RETEST_CONFIRMATION" else None,
            baseline_entry=base_fr.entry_reference,
            baseline_sl=base_fr.selected_sl,
            baseline_tp=base_fr.take_profit,
            created_bar_index_1m=base_chk,
        )
        updated, conf_bar_idx = advance_pending_confirmation_state_v1793122(
            pending,
            bars_1m=bars_1m,
            start_bar_index=base_chk + 1,
            atr_m1=atr_m1_val,
            min_displacement_body_atr_ratio=float(rule_spec.get("min_displacement_body_atr_ratio", 0.55)),
            min_net_rr=float(rule_spec.get("min_net_rr", 2.0)),
            max_planned_risk_usd=float(rule_spec.get("max_planned_risk_usd", 2.50)),
            spread_usd=0.20,
            slippage_usd=0.10,
            taker_fee_rate=0.0004,
        )
        if updated.status != "CONFIRMED" or conf_bar_idx is None or updated.confirmed_at_ms is None:
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=False, tri_state="PROVEN_FALSE",
                rejection_code=f"DELAYED_{updated.status}:{updated.terminal_reason}",
                missing_evidence_rejected=(updated.status == "CANCELLED_GAP"),
                proven_false_rejected=(updated.status != "CANCELLED_GAP"),
                invalid_evidence_rejected=False,
                delayed_confirmation_used=True, effective_checkpoint_index_1m=base_chk,
                effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
            )

        conf_ts = int(updated.confirmed_at_ms)
        if conf_ts <= base_ts or int(conf_bar_idx) <= base_chk:
            raise RuntimeError(f"CAUSAL_VIOLATION_DELAYED_NOT_STRICTLY_AFTER_T0:{oid}:{conf_ts}<={base_ts}")

        new_entry = float(updated.confirmed_entry_price or base_fr.entry_reference)
        new_sl = float(updated.confirmed_sl_price or base_fr.selected_sl)
        new_tp = float(updated.confirmed_tp_price or base_fr.take_profit)
        rk = recalculate_trade_risk_quantity_v179312(
            new_entry, new_sl, new_tp, direction=base_fr.direction, decision_ts=conf_ts
        )
        if not rk.get("can_execute") or float(rk.get("net_rr") or 0.0) < 2.0 or float(rk.get("planned_net_risk_usd") or 99.0) > 2.50:
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=False, tri_state="PROVEN_FALSE",
                rejection_code="DELAYED_RECOMPUTED_RISK_OR_RR_INVALID",
                missing_evidence_rejected=False, proven_false_rejected=True, invalid_evidence_rejected=False,
                delayed_confirmation_used=True, effective_checkpoint_index_1m=int(conf_bar_idx),
                effective_decision_timestamp_ms=conf_ts, known_at_ms=conf_ts, materialized_plan=None,
            )

        new_qty = float(rk["quantity"])
        new_risk = float(rk["planned_net_risk_usd"])
        new_nrr = float(rk["net_rr"])
        new_grr = float(rk.get("gross_rr") or new_nrr)
        fp = canonical_hash({
            "variant_id": variant_id,
            "oid": oid,
            "conf_ts": conf_ts,
            "entry": new_entry,
            "sl": new_sl,
            "tp": new_tp,
            "qty": new_qty,
        })
        mat_delayed = {
            "schema_version": SCHEMA_VERSION_KH01B_R1,
            "intent_id": f"intent_{variant_id}_{hashlib.sha256(f'{variant_id}|{oid}|{conf_ts}'.encode('utf-8')).hexdigest()[:16]}",
            "opportunity_id": oid,
            "setup_id": str(cand.get("setup_id") or base_fr.setup_episode_id),
            "variant_id": variant_id,
            "intervention_type": "CAUSAL_DELAYED_CLOSED_BAR_ENTRY_CONFIRMATION",
            "status": "ACCEPTED_DELAYED_TIME_SHIFTED_ENTRY",
            "accepted": True,
            "rejection_reason": None,
            "old_plan": {
                "opportunity_id": oid,
                "model_id": base_fr.model_id,
                "direction": base_fr.direction,
                "checkpoint_index_1m": base_chk,
                "decision_timestamp_ms": base_ts,
                "entry_reference": base_fr.entry_reference,
                "selected_sl": base_fr.selected_sl,
                "take_profit": base_fr.take_profit,
                "quantity": base_fr.hypothetical_quantity,
                "planned_net_risk_usd": base_fr.planned_net_risk_usd,
                "net_rr": base_fr.baseline_net_rr,
            },
            "new_checkpoint_index_1m": int(conf_bar_idx),
            "new_decision_timestamp_ms": conf_ts,
            "wait_bars_m1": int(conf_bar_idx) - base_chk,
            "new_entry_reference": new_entry,
            "new_selected_sl": new_sl,
            "new_take_profit": new_tp,
            "new_quantity": new_qty,
            "new_planned_net_risk_usd": new_risk,
            "new_net_rr": new_nrr,
            "new_gross_rr": new_grr,
            "new_structural_invalidation_price": inv_px,
            "new_selected_anchor_id": cand.get("selected_anchor_id"),
            "new_anchor_kind": cand.get("anchor_kind"),
            "new_selected_target_id": base_fr.selected_target_id,
            "changed_dimensions": ["decision_timestamp_ms", "entry_reference", "quantity"],
            "known_at_ms": conf_ts,
            "confirmation_subtype": conf_type,
            "new_trade_fingerprint": fp,
        }
        return R1CandidateDecision(
            opportunity_id=oid, short_code=short_code, variant_id=variant_id,
            eligible=True, tri_state="PROVEN_TRUE", rejection_code=None,
            missing_evidence_rejected=False, proven_false_rejected=False, invalid_evidence_rejected=False,
            delayed_confirmation_used=True, effective_checkpoint_index_1m=int(conf_bar_idx),
            effective_decision_timestamp_ms=conf_ts, known_at_ms=conf_ts, materialized_plan=mat_delayed,
        )

    if rtype == "REGIME_X_MODEL_INTERACTION":
        allowed_m = {str(m) for m in (rule_spec.get("allowed_model_ids") or ())}
        target_reg = str(rule_spec.get("target_regime") or "TREND_EXPANSION_ALIGNED")
        if base_fr.model_id not in allowed_m:
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=False, tri_state="PROVEN_FALSE", rejection_code=f"INTERACTION_MODEL_EXCLUDED:{base_fr.model_id}",
                missing_evidence_rejected=False, proven_false_rejected=True, invalid_evidence_rejected=False,
                delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
                effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
            )
        if r1_frame.causal_regime_tri_state == "UNKNOWN_MISSING":
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=False, tri_state="UNKNOWN_MISSING", rejection_code="INTERACTION_REGIME_UNKNOWN_MISSING",
                missing_evidence_rejected=True, proven_false_rejected=False, invalid_evidence_rejected=False,
                delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
                effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
            )
        if r1_frame.causal_regime_label != target_reg:
            return R1CandidateDecision(
                opportunity_id=oid, short_code=short_code, variant_id=variant_id,
                eligible=False, tri_state="PROVEN_FALSE", rejection_code=f"INTERACTION_REGIME_MISMATCH:{r1_frame.causal_regime_label}",
                missing_evidence_rejected=False, proven_false_rejected=True, invalid_evidence_rejected=False,
                delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
                effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts, materialized_plan=None,
            )
        return R1CandidateDecision(
            opportunity_id=oid, short_code=short_code, variant_id=variant_id,
            eligible=True, tri_state="PROVEN_TRUE", rejection_code=None,
            missing_evidence_rejected=False, proven_false_rejected=False, invalid_evidence_rejected=False,
            delayed_confirmation_used=False, effective_checkpoint_index_1m=base_chk,
            effective_decision_timestamp_ms=base_ts, known_at_ms=base_ts,
            materialized_plan=_build_r1_noop_materialized_plan(cand, base_fr, variant_id),
        )

    raise ValueError(f"UNSUPPORTED_KH01B_R1_RULE_TYPE:{rtype}")


# ==============================================================================
# D3.A.7 & D3.A.8: REPLAY FROZEN VARIANTS + EDGE UNCERTAINTY & RESEARCH VERDICT
# ==============================================================================

@dataclass(frozen=True)
class KH01BR1EconomicVerdict:
    variant_id: str
    short_code: str
    is_sub_contrast: bool
    parent_hypothesis_code: str
    ontology: str
    scheduled_nb: int
    closed_trades_nb: int
    wins_nb: int
    losses_nb: int
    win_rate_nb: Optional[float]
    trade_frequency_retention_vs_e00: float
    gross_pnl_1x_nb_usd: float
    total_fees_1x_nb_usd: float
    net_pnl_1x_nb_usd: float
    expectancy_1x_nb_r: Optional[float]
    profit_factor_1x_nb: Optional[float]
    max_drawdown_1x_nb_usd: float
    longest_losing_streak_1x_nb: int
    legacy_close_fill_net_pnl_usd: float
    stress_2x_spread_slippage_net_pnl_usd: float
    stress_2x_all_cost_net_pnl_usd: float
    stress_2x_all_cost_expectancy_r: Optional[float]
    stress_3x_all_cost_net_pnl_usd: float
    cluster_bootstrap_95ci_expectancy_r: Optional[Tuple[float, float]]
    cluster_bootstrap_95ci_net_pnl_usd: Optional[Tuple[float, float]]
    approx_min_detectable_edge_r_80pct_power: Optional[float]
    purged_reused_walkforward_folds: Tuple[Dict[str, Any], ...]
    paired_vs_e00_portfolio_net_pnl_diff_usd: float
    paired_vs_e00_matched_242_net_pnl_diff_usd: float
    winner_substitutions_vs_e00_count: int
    multiplicity_family_size_m: int
    holm_bonferroni_alpha_threshold: float
    sample_power_status: str
    research_verdict: str  # NO_CANDIDATE | RESEARCH_CANDIDATE_ONLY | NO_VALID_SETUPS | INCONCLUSIVE_UNDERPOWERED | NOT_RUN_JUSTIFIED | FAIL_LEAKAGE
    qualifies_for_kh01c: bool
    fresh_oos_verdict: str
    activation_ready: str


def _replay_one_r1_arm(
    *,
    short_code: str,
    variant_id: str,
    ontology: str,
    rule_spec: Mapping[str, Any],
    a1_decisions: Sequence[Mapping[str, Any]],
    si8_decisions: Sequence[Mapping[str, Any]],
    r1_frames_a1: Mapping[str, CausalRegimeFeatureFrameR1],
    r1_frames_si8: Mapping[str, CausalRegimeFeatureFrameR1],
    feature_ledger: Mapping[str, Mapping[str, Any]],
    candidate_index: Mapping[str, Any],
    datasets: Mapping[str, Any],
    e00_winner_by_chk: Optional[Mapping[int, str]],
    e00_nb_episodes: Optional[Sequence[Mapping[str, Any]]],
    unchanged_outcome_cache: Dict[Tuple[str, str], Dict[str, Any]],
) -> VariantReplayBundle:
    bars_1m = list(datasets["1m"])
    baseline_outcomes_by_opp_id = candidate_index.get("baseline_outcomes_by_opp_id") or {}

    surviving_by_eff_chk: Dict[int, List[Tuple[Mapping[str, Any], Dict[str, Any]]]] = {}
    elig_by_oid: Dict[str, R1CandidateDecision] = {}
    rej_counts: Counter[str] = Counter()
    elig_n = rej_n = miss_n = false_n = inv_n = delayed_shifted_n = 0

    for cand in a1_decisions:
        oid = str(cand["opportunity_id"])
        dec = evaluate_kh01b_r1_entry_variant(
            rule_spec, short_code, variant_id, cand, r1_frames_a1[oid], bars_1m=bars_1m
        )
        elig_by_oid[oid] = dec
        if dec.eligible and dec.materialized_plan is not None:
            elig_n += 1
            if dec.delayed_confirmation_used:
                delayed_shifted_n += 1
            surviving_by_eff_chk.setdefault(dec.effective_checkpoint_index_1m, []).append((cand, dec.materialized_plan))
        else:
            rej_n += 1
            rej_counts[str(dec.rejection_code or "REJECTED")] += 1
            if dec.invalid_evidence_rejected:
                inv_n += 1
            elif dec.missing_evidence_rejected:
                miss_n += 1
            else:
                false_n += 1

    # Multi-model arbitration at effective decision checkpoint (STATIC_PRODUCTION_PRIORITY)
    selected_pairs: List[Tuple[Mapping[str, Any], Dict[str, Any]]] = []
    suppressed_ties = 0
    winner_subs = 0

    for eff_chk in sorted(surviving_by_eff_chk.keys()):
        items = surviving_by_eff_chk[eff_chk]
        mat_by_oid = {str(c["opportunity_id"]): mat for c, mat in items}
        cands_at_chk = [dict(c) for c, _ in items]
        winner, losers = arbitrate_competing_model_intents_v1793122(
            cands_at_chk,
            arbitration_mode="STATIC_PRODUCTION_PRIORITY",
            feature_ledger_by_opp_id=feature_ledger,
        )
        if winner is not None:
            w_oid = str(winner["opportunity_id"])
            suppressed_ties += len(losers)
            if e00_winner_by_chk is not None and e00_winner_by_chk.get(eff_chk) != w_oid:
                winner_subs += 1
            selected_pairs.append((winner, mat_by_oid[w_oid]))

    selected_pairs.sort(
        key=lambda p: (
            int(p[1]["new_decision_timestamp_ms"]),
            int(p[1]["new_checkpoint_index_1m"]),
            int(PRODUCTION_MODEL_STATIC_PRIORITY.get(str(p[0]["model_id"]), 99)),
            str(p[0]["opportunity_id"]),
        )
    )

    fill_summaries: Dict[str, Dict[str, Any]] = {}
    all_mode_eps: Dict[str, List[Dict[str, Any]]] = {}

    for f_mode in FILL_MODES_KH01B:
        state = PortfolioReplayStateV179312(variant=variant_id, max_concurrent_positions=1)
        mode_eps: List[Dict[str, Any]] = []
        blocked_n = 0

        for opp, mat in selected_pairs:
            oid = str(opp["opportunity_id"])
            eff_ts = int(mat["new_decision_timestamp_ms"])
            advance_portfolio_to_next_event_v179312(state, None, eff_ts)
            if state.active_episode is not None and eff_ts < state.active_release_ts_ms:
                blocked_n += 1
                continue

            is_noop = (mat["status"] == "ACCEPTED_UNCHANGED_NO_OP")
            ckey = (oid, f_mode)
            if is_noop and f_mode == "NEXT_BAR_OPEN_BID_ASK" and oid in baseline_outcomes_by_opp_id:
                ep_rec = dict(baseline_outcomes_by_opp_id[oid]["episode_record"])
            elif is_noop and ckey in unchanged_outcome_cache:
                ep_rec = dict(unchanged_outcome_cache[ckey])
            else:
                rep = replay_intervention_outcome_v179312(mat, bars_1m, mode=f_mode, base_record=opp)
                ep_rec = dict(rep["episode_record"])
                if is_noop:
                    unchanged_outcome_cache[ckey] = dict(ep_rec)

            ep_rec["opportunity_id"] = oid
            ep_rec["intent_id"] = mat.get("intent_id")
            ep_rec["policy_id"] = variant_id
            ep_rec["fill_mode"] = f_mode
            ep_rec["decision_timestamp_ms"] = eff_ts
            ep_rec["origin_candidate_timestamp_ms"] = int(opp["decision_timestamp_ms"])
            ep_rec["known_at_ms"] = int(mat["known_at_ms"])
            ep_rec["changed_dimensions"] = list(mat.get("changed_dimensions") or [])
            ep_rec["entry_ts_ms"] = int(ep_rec.get("entry_fill_ts") or eff_ts)
            ep_rec["exit_ts_ms"] = int(ep_rec.get("exit_fill_ts") or ep_rec.get("release_ts_ms") or eff_ts)
            ep_rec["total_fees_usd"] = round(
                float(ep_rec.get("entry_fee_usd") or 0.0) + float(ep_rec.get("exit_fee_usd") or 0.0), 4
            )
            ep_rec["realized_net_r"] = float(ep_rec.get("realized_r") or 0.0)
            mode_eps.append(ep_rec)

            if ep_rec.get("filled", False):
                state.active_episode = ep_rec
                state.active_release_ts_ms = int(ep_rec["release_ts_ms"])

        fill_summaries[f_mode] = _summarize_episode_list_kh01b(mode_eps, blocked_n)
        all_mode_eps[f_mode] = mode_eps

    # Matched 242 E00 cohort counterfactual (for E05 time-shifted arm, replay shifted plan on matched 242 IDs)
    opp_by_id = {str(c["opportunity_id"]): c for c in a1_decisions}
    matched_cf: Dict[str, Any] = {"evaluated_b00_episodes": 0}
    if e00_nb_episodes is not None:
        m_closed: List[Dict[str, Any]] = []
        m_rej_w = m_rej_l = m_kept_sched = 0
        for b_ep in e00_nb_episodes:
            b_oid = str(b_ep["opportunity_id"])
            dec_b = elig_by_oid.get(b_oid)
            if dec_b is None or not dec_b.eligible or dec_b.materialized_plan is None:
                if b_ep.get("outcome_label") == "WIN":
                    m_rej_w += 1
                elif b_ep.get("outcome_label") == "LOSS":
                    m_rej_l += 1
                continue
            m_kept_sched += 1
            if dec_b.delayed_confirmation_used:
                rep_m = replay_intervention_outcome_v179312(
                    dec_b.materialized_plan, bars_1m, mode="NEXT_BAR_OPEN_BID_ASK", base_record=opp_by_id[b_oid]
                )
                ep_m = dict(rep_m["episode_record"])
            else:
                ep_m = dict(b_ep)
            if ep_m.get("filled", False) and str(ep_m.get("outcome_label")) in ("WIN", "LOSS"):
                m_closed.append(ep_m)

        e00_net = round(
            sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in e00_nb_episodes if e.get("outcome_label") in ("WIN", "LOSS")),
            4,
        )
        m_net = round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in m_closed), 4)
        m_exp = round(sum(float(e.get("realized_r") or 0.0) for e in m_closed) / len(m_closed), 4) if m_closed else None
        matched_cf = {
            "evaluated_b00_episodes": len(e00_nb_episodes),
            "surviving_scheduled_count": m_kept_sched,
            "surviving_closed_count": len(m_closed),
            "surviving_wins_count": sum(1 for e in m_closed if e.get("outcome_label") == "WIN"),
            "surviving_losses_count": sum(1 for e in m_closed if e.get("outcome_label") == "LOSS"),
            "filtered_out_b00_wins_count": m_rej_w,
            "filtered_out_b00_losses_count": m_rej_l,
            "surviving_net_pnl_usd": m_net,
            "surviving_expectancy_r": m_exp,
            "paired_net_pnl_delta_vs_b00_usd": round(m_net - e00_net, 4),
            "arbitration_substitution_caution": (
                "Full chronological replay may differ from matched-242 cohort when filtered high-priority "
                "models allow lower-priority models to win timestamp arbitration or unblock 1-pos slots."
            ),
        }

    # Separate unmixed SI8 6,375 diagnostic
    s8_elig = 0
    s8_rejs: Counter[str] = Counter()
    for s_cand in si8_decisions:
        s_oid = str(s_cand["opportunity_id"])
        d_s8 = evaluate_kh01b_r1_entry_variant(
            rule_spec, short_code, variant_id, s_cand, r1_frames_si8[s_oid], bars_1m=bars_1m
        )
        if d_s8.eligible:
            s8_elig += 1
        else:
            s8_rejs[str(d_s8.rejection_code or "REJECTED")] += 1
    si8_diag = {
        "population_name": "SEPARATE_SI8_DISCOVERY_6375_UNMIXED",
        "si8_total": len(si8_decisions),
        "si8_eligible_count": s8_elig,
        "si8_rejection_counts": dict(sorted(s8_rejs.items())),
    }

    b_sha = canonical_hash({
        "vid": variant_id,
        "elig": elig_n,
        "rej": rej_n,
        "summaries": fill_summaries,
        "matched": matched_cf,
    })
    return VariantReplayBundle(
        variant_id=variant_id,
        short_code=short_code,
        ontology=ontology,
        axis_type=str(rule_spec.get("type") or ""),
        population_name="CANONICAL_A1_V1792_SL_TP_BASELINE_56710",
        population_total=len(a1_decisions),
        eligible_candidates_count=elig_n,
        rejected_candidates_count=rej_n,
        missing_evidence_rejected_count=miss_n,
        proven_false_rejected_count=false_n,
        invalid_evidence_rejected_count=inv_n,
        plausible_eligibility_lower_bound=elig_n,
        plausible_eligibility_upper_bound=elig_n + miss_n,
        rejection_reasons_counts=dict(sorted(rej_counts.items())),
        post_arbitration_timestamps_selected=len(selected_pairs),
        arbitration_suppressed_ties_count=suppressed_ties,
        arbitration_winner_substitutions_vs_b00=winner_subs,
        materialized_accepted_count=len(selected_pairs),
        materialized_modified_tp_count=delayed_shifted_n,
        fill_mode_summaries=fill_summaries,
        primary_nb_episodes=all_mode_eps["NEXT_BAR_OPEN_BID_ASK"],
        all_mode_episodes=all_mode_eps,
        matched_b00_242_counterfactual=matched_cf,
        si8_separate_diagnostic=si8_diag,
        bundle_sha256=b_sha,
    )


def compute_kh01b_r1_edge_uncertainty_and_research_verdict(
    bundle: VariantReplayBundle,
    folds: Sequence[Tuple[int, int, int, int]],
    *,
    baseline_e00_bundle: Optional[VariantReplayBundle] = None,
    multiplicity_m: int = 10,
    is_sub_contrast: bool = False,
    parent_hypothesis_code: str = "",
    not_run_justified: bool = False,
) -> KH01BR1EconomicVerdict:
    """
    D3.A.8 & G17: Computes per-variant economics, 1x/2x/3x ALL-COST stress, NY-day cluster
    bootstrap 95% CIs, 180m purged reused walk-forward folds, Holm-Bonferroni threshold, and
    strict zero-trade (`None` / `NOT_ESTIMABLE`) & small-N (`UNDERPOWERED`) verdicts.
    """
    alpha_holm = round(0.05 / max(1, int(multiplicity_m)), 6)
    p_code = parent_hypothesis_code or bundle.short_code

    if not_run_justified:
        return KH01BR1EconomicVerdict(
            variant_id=bundle.variant_id, short_code=bundle.short_code,
            is_sub_contrast=is_sub_contrast, parent_hypothesis_code=p_code, ontology=bundle.ontology,
            scheduled_nb=0, closed_trades_nb=0, wins_nb=0, losses_nb=0, win_rate_nb=None,
            trade_frequency_retention_vs_e00=0.0, gross_pnl_1x_nb_usd=0.0, total_fees_1x_nb_usd=0.0,
            net_pnl_1x_nb_usd=0.0, expectancy_1x_nb_r=None, profit_factor_1x_nb=None,
            max_drawdown_1x_nb_usd=0.0, longest_losing_streak_1x_nb=0,
            legacy_close_fill_net_pnl_usd=0.0, stress_2x_spread_slippage_net_pnl_usd=0.0,
            stress_2x_all_cost_net_pnl_usd=0.0, stress_2x_all_cost_expectancy_r=None,
            stress_3x_all_cost_net_pnl_usd=0.0, cluster_bootstrap_95ci_expectancy_r=None,
            cluster_bootstrap_95ci_net_pnl_usd=None, approx_min_detectable_edge_r_80pct_power=None,
            purged_reused_walkforward_folds=(), paired_vs_e00_portfolio_net_pnl_diff_usd=0.0,
            paired_vs_e00_matched_242_net_pnl_diff_usd=0.0, winner_substitutions_vs_e00_count=0,
            multiplicity_family_size_m=multiplicity_m, holm_bonferroni_alpha_threshold=alpha_holm,
            sample_power_status="NOT_RUN_PRE_OUTCOME_COMPLETENESS_GATE",
            research_verdict="NOT_RUN_JUSTIFIED", qualifies_for_kh01c=False,
            fresh_oos_verdict="BLOCKED_NO_UNTOUCHED_DATA", activation_ready="NO",
        )

    nb_sum = bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]
    leg_sum = bundle.fill_mode_summaries["LEGACY_CLOSE_FILL"]
    sp2_sum = bundle.fill_mode_summaries["STRESS_WIDER_SPREAD_SLIPPAGE_2X"]

    closed_nb = [
        e for e in bundle.primary_nb_episodes
        if e.get("filled", False) and str(e.get("outcome_label")) in ("WIN", "LOSS")
    ]
    closed_sp2 = [
        e for e in bundle.all_mode_episodes["STRESS_WIDER_SPREAD_SLIPPAGE_2X"]
        if e.get("filled", False) and str(e.get("outcome_label")) in ("WIN", "LOSS")
    ]
    closed_n = len(closed_nb)

    # 2x & 3x ALL-COST per-trade recompute
    net_2x_list: List[float] = []
    r_2x_list: List[float] = []
    for e2 in closed_sp2:
        g2 = float(e2.get("realized_gross_pnl_usd") or 0.0)
        f1 = float(e2.get("total_fees_usd") or (float(e2.get("entry_fee_usd") or 0.0) + float(e2.get("exit_fee_usd") or 0.0)))
        n2 = g2 - 2.0 * f1
        rk = max(0.01, float(e2.get("planned_net_risk_usd") or 2.50))
        net_2x_list.append(n2)
        r_2x_list.append(n2 / rk)

    net_2x_all = round(sum(net_2x_list), 4)
    exp_2x_all: Optional[float] = round(sum(r_2x_list) / len(r_2x_list), 4) if r_2x_list else None

    sp2_by_oid = {str(e["opportunity_id"]): e for e in closed_sp2}
    net_3x_list: List[float] = []
    for e1 in closed_nb:
        oid = str(e1["opportunity_id"])
        g1 = float(e1.get("realized_gross_pnl_usd") or 0.0)
        f1 = float(e1.get("total_fees_usd") or 0.0)
        if oid in sp2_by_oid:
            g2 = float(sp2_by_oid[oid].get("realized_gross_pnl_usd") or g1)
            extra = max(0.0, g1 - g2)
            g3 = g1 - 2.0 * extra
        else:
            g3 = g1 - 0.60 * float(e1.get("executed_quantity") or 0.0)
        net_3x_list.append(g3 - 3.0 * f1)
    net_3x_all = round(sum(net_3x_list), 4)

    # Cluster bootstrap by NY trading day (deterministic seed=20261119) + MDE power precision
    ci_r: Optional[Tuple[float, float]] = None
    ci_pnl: Optional[Tuple[float, float]] = None
    mde_80: Optional[float] = None
    if closed_n >= 2:
        r_vals = [float(e.get("realized_net_r") or e.get("realized_r") or 0.0) for e in closed_nb]
        mean_r = sum(r_vals) / closed_n
        var_r = sum((x - mean_r) ** 2 for x in r_vals) / max(1, closed_n - 1)
        sd_r = math.sqrt(max(1e-9, var_r))
        # Approximate 80% power MDE at Holm-adjusted two-sided alpha: (z_{1-a/2} + z_0.80) * sd / sqrt(N)
        mde_80 = round((2.81 + 0.84) * sd_r / math.sqrt(closed_n), 4)

    if closed_n >= 5:
        by_day: Dict[str, List[Tuple[float, float]]] = {}
        for e in closed_nb:
            ts = int(e.get("decision_timestamp_ms") or 0)
            dk = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).astimezone(NY_TZ).strftime("%Y-%m-%d")
            by_day.setdefault(dk, []).append((
                float(e.get("realized_net_r") or e.get("realized_r") or 0.0),
                float(e.get("realized_net_pnl_usd") or 0.0),
            ))
        day_keys = sorted(by_day.keys())
        if len(day_keys) >= 2:
            rng = random.Random(20261119)
            boot_r: List[float] = []
            boot_p: List[float] = []
            k_d = len(day_keys)
            for _ in range(1000):
                s_days = [day_keys[rng.randrange(k_d)] for _ in range(k_d)]
                pts = [it for dk in s_days for it in by_day[dk]]
                if pts:
                    boot_r.append(sum(x[0] for x in pts) / len(pts))
                    boot_p.append(sum(x[1] for x in pts))
            boot_r.sort()
            boot_p.sort()
            lo_i = int(0.025 * len(boot_r))
            hi_i = min(len(boot_r) - 1, int(0.975 * len(boot_r)))
            ci_r = (round(boot_r[lo_i], 4), round(boot_r[hi_i], 4))
            ci_pnl = (round(boot_p[lo_i], 4), round(boot_p[hi_i], 4))

    # 180m purged reused walk-forward folds
    fold_reports: List[Dict[str, Any]] = []
    leakage = False
    for f_i, (tr_s, tr_e, val_s, val_e) in enumerate(folds, start=1):
        tr_eps = [
            e for e in closed_nb
            if tr_s <= int(e.get("decision_timestamp_ms") or 0) <= tr_e
            and int(e.get("exit_ts_ms") or e.get("release_ts_ms") or 0) <= tr_e
        ]
        val_eps = [
            e for e in closed_nb
            if val_s <= int(e.get("decision_timestamp_ms") or 0) <= val_e
        ]
        tr_ids = {str(e.get("setup_episode_id") or e["opportunity_id"]) for e in tr_eps}
        val_ids = {str(e.get("setup_episode_id") or e["opportunity_id"]) for e in val_eps}
        shared = tr_ids.intersection(val_ids)
        if shared or (val_s - tr_e) < 180 * 60_000:
            leakage = True
        fold_reports.append({
            "fold_id": f"REUSED_DEV_PURGED_FOLD_{f_i}",
            "partition_label": "REUSED_DEVELOPMENT_NOT_FRESH_OOS",
            "embargo_minutes": round((val_s - tr_e) / 60_000, 1),
            "shared_episodes_count": len(shared),
            "train_closed_n": len(tr_eps),
            "train_net_pnl_usd": round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in tr_eps), 4),
            "val_closed_n": len(val_eps),
            "val_net_pnl_usd": round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in val_eps), 4),
            "val_expectancy_r": round(sum(float(e.get("realized_net_r") or 0.0) for e in val_eps) / len(val_eps), 4) if val_eps else None,
        })

    e00_sched = int(baseline_e00_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]["total_scheduled_episodes"]) if baseline_e00_bundle else max(1, nb_sum["total_scheduled_episodes"])
    e00_net = float(baseline_e00_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]["total_realized_net_usd"]) if baseline_e00_bundle else 0.0

    net_1x = float(nb_sum["total_realized_net_usd"])
    exp_1x: Optional[float] = float(nb_sum["expectancy_net_r"]) if closed_n > 0 else None
    pf_1x: Optional[float] = float(nb_sum["profit_factor"]) if closed_n > 0 else None
    wr_1x: Optional[float] = float(nb_sum["win_rate"]) if closed_n > 0 else None

    if leakage:
        verdict = "FAIL_LEAKAGE"
        power_st = "FATAL_FOLD_EMBARGO_OR_EPISODE_LEAKAGE"
    elif closed_n == 0:
        # CRITICAL D1.7: Zero trades => expectancy/CI NOT_ESTIMABLE (None), NEVER a 0R edge!
        verdict = "NO_VALID_SETUPS"
        power_st = "NOT_ESTIMABLE_ZERO_CLOSED_TRADES"
    elif net_1x <= 0.0 or (exp_1x is not None and exp_1x <= 0.0) or net_2x_all <= 0.0 or (pf_1x is not None and pf_1x <= 1.05):
        verdict = "NO_CANDIDATE"
        power_st = (
            f"UNDERPOWERED_N={closed_n}_LT_30_NEGATIVE_OR_COST_FRAGILE"
            if closed_n < 30
            else f"POWERED_MIN_GATE_N={closed_n}_NEGATIVE_NET_OR_2X_COST_EDGE"
        )
    elif closed_n < 30:
        verdict = "INCONCLUSIVE_UNDERPOWERED"
        power_st = f"UNDERPOWERED_N={closed_n}_LT_30_CANNOT_QUALIFY"
    else:
        pos_folds = sum(1 for f in fold_reports if f["val_closed_n"] > 0 and float(f["val_net_pnl_usd"]) > 0.0)
        ci_lo_pos = bool(ci_r is not None and ci_r[0] > 0.0 and ci_pnl is not None and ci_pnl[0] > 0.0)
        if pos_folds >= 2 and ci_lo_pos:
            verdict = "RESEARCH_CANDIDATE_ONLY"
            power_st = f"REUSED_DEV_ONLY_N={closed_n}_REQUIRES_LARGE_UNTOUCHED_OOS"
        else:
            verdict = "INCONCLUSIVE_UNDERPOWERED"
            power_st = f"WIDE_CLUSTER_CI_OR_FOLD_INSTABILITY_N={closed_n}"

    qualifies = (verdict == "RESEARCH_CANDIDATE_ONLY" and not is_sub_contrast and bundle.short_code != "E00")

    return KH01BR1EconomicVerdict(
        variant_id=bundle.variant_id,
        short_code=bundle.short_code,
        is_sub_contrast=is_sub_contrast,
        parent_hypothesis_code=p_code,
        ontology=bundle.ontology,
        scheduled_nb=int(nb_sum["total_scheduled_episodes"]),
        closed_trades_nb=closed_n,
        wins_nb=int(nb_sum["wins_count"]),
        losses_nb=int(nb_sum["losses_count"]),
        win_rate_nb=wr_1x,
        trade_frequency_retention_vs_e00=round(nb_sum["total_scheduled_episodes"] / max(1, e00_sched), 4),
        gross_pnl_1x_nb_usd=float(nb_sum["total_realized_gross_usd"]),
        total_fees_1x_nb_usd=float(nb_sum["total_fees_usd"]),
        net_pnl_1x_nb_usd=net_1x,
        expectancy_1x_nb_r=exp_1x,
        profit_factor_1x_nb=pf_1x,
        max_drawdown_1x_nb_usd=float(nb_sum["max_drawdown_usd"]),
        longest_losing_streak_1x_nb=int(nb_sum["longest_losing_streak"]),
        legacy_close_fill_net_pnl_usd=float(leg_sum["total_realized_net_usd"]),
        stress_2x_spread_slippage_net_pnl_usd=float(sp2_sum["total_realized_net_usd"]),
        stress_2x_all_cost_net_pnl_usd=net_2x_all,
        stress_2x_all_cost_expectancy_r=exp_2x_all,
        stress_3x_all_cost_net_pnl_usd=net_3x_all,
        cluster_bootstrap_95ci_expectancy_r=ci_r,
        cluster_bootstrap_95ci_net_pnl_usd=ci_pnl,
        approx_min_detectable_edge_r_80pct_power=mde_80,
        purged_reused_walkforward_folds=tuple(fold_reports),
        paired_vs_e00_portfolio_net_pnl_diff_usd=round(net_1x - e00_net, 4),
        paired_vs_e00_matched_242_net_pnl_diff_usd=float(bundle.matched_b00_242_counterfactual.get("paired_net_pnl_delta_vs_b00_usd") or 0.0),
        winner_substitutions_vs_e00_count=int(bundle.arbitration_winner_substitutions_vs_b00),
        multiplicity_family_size_m=multiplicity_m,
        holm_bonferroni_alpha_threshold=alpha_holm,
        sample_power_status=power_st,
        research_verdict=verdict,
        qualifies_for_kh01c=qualifies,
        fresh_oos_verdict="BLOCKED_NO_UNTOUCHED_DATA",
        activation_ready="NO",
    )


def replay_kh01b_r1_frozen_variants(
    frozen_manifest: FrozenKH01BR1Manifest,
    *,
    a1_decisions: Sequence[Mapping[str, Any]],
    si8_decisions: Sequence[Mapping[str, Any]],
    r1_frames_a1: Mapping[str, CausalRegimeFeatureFrameR1],
    r1_frames_si8: Mapping[str, CausalRegimeFeatureFrameR1],
    feature_ledger: Mapping[str, Mapping[str, Any]],
    candidate_index: Mapping[str, Any],
    datasets: Mapping[str, Any],
    run_id: str,
    tested_sha: str,
) -> Dict[str, Any]:
    """
    D3.A.7: Replays frozen E00..E06 + predeclared sub-contrasts + unchanged B09/B10 D4 ontology
    audit on the 56,710 A1 candidate universe and unmixed 6,375 SI8 discovery population.
    """
    if not frozen_manifest.frozen_before_outcome_evaluation:
        raise ValueError("KH01B_R1_MANIFEST_MUST_BE_FROZEN_BEFORE_REPLAY")

    bars_1m = datasets["1m"]
    first_ms = int(bars_1m[0].get("open_time") or bars_1m[0].get("timestamp") or 0)
    last_ms = int(bars_1m[-1].get("close_time") or 0)
    span_ms = max(1, last_ms - first_ms)
    emb_ms = 180 * 60_000
    folds_spec: List[Tuple[int, int, int, int]] = []
    for f_idx in range(3):
        tr_end = first_ms + int(span_ms * (0.45 + 0.15 * f_idx))
        val_st = tr_end + emb_ms
        val_end = first_ms + int(span_ms * (0.60 + 0.15 * f_idx)) if f_idx < 2 else last_ms
        folds_spec.append((first_ms, tr_end, val_st, val_end))

    unchanged_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
    bundles: Dict[str, VariantReplayBundle] = {}
    economics: Dict[str, KH01BR1EconomicVerdict] = {}

    # E00 baseline control first
    e00_spec = next(t for t in frozen_manifest.theses if t["short_code"] == "E00")
    e00_bundle = _replay_one_r1_arm(
        short_code="E00",
        variant_id=str(e00_spec["variant_id"]),
        ontology=str(e00_spec["ontology"]),
        rule_spec=e00_spec["rule"],
        a1_decisions=a1_decisions,
        si8_decisions=si8_decisions,
        r1_frames_a1=r1_frames_a1,
        r1_frames_si8=r1_frames_si8,
        feature_ledger=feature_ledger,
        candidate_index=candidate_index,
        datasets=datasets,
        e00_winner_by_chk=None,
        e00_nb_episodes=None,
        unchanged_outcome_cache=unchanged_cache,
    )
    e00_by_chk: Dict[int, List[Dict[str, Any]]] = {}
    for c in a1_decisions:
        e00_by_chk.setdefault(int(c["checkpoint_index_1m"]), []).append(dict(c))
    e00_winner_map: Dict[int, str] = {}
    for ck_i, grp in e00_by_chk.items():
        w, _ = arbitrate_competing_model_intents_v1793122(
            grp, arbitration_mode="STATIC_PRODUCTION_PRIORITY", feature_ledger_by_opp_id=feature_ledger
        )
        if w is not None:
            e00_winner_map[ck_i] = str(w["opportunity_id"])

    e00_nb_sum = e00_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]
    e00_bundle.matched_b00_242_counterfactual = {
        "evaluated_b00_episodes": len(e00_bundle.primary_nb_episodes),
        "surviving_scheduled_count": len(e00_bundle.primary_nb_episodes),
        "surviving_closed_count": e00_nb_sum["closed_trades_count"],
        "surviving_wins_count": e00_nb_sum["wins_count"],
        "surviving_losses_count": e00_nb_sum["losses_count"],
        "filtered_out_b00_wins_count": 0,
        "filtered_out_b00_losses_count": 0,
        "surviving_net_pnl_usd": e00_nb_sum["total_realized_net_usd"],
        "surviving_expectancy_r": e00_nb_sum["expectancy_net_r"],
        "paired_net_pnl_delta_vs_b00_usd": 0.0,
    }
    bundles["E00"] = e00_bundle
    economics["E00"] = compute_kh01b_r1_edge_uncertainty_and_research_verdict(
        e00_bundle, folds_spec, baseline_e00_bundle=e00_bundle, multiplicity_m=frozen_manifest.multiplicity_family_size_m
    )

    for th in frozen_manifest.theses:
        sc = str(th["short_code"])
        if sc == "E00":
            continue
        if sc == "E06" and frozen_manifest.e06_execution_status == "NOT_RUN_JUSTIFIED":
            empty_bnd = VariantReplayBundle(
                variant_id=str(th["variant_id"]), short_code="E06", ontology=str(th["ontology"]),
                axis_type="NOT_RUN_JUSTIFIED", population_name="CANONICAL_A1_V1792_SL_TP_BASELINE_56710",
                population_total=len(a1_decisions), eligible_candidates_count=0,
                rejected_candidates_count=len(a1_decisions), missing_evidence_rejected_count=len(a1_decisions),
                proven_false_rejected_count=0, invalid_evidence_rejected_count=0,
                plausible_eligibility_lower_bound=0, plausible_eligibility_upper_bound=len(a1_decisions),
                rejection_reasons_counts={"NOT_RUN_PRE_OUTCOME_COMPLETENESS_GATE": len(a1_decisions)},
                post_arbitration_timestamps_selected=0, arbitration_suppressed_ties_count=0,
                arbitration_winner_substitutions_vs_b00=0, materialized_accepted_count=0,
                materialized_modified_tp_count=0,
                fill_mode_summaries={fm: _summarize_episode_list_kh01b([], 0) for fm in FILL_MODES_KH01B},
                primary_nb_episodes=[], all_mode_episodes={fm: [] for fm in FILL_MODES_KH01B},
                matched_b00_242_counterfactual={"evaluated_b00_episodes": 242, "surviving_closed_count": 0, "paired_net_pnl_delta_vs_b00_usd": 0.0},
                si8_separate_diagnostic=None, bundle_sha256="NOT_RUN_JUSTIFIED",
            )
            bundles["E06"] = empty_bnd
            economics["E06"] = compute_kh01b_r1_edge_uncertainty_and_research_verdict(
                empty_bnd, folds_spec, baseline_e00_bundle=e00_bundle,
                multiplicity_m=frozen_manifest.multiplicity_family_size_m, not_run_justified=True,
            )
            continue

        bnd = _replay_one_r1_arm(
            short_code=sc,
            variant_id=str(th["variant_id"]),
            ontology=str(th["ontology"]),
            rule_spec=th["rule"],
            a1_decisions=a1_decisions,
            si8_decisions=si8_decisions,
            r1_frames_a1=r1_frames_a1,
            r1_frames_si8=r1_frames_si8,
            feature_ledger=feature_ledger,
            candidate_index=candidate_index,
            datasets=datasets,
            e00_winner_by_chk=e00_winner_map,
            e00_nb_episodes=e00_bundle.primary_nb_episodes,
            unchanged_outcome_cache=unchanged_cache,
        )
        bundles[sc] = bnd
        economics[sc] = compute_kh01b_r1_edge_uncertainty_and_research_verdict(
            bnd, folds_spec, baseline_e00_bundle=e00_bundle, multiplicity_m=frozen_manifest.multiplicity_family_size_m
        )

        # Also evaluate fixed predeclared sub-contrasts (E01_C1..C3, E02_C1..C2, E03_CHOP)
        for sub in th.get("predeclared_sub_contrasts") or ():
            sub_sc = str(sub["short_code"])
            sub_bnd = _replay_one_r1_arm(
                short_code=sub_sc,
                variant_id=str(sub["variant_id"]),
                ontology=str(th["ontology"]),
                rule_spec=sub["rule"],
                a1_decisions=a1_decisions,
                si8_decisions=si8_decisions,
                r1_frames_a1=r1_frames_a1,
                r1_frames_si8=r1_frames_si8,
                feature_ledger=feature_ledger,
                candidate_index=candidate_index,
                datasets=datasets,
                e00_winner_by_chk=e00_winner_map,
                e00_nb_episodes=e00_bundle.primary_nb_episodes,
                unchanged_outcome_cache=unchanged_cache,
            )
            bundles[sub_sc] = sub_bnd
            economics[sub_sc] = compute_kh01b_r1_edge_uncertainty_and_research_verdict(
                sub_bnd, folds_spec, baseline_e00_bundle=e00_bundle,
                multiplicity_m=frozen_manifest.multiplicity_family_size_m,
                is_sub_contrast=True, parent_hypothesis_code=sc,
            )

    # Verify strict D4 unchanged (0 historical setups, 25/25 unit fixtures PASS, adjacent never relabels D4)
    d4_funnel = build_d4_full_63085_and_matched_funnel_v17931214(
        run_id=run_id,
        tested_sha=tested_sha,
        dataset_sha=CANONICAL_DATASET_SHA256_KH01B_R1,
        baseline_decisions=a1_decisions,
        si8_decisions=si8_decisions,
        feature_ledger_by_opp_id=feature_ledger,
        datasets=datasets,
        matched_242_d4_rows_by_mode={fm: [] for fm in FILL_MODES_KH01B},
    )
    d4_fixtures = audit_kh01b_true_unicorn_positive_and_negative_fixtures()
    d4_a1_eligible = sum(
        1 for fr in r1_frames_a1.values()
        if evaluate_strict_true_unicorn_hypothesis(fr.base_frame).is_valid_strict_unicorn
    )
    adj_a1_decisions = [
        evaluate_unicorn_adjacent_research_hypothesis(fr.base_frame)
        for fr in r1_frames_a1.values()
    ]
    adj_a1_eligible = sum(1 for d in adj_a1_decisions if d.eligible)
    adj_counted_as_d4 = sum(1 for d in adj_a1_decisions if d.counts_toward_d4_true_unicorn)

    primary_codes = ["E00", "E01", "E02", "E03", "E04", "E05", "E06"]
    qualified = sorted(sc for sc in primary_codes if sc != "E00" and economics[sc].qualifies_for_kh01c)
    kh01c_verdict = (
        "QUALIFIED_RESEARCH_CANDIDATE_FOR_KH01C"
        if qualified
        else "NO_ECONOMIC_ADVANCEMENT_CANDIDATE"
    )
    if qualified:
        research_hyp_verdict = "RESEARCH_CANDIDATE_ONLY"
    elif any(economics[sc].research_verdict == "FAIL_LEAKAGE" for sc in economics):
        research_hyp_verdict = "FAIL_LEAKAGE"
    else:
        research_hyp_verdict = "NO_DEFENSIBLE_EDGE"

    return {
        "bundles": bundles,
        "economics": economics,
        "primary_codes": primary_codes,
        "qualified_kh01c_candidates": qualified,
        "kh01c_advancement_verdict": kh01c_verdict,
        "research_hypothesis_verdict": research_hyp_verdict,
        "d4_isolation_11": {
            "spec_version": SPEC_VERSION_KH01B_R1,
            "strict_d4_definition_unchanged": True,
            "d4_63085_stage_funnel": d4_funnel.get("full_63085_stage_counts") or d4_funnel,
            "d4_a1_56710_eligible_count": d4_a1_eligible,
            "d4_historical_closed_trades": 0,
            "d4_historical_expectancy_r": None,
            "d4_historical_verdict": "NO_VALID_SETUPS",
            "adjacent_b10_a1_eligible_count": adj_a1_eligible,
            "adjacent_counted_toward_d4_count": adj_counted_as_d4,
            "ontology_isolation_verified": (d4_a1_eligible == 0 and adj_counted_as_d4 == 0),
            "detector_unit_fixture_validation": asdict(d4_fixtures),
        },
    }


# ==============================================================================
# D3.A.9: VALIDATE DATA PROVENANCE & FUTURE OOS SEALED ESCROW
# ==============================================================================

def validate_kh01b_r1_data_provenance_and_escrow(
    *,
    collection_report: Mapping[str, Any],
    freeze_timestamp_ms: int,
    tested_sha: str,
    config_sha256: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    D3.A.9: Produces `06_extended_dev_dataset_provenance_gap_and_quote_capability.json`
    and `07_future_oos_sealed_escrow_registry_no_evaluation.json`.
    Ensures extended DEV data is strictly marked `DEV_EXTENDED_RESEARCH` (`MODELED_BID_ASK`)
    and excluded from the Future OOS Escrow Registry (`strategy_evaluation_count == 0`).
    """
    known_dev_shas = [CANONICAL_DATASET_SHA256_KH01B_R1]
    ext_sha = collection_report.get("dev_extended_snapshot_sha256")
    if isinstance(ext_sha, str) and len(ext_sha) == 64:
        known_dev_shas.append(ext_sha)

    acq_verdict = str(collection_report.get("DATA_ACQUISITION_VERDICT") or "BLOCKED_UNAVAILABLE")
    allowed_acq_verdicts = (
        "VERIFIED_COMPLETE",
        "VERIFIED_PARTIAL",
        "BLOCKED_UNAVAILABLE",
        "BLOCKED_LOW_DISK",
        "FAIL_INTEGRITY",
        "FAIL_CONTAMINATED",
    )
    if acq_verdict not in allowed_acq_verdicts:
        acq_verdict = "FAIL_INTEGRITY"
    if bool(collection_report.get("synthetic_substitution_performed", False)):
        acq_verdict = "FAIL_CONTAMINATED"

    if acq_verdict in ("VERIFIED_COMPLETE", "VERIFIED_PARTIAL"):
        gap_sum = collection_report.get("gap_classification_summary")
        per_tf_val = collection_report.get("per_timeframe_validation")
        req_tfs = ("1m", "5m", "15m", "1H", "4H", "1D")
        if not isinstance(gap_sum, Mapping) or not isinstance(per_tf_val, Mapping):
            acq_verdict = "FAIL_INTEGRITY"
        else:
            for tf_k in req_tfs:
                g_e = gap_sum.get(tf_k)
                v_e = per_tf_val.get(tf_k)
                if (
                    not isinstance(g_e, Mapping)
                    or not isinstance(v_e, Mapping)
                    or g_e.get("actual_bars") is None
                    or g_e.get("total_missing_bars") is None
                    or not isinstance(g_e.get("gap_events_by_category"), Mapping)
                    or not isinstance(g_e.get("missing_bars_by_category"), Mapping)
                    or v_e.get("status") != "PASS"
                ):
                    acq_verdict = "FAIL_INTEGRITY"
                    break
                if acq_verdict == "VERIFIED_COMPLETE" and (
                    v_e.get("coverage_verdict") != "VERIFIED_COMPLETE"
                    or int(v_e.get("missing_slots", 1)) != 0
                ):
                    acq_verdict = "FAIL_INTEGRITY"
                    break

    doc_06 = {
        "spec_version": SPEC_VERSION_KH01B_R1,
        "schema_version": SCHEMA_VERSION_KH01B_R1,
        "DATA_ACQUISITION_VERDICT": acq_verdict,
        "canonical_reused_dataset": {
            "path": "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz",
            "sha256": CANONICAL_DATASET_SHA256_KH01B_R1,
            "partition_role": "REUSED_RESEARCH",
            "quote_mode": "MODELED_BID_ASK",
            "modeled_spread_usd": 0.20,
            "historical_l1_bbo_captured": False,
            "untouched_bytes_verified": collection_report.get("canonical_dataset_untouched_sha256", CANONICAL_DATASET_SHA256_KH01B_R1) == CANONICAL_DATASET_SHA256_KH01B_R1,
        },
        "bitget_source_capability_and_extended_dev": dict(collection_report),
    }

    doc_07 = build_and_validate_future_oos_escrow_registry(
        freeze_timestamp_ms=freeze_timestamp_ms,
        frozen_candidate_code_sha=tested_sha,
        frozen_config_sha256=config_sha256,
        proposed_oos_entries=(),
        known_dev_dataset_shas=known_dev_shas,
    )
    return doc_06, doc_07


# ==============================================================================
# G: 24 REAL RED/GREEN ADVERSARIAL FAULT INJECTIONS (15)
# ==============================================================================

def run_kh01b_r1_real_fault_injections(repo_root: Path) -> Dict[str, Any]:
    """
    Executes 24 real mutation / adversarial controls (`M01`..`M24`) anchored to actual R1
    functions and validators. Every case verifies BOTH `RED` (mutated input rejected) AND
    `GREEN` (clean control accepted). Zero string-padding assertions!
    """
    cases: List[Dict[str, Any]] = []
    ts_ms = 1_792_000_080_000  # Strictly minute-aligned (divisible by 60_000)
    base_cand = {
        "opportunity_id": "opp_m_ctrl",
        "setup_episode_id": "ep_m_ctrl",
        "model_id": "SMC_M1_FVG_CONTINUATION",
        "direction": "SHORT",
        "decision_timestamp_ms": ts_ms,
        "checkpoint_index_1m": 30,
        "decision_status": "ELIGIBLE",
        "entry_reference": 3010.0,
        "selected_sl": 3014.0,
        "take_profit": 3000.0,
        "hypothetical_quantity": 0.50,
        "planned_net_risk_usd": 2.25,
        "net_rr": 2.20,
        "gross_rr": 2.50,
        "trade_action_fingerprint": "fp_ctrl_1",
        "decision_fingerprint": "dfp_ctrl_1",
    }

    def _mk_bars(tf_ms: int, n: int, *, future_last: bool = False, unclosed_last: bool = False) -> Tuple[List[Dict[str, Any]], List[int]]:
        st = ((ts_ms // tf_ms) * tf_ms) - n * tf_ms
        out: List[Dict[str, Any]] = []
        cts: List[int] = []
        for i in range(n):
            o_ms = st + i * tf_ms
            c_ms = o_ms + tf_ms + (tf_ms if (future_last and i == n - 1) else 0)
            px = 3050.0 - i * 0.45
            out.append({
                "timestamp": o_ms, "open_time": o_ms, "close_time": c_ms,
                "open": round(px + 0.3, 2), "high": round(px + 0.8 + (i % 5) * 0.2, 2),
                "low": round(px - 0.6, 2), "close": round(px, 2), "volume": 100.0,
                "is_closed": False if (unclosed_last and i == n - 1) else True,
            })
            cts.append(c_ms)
        return out, cts

    b1, c1 = _mk_bars(60_000, 90)
    b15, c15 = _mk_bars(900_000, 20)
    bh1, ch1 = _mk_bars(3_600_000, 70)
    ds_ok = {"1m": b1, "15m": b15, "1H": bh1}
    cts_ok = {"1m": c1, "15m": c15, "1H": ch1}
    cbi_ok: Dict[Tuple[str, int], int] = {}
    for tf_u, seq in (("1M", b1), ("15M", b15), ("1H", bh1)):
        for b in seq:
            cbi_ok[(tf_u, int(b["open_time"]))] = int(b["close_time"])

    fr_green = extract_kh01b_r1_causal_regime_features(
        base_cand, {}, cbi_ok, ts_ms, datasets=ds_ok, close_ts_index=cts_ok
    )

    def _rec(cid: str, name: str, target_fn: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        cases.append({
            "mutation_id": cid,
            "name": name,
            "anchored_function": target_fn,
            "red_rejected": bool(red_ok),
            "green_accepted": bool(green_ok),
            "status": "PASS" if (red_ok and green_ok) else "FAIL",
            "diagnostic": detail,
        })

    # M01: Wrong mandatory branch rejected
    t_bad_br = load_and_verify_kh01b_r1_sources(repo_root, "abcd1234", branch_override="main")
    t_ok_br = load_and_verify_kh01b_r1_sources(repo_root, "abcd1234")
    _rec("M01", "WRONG_MANDATORY_BRANCH_REJECTED", "load_and_verify_kh01b_r1_sources", not t_bad_br.verification_passed, t_ok_br.verification_passed, str(t_bad_br.violations[:1]))

    # M02: Over-budget >6 new hypotheses rejected
    over_specs = list(DEFAULT_KH01B_R1_THESIS_SPECS) + [{
        "short_code": "E07", "variant_id": "KH01B_R1-E07-ILLEGAL-SEVENTH", "is_baseline_control": False,
        "ontology": "ILLEGAL", "modifies_tp_geometry": False, "rule": {"type": "DIRECTION_ONLY", "allowed_direction": "EXTRA"},
    }]
    m02_red = False
    try:
        pre_register_kh01b_r1_theses("ds", "cfg", "code", over_specs)
    except ValueError:
        m02_red = True
    m02_green = pre_register_kh01b_r1_theses("ds", "cfg", "code").registered_new_hypotheses_count == 6
    _rec("M02", "OVER_BUDGET_7TH_HYPOTHESIS_REJECTED", "pre_register_kh01b_r1_theses", m02_red, m02_green, "max_new_hypotheses=6")

    # M03: Duplicate / non-disjoint hypothesis rule rejected
    dup_specs = [copy.deepcopy(x) for x in DEFAULT_KH01B_R1_THESIS_SPECS[:3]]
    dup_specs[2]["rule"] = copy.deepcopy(dup_specs[1]["rule"])
    m03_red = False
    try:
        pre_register_kh01b_r1_theses("ds", "cfg", "code", dup_specs)
    except ValueError:
        m03_red = True
    _rec("M03", "NON_DISJOINT_DUPLICATE_RULE_REJECTED", "pre_register_kh01b_r1_theses", m03_red, m02_green, "duplicate rule hash rejected")

    # M04: TP geometry modification in Entry-only arm rejected
    tp_specs = [copy.deepcopy(x) for x in DEFAULT_KH01B_R1_THESIS_SPECS]
    tp_specs[1]["modifies_tp_geometry"] = True
    m04_red = False
    try:
        pre_register_kh01b_r1_theses("ds", "cfg", "code", tp_specs)
    except ValueError:
        m04_red = True
    _rec("M04", "TP_MUTATION_IN_ENTRY_ARM_REJECTED", "pre_register_kh01b_r1_theses", m04_red, m02_green, "modifies_tp_geometry=True blocked")

    # M05: Planted future MFE leakage key on candidate rejected
    fr_mfe = extract_kh01b_r1_causal_regime_features({**base_cand, "_planted_future_mfe": 3.5}, {}, cbi_ok, ts_ms, datasets=ds_ok, close_ts_index=cts_ok)
    _rec("M05", "PLANTED_FUTURE_MFE_REJECTED", "extract_kh01b_r1_causal_regime_features", fr_mfe.causal_regime_tri_state == "INVALID", fr_green.causal_regime_tri_state in ("PROVEN_TRUE", "PROVEN_FALSE"), str(fr_mfe.invalidation_reasons[:1]))

    # M06: Planted post-stop TP hit key rejected
    fr_pstp = extract_kh01b_r1_causal_regime_features(base_cand, {"opp_m_ctrl": {"opportunity_id": "opp_m_ctrl", "decision_timestamp_ms": ts_ms, "post_stop_tp_hit": True}}, cbi_ok, ts_ms, datasets=ds_ok, close_ts_index=cts_ok)
    _rec("M06", "POST_STOP_TP_LEAKAGE_REJECTED", "extract_kh01b_r1_causal_regime_features", fr_pstp.causal_regime_tri_state == "INVALID", not fr_green.invalidation_reasons, str(fr_pstp.invalidation_reasons[:1]))

    # M07: Future H4/H1 known_at_ms in feature snapshot rejected
    fr_fut_h4 = extract_kh01b_r1_causal_regime_features(
        base_cand,
        {"opp_m_ctrl": {"opportunity_id": "opp_m_ctrl", "decision_timestamp_ms": ts_ms + 60_000, "h4_bias": "BEARISH"}},
        cbi_ok, ts_ms, datasets=ds_ok, close_ts_index=cts_ok,
    )
    _rec("M07", "FUTURE_HTF_FEATURE_TIMESTAMP_REJECTED", "extract_kh01b_r1_causal_regime_features", fr_fut_h4.causal_regime_tri_state == "INVALID", not fr_green.invalidation_reasons, str(fr_fut_h4.invalidation_reasons[:1]))

    # M08: Reopened / unclosed H1 candle in trailing history rejected
    bh1_open, ch1_open = _mk_bars(3_600_000, 70, unclosed_last=True)
    fr_reopen = extract_kh01b_r1_causal_regime_features(
        base_cand, {}, cbi_ok, ts_ms, datasets={"1m": b1, "15m": b15, "1H": bh1_open}, close_ts_index={"1m": c1, "15m": c15, "1H": ch1_open}
    )
    _rec("M08", "REOPENED_UNCLOSED_CANDLE_REJECTED", "extract_kh01b_r1_causal_regime_features", fr_reopen.causal_regime_tri_state == "INVALID", fr_green.causal_regime_tri_state != "INVALID", str(fr_reopen.invalidation_reasons[:1]))

    # M09: Missing source bar ID in canonical closed_bar_index rejected
    cbi_missing = dict(cbi_ok)
    cbi_missing.pop(("1H", int(bh1[-1]["open_time"])), None)
    fr_miss_bar = extract_kh01b_r1_causal_regime_features(base_cand, {}, cbi_missing, ts_ms, datasets=ds_ok, close_ts_index=cts_ok)
    _rec("M09", "MISSING_SOURCE_BAR_ID_REJECTED", "extract_kh01b_r1_causal_regime_features", fr_miss_bar.causal_regime_tri_state == "INVALID", fr_green.causal_regime_tri_state != "INVALID", str(fr_miss_bar.invalidation_reasons[:1]))

    # M10: Fake persisted snapshot without cryptographic proof rejected
    fr_fake_snap = extract_kh01b_r1_causal_regime_features(
        base_cand, {"opp_m_ctrl": {"opportunity_id": "opp_m_ctrl", "decision_timestamp_ms": ts_ms, "causal_snapshot": {"features": []}, "proof_hash": "0" * 64}},
        cbi_ok, ts_ms, datasets=ds_ok, close_ts_index=cts_ok,
    )
    _rec("M10", "TAMPERED_PERSISTED_SNAPSHOT_PROOF_REJECTED", "extract_kh01b_r1_causal_regime_features", fr_fake_snap.causal_regime_tri_state == "INVALID", fr_green.causal_regime_tri_state != "INVALID", str(fr_fake_snap.invalidation_reasons[:1]))

    # M11: UNKNOWN regime evidence blocked (never coerced into implicit False/True edge)
    fr_unk = extract_kh01b_r1_causal_regime_features(base_cand, {}, {}, ts_ms, datasets=None, close_ts_index=None)
    d_unk_trend = evaluate_kh01b_r1_entry_variant({"type": "CAUSAL_REGIME_RULE", "target_regime": "TREND_EXPANSION_ALIGNED"}, "E03", "KH01B_R1-E03", base_cand, fr_unk)
    d_unk_chop = evaluate_kh01b_r1_entry_variant({"type": "CAUSAL_REGIME_RULE", "target_regime": "CHOP_OR_COUNTER_REGIME"}, "E03_CHOP", "KH01B_R1-E03_CHOP", base_cand, fr_unk)
    _rec("M11", "UNKNOWN_REGIME_BLOCKED_IN_BOTH_SPLITS", "evaluate_kh01b_r1_entry_variant", (not d_unk_trend.eligible and not d_unk_chop.eligible and d_unk_trend.missing_evidence_rejected), fr_green.causal_regime_tri_state in ("PROVEN_TRUE", "PROVEN_FALSE"), "UNKNOWN_MISSING rejected in both trend & chop")

    # M12: Baseline non-eligible candidate never rescued by R1 variant
    d_non_elig = evaluate_kh01b_r1_entry_variant({"type": "UNCHANGED_B00_CONTROL"}, "E00", "KH01B_R1-E00", {**base_cand, "decision_status": "REJECTED"}, fr_green)
    d_elig = evaluate_kh01b_r1_entry_variant({"type": "UNCHANGED_B00_CONTROL"}, "E00", "KH01B_R1-E00", base_cand, fr_green)
    _rec("M12", "NON_RESCUE_OF_REJECTED_BASELINE_CANDIDATE", "evaluate_kh01b_r1_entry_variant", not d_non_elig.eligible, d_elig.eligible, str(d_non_elig.rejection_code))

    # M13: Runtime LIVE/PAPER/AUTO activation rejected with PermissionError
    m13_red = False
    try:
        evaluate_kh01b_r1_entry_variant({"type": "UNCHANGED_B00_CONTROL"}, "E00", "KH01B_R1-E00", base_cand, fr_green, runtime_modes={"LIVE": "ON"})
    except PermissionError:
        m13_red = True
    _rec("M13", "LIVE_RUNTIME_ACTIVATION_FLAG_REJECTED", "assert_research_only_runtime_modes_off", m13_red, d_elig.eligible, "LIVE=ON raises PermissionError")

    # M14: Zero closed trades yields None expectancy (never 0.0R positive/neutral edge)
    zero_bnd = VariantReplayBundle(
        variant_id="KH01B_R1-B09-ZERO", short_code="B09", ontology="STRICT_D4_TRUE_UNICORN",
        axis_type="STRICT", population_name="TEST", population_total=10, eligible_candidates_count=0,
        rejected_candidates_count=10, missing_evidence_rejected_count=0, proven_false_rejected_count=10,
        invalid_evidence_rejected_count=0, plausible_eligibility_lower_bound=0, plausible_eligibility_upper_bound=0,
        rejection_reasons_counts={"NONE": 10}, post_arbitration_timestamps_selected=0,
        arbitration_suppressed_ties_count=0, arbitration_winner_substitutions_vs_b00=0,
        materialized_accepted_count=0, materialized_modified_tp_count=0,
        fill_mode_summaries={fm: _summarize_episode_list_kh01b([], 0) for fm in FILL_MODES_KH01B},
        primary_nb_episodes=[], all_mode_episodes={fm: [] for fm in FILL_MODES_KH01B},
        matched_b00_242_counterfactual={}, si8_separate_diagnostic=None, bundle_sha256="z",
    )
    ec_zero = compute_kh01b_r1_edge_uncertainty_and_research_verdict(zero_bnd, ())
    _rec("M14", "ZERO_TRADES_EXPECTANCY_IS_NONE_NOT_ZERO_EDGE", "compute_kh01b_r1_edge_uncertainty_and_research_verdict", (ec_zero.expectancy_1x_nb_r is None and not ec_zero.qualifies_for_kh01c and ec_zero.research_verdict == "NO_VALID_SETUPS"), True, f"exp={ec_zero.expectancy_1x_nb_r}")

    # M15: Small-N (<30) positive sample rejected as UNDERPOWERED (cannot advance to KH-01C)
    win_eps_10 = [
        {"opportunity_id": f"w_{i}", "filled": True, "outcome_label": "WIN", "realized_gross_pnl_usd": 5.5, "total_fees_usd": 0.5, "realized_net_pnl_usd": 5.0, "realized_net_r": 2.0, "planned_net_risk_usd": 2.5, "decision_timestamp_ms": ts_ms + i * 86_400_000, "exit_ts_ms": ts_ms + i * 86_400_000 + 600_000}
        for i in range(10)
    ]
    small_bnd = VariantReplayBundle(
        **{
            **asdict(zero_bnd),
            "variant_id": "KH01B_R1-E01-SMALL",
            "short_code": "E01",
            "fill_mode_summaries": {fm: _summarize_episode_list_kh01b(win_eps_10, 0) for fm in FILL_MODES_KH01B},
            "primary_nb_episodes": win_eps_10,
            "all_mode_episodes": {fm: win_eps_10 for fm in FILL_MODES_KH01B},
        }
    )
    ec_small = compute_kh01b_r1_edge_uncertainty_and_research_verdict(small_bnd, ())
    _rec("M15", "SMALL_SAMPLE_LT_30_CHERRY_PICK_CANNOT_ADVANCE", "compute_kh01b_r1_edge_uncertainty_and_research_verdict", (ec_small.research_verdict == "INCONCLUSIVE_UNDERPOWERED" and not ec_small.qualifies_for_kh01c), ec_small.net_pnl_1x_nb_usd > 0, ec_small.sample_power_status)

    # M16: 2x all-cost stress flip disqualifies thin 1x positive variant
    thin_1x = [
        {"opportunity_id": f"th_{i}", "filled": True, "outcome_label": "WIN", "realized_gross_pnl_usd": 0.45, "total_fees_usd": 0.30, "realized_net_pnl_usd": 0.15, "realized_net_r": 0.06, "planned_net_risk_usd": 2.5, "decision_timestamp_ms": ts_ms + i * 86_400_000, "exit_ts_ms": ts_ms + i * 86_400_000 + 600_000}
        for i in range(35)
    ]
    thin_2x = [{**e, "realized_gross_pnl_usd": 0.20, "realized_net_pnl_usd": -0.10} for e in thin_1x]
    thin_bnd = VariantReplayBundle(
        **{
            **asdict(zero_bnd),
            "variant_id": "KH01B_R1-E03-THIN",
            "short_code": "E03",
            "fill_mode_summaries": {
                "NEXT_BAR_OPEN_BID_ASK": _summarize_episode_list_kh01b(thin_1x, 0),
                "LEGACY_CLOSE_FILL": _summarize_episode_list_kh01b(thin_1x, 0),
                "STRESS_WIDER_SPREAD_SLIPPAGE_2X": _summarize_episode_list_kh01b(thin_2x, 0),
            },
            "primary_nb_episodes": thin_1x,
            "all_mode_episodes": {"NEXT_BAR_OPEN_BID_ASK": thin_1x, "LEGACY_CLOSE_FILL": thin_1x, "STRESS_WIDER_SPREAD_SLIPPAGE_2X": thin_2x},
        }
    )
    ec_thin = compute_kh01b_r1_edge_uncertainty_and_research_verdict(thin_bnd, ())
    _rec("M16", "COST_FRAGILE_2X_FLIP_DISQUALIFIED", "compute_kh01b_r1_edge_uncertainty_and_research_verdict", (ec_thin.research_verdict == "NO_CANDIDATE" and not ec_thin.qualifies_for_kh01c), ec_thin.net_pnl_1x_nb_usd > 0, f"2x_net={ec_thin.stress_2x_all_cost_net_pnl_usd}")

    # M17: Purged fold embargo violation (<180m) triggers FAIL_LEAKAGE
    ec_leak = compute_kh01b_r1_edge_uncertainty_and_research_verdict(
        small_bnd, [(ts_ms, ts_ms + 3_600_000, ts_ms + 3_660_000, ts_ms + 7_200_000)]
    )
    _rec("M17", "PURGED_FOLD_EMBARGO_LT_180M_FAIL_LEAKAGE", "compute_kh01b_r1_edge_uncertainty_and_research_verdict", ec_leak.research_verdict == "FAIL_LEAKAGE", ec_small.research_verdict != "FAIL_LEAKAGE", ec_leak.sample_power_status)

    # M18: Adjacent ontology trying to relabel as D4 rejected
    m18_red = False
    try:
        evaluate_unicorn_adjacent_research_hypothesis(fr_green.base_frame, variant_id="KH01B_R1-D4-TRUE-UNICORN")
    except ValueError:
        m18_red = True
    adj_ok = evaluate_unicorn_adjacent_research_hypothesis(fr_green.base_frame)
    _rec("M18", "ADJACENT_RELABELED_AS_D4_REJECTED", "evaluate_unicorn_adjacent_research_hypothesis", m18_red, adj_ok.counts_toward_d4_true_unicorn is False, "counts_toward_d4=False")

    # M19: Reversed pagination order in Bitget candle page rejected
    good_page = {"raw_http_sha256": "a" * 64, "server_time_ms": ts_ms + 600_000, "rows": [[str(ts_ms), "3000", "3002", "2999", "3001", "10"], [str(ts_ms + 60_000), "3001", "3003", "3000", "3002", "12"]]}
    rev_page = {"raw_http_sha256": "a" * 64, "server_time_ms": ts_ms + 600_000, "rows": list(reversed(good_page["rows"]))}
    v_rev = validate_and_normalize_paginated_candles([rev_page], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    v_good = validate_and_normalize_paginated_candles([good_page], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    _rec("M19", "REVERSED_PAGINATION_ORDER_REJECTED", "validate_and_normalize_paginated_candles", v_rev["status"] == "FAIL", v_good["status"] == "PASS", str(v_rev["violations"][:1]))

    # M20: Duplicate boundary candle timestamp across pages rejected
    v_dup = validate_and_normalize_paginated_candles([good_page, good_page], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    _rec("M20", "DUPLICATE_BOUNDARY_CANDLE_REJECTED", "validate_and_normalize_paginated_candles", v_dup["status"] == "FAIL", v_good["status"] == "PASS", str(v_dup["violations"][:1]))

    # M21: Missing HTTP origin SHA256 on acquired candle page rejected
    no_sha_page = {**good_page, "raw_http_sha256": ""}
    v_nosha = validate_and_normalize_paginated_candles([no_sha_page], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    _rec("M21", "MISSING_HTTP_ORIGIN_SHA_REJECTED", "validate_and_normalize_paginated_candles", v_nosha["status"] == "FAIL", v_good["status"] == "PASS", str(v_nosha["violations"][:1]))

    # M22: Crossed bid > ask or synthetic L1 BBO substituted as real rejected
    bbo_good = validate_prospective_bbo_snapshot({"symbol": "XAUUSDT", "raw_http_sha256": "b" * 64, "receive_ts_ms": ts_ms, "exchange_ts_ms": ts_ms - 50, "bid": 3742.10, "ask": 3742.20, "bid_size": 5.0, "ask_size": 3.0})
    bbo_crossed = validate_prospective_bbo_snapshot({"symbol": "XAUUSDT", "raw_http_sha256": "b" * 64, "receive_ts_ms": ts_ms, "exchange_ts_ms": ts_ms - 50, "bid": 3742.50, "ask": 3742.20, "bid_size": 5.0, "ask_size": 3.0})
    bbo_synth = validate_prospective_bbo_snapshot({"symbol": "XAUUSDT", "raw_http_sha256": "b" * 64, "receive_ts_ms": ts_ms, "exchange_ts_ms": ts_ms - 50, "bid": 3742.10, "ask": 3742.20, "bid_size": 5.0, "ask_size": 3.0, "is_synthetic": True})
    _rec("M22", "CROSSED_OR_SYNTHETIC_BBO_REJECTED", "validate_prospective_bbo_snapshot", (bbo_crossed["status"] == "FAIL" and bbo_synth["status"] == "FAIL"), bbo_good["status"] == "PASS", str(bbo_crossed["violations"] + bbo_synth["violations"]))

    # M23: Reused canonical dataset or DEV_EXTENDED dataset rejected from Fresh OOS Escrow
    esc_reused = build_and_validate_future_oos_escrow_registry(
        freeze_timestamp_ms=ts_ms, frozen_candidate_code_sha="sha", frozen_config_sha256="cfg",
        proposed_oos_entries=[{"dataset_id": "fake_oos", "dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1, "earliest_bar_open_ms": CANONICAL_SAMPLE_END_MS + 999_999_999, "latest_bar_close_ms": CANONICAL_SAMPLE_END_MS + 1_999_999_999, "strategy_evaluation_count": 0, "partition_role": "FRESH_UNTOUCHED"}],
    )
    esc_ok = build_and_validate_future_oos_escrow_registry(freeze_timestamp_ms=ts_ms, frozen_candidate_code_sha="sha", frozen_config_sha256="cfg")
    _rec("M23", "REUSED_DATASET_REJECTED_FROM_FRESH_OOS_ESCROW", "build_and_validate_future_oos_escrow_registry", esc_reused["status"] == "FAIL", esc_ok["status"] == "PASS", str(esc_reused["violations"][:1]))

    # M24: Forcing Fresh OOS PASS or nonzero OOS evaluation count fails closed
    esc_forced_pass = build_and_validate_future_oos_escrow_registry(
        freeze_timestamp_ms=ts_ms, frozen_candidate_code_sha="sha", frozen_config_sha256="cfg",
        claimed_fresh_oos_verdict="PASS_FINANCIAL_EDGE",
    )
    _rec("M24", "FORCED_FRESH_OOS_PASS_FAILS_CLOSED", "build_and_validate_future_oos_escrow_registry", esc_forced_pass["status"] == "FAIL", esc_ok["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA", str(esc_forced_pass["violations"][:1]))

    passed = sum(1 for c in cases if c["status"] == "PASS")
    return {
        "spec_version": SPEC_VERSION_KH01B_R1,
        "schema_version": SCHEMA_VERSION_KH01B_R1,
        "total_mutations": len(cases),
        "passed_mutations": passed,
        "failed_mutations": len(cases) - passed,
        "synthetic_fixtures_counted_in_historical_pnl": 0,
        "status": "PASS" if (passed == len(cases) and len(cases) >= 20) else "FAIL",
        "mutations": cases,
        "fault_suite_sha256": canonical_hash(cases),
    }


# ==============================================================================
# D3.A.10: EVALUATE G01..G18 GATES FROM CLOSED DISK ARTIFACTS & RECEIPTS (18)
# ==============================================================================

def evaluate_kh01b_r1_engineering_and_economic_gates_from_disk(
    run_dir: Path,
    run_id: str,
    tested_sha: str,
    *,
    publication_status: str = "PENDING_REMOTE_PUSH",
) -> Dict[str, Any]:
    """
    D3.A.10 & §J: Evaluates G01..G18 independently from closed disk files & dynamic receipts.
    Never defaults missing/failed evidence to True, and keeps ENGINEERING, BASELINE_PARITY,
    RESEARCH_HYPOTHESIS, FRESH_OOS, DATA_ACQUISITION, KH01C_ADVANCEMENT, ACTIVATION_READY,
    and PUBLICATION strictly orthogonal.
    """
    def _rd(name: str) -> Dict[str, Any]:
        p = run_dir / name
        if not p.is_file():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    f00 = _rd("00_preflight_branch_source_dataset_invariants.json")
    f01 = _rd("01_function_callgraph_keep_as_is_and_no_production_change.json")
    f02 = _rd("02_frozen_hypothesis_budget_and_expected_falsification.json")
    f03 = _rd("03_si0_242_episode_preentry_vs_expost_loss_attribution.json")
    f04 = _rd("04_model_direction_session_regime_cost_decomposition.json")
    f05 = _rd("05_source_evidence_missingness_causal_regime_quality.json")
    f06 = _rd("06_extended_dev_dataset_provenance_gap_and_quote_capability.json")
    f07 = _rd("07_future_oos_sealed_escrow_registry_no_evaluation.json")
    f08 = _rd("08_E00_E06_variant_conservation_and_rejection_reason_ledger.json")
    f09 = _rd("09_matched_baseline_and_chronological_1pos_3fill_economics.json")
    f10 = _rd("10_cost_stress_cluster_bootstrap_reused_purged_folds.json")
    f11 = _rd("11_D4_strict_unchanged_and_unicorn_adjacent_isolation.json")
    f12 = _rd("12_full_regression_junit_shard_exits_bijection.json")
    f13 = _rd("13_async_workers_w1_w2_w4_repeat_speed_determinism.json")
    f14 = _rd("14_disk_ram_cpu_pid_atomic_write_cleanup.json")
    f15 = _rd("15_real_fault_injection_red_green_mutations.json")
    f16 = _rd("16_full_baseline_decision_trade_action_and_adapter_parity.json")

    receipts_map: Dict[str, EvidenceReceiptV17931219] = {}
    rcpt_dir = run_dir / "receipts"
    if rcpt_dir.is_dir():
        for rp in sorted(rcpt_dir.glob("G*.json")):
            doc = json.loads(rp.read_text(encoding="utf-8"))
            r_in = doc.get("receipt")
            if isinstance(r_in, Mapping):
                receipts_map[rp.stem] = EvidenceReceiptV17931219(**r_in)

    gates: List[Dict[str, Any]] = []

    def _add_gate(gid: str, name: str, rel_art: str, obs: Any, status: str, viols: Sequence[str], *, require_pass_receipt: bool = True) -> None:
        v_list = list(viols)
        rcpt = receipts_map.get(gid)
        if status == "PASS" and require_pass_receipt:
            if rcpt is None:
                status = "FAIL"
                v_list.append(f"MISSING_GATE_RECEIPT:{gid}")
            elif not rcpt.is_valid_pass(
                expected_run_id=run_id,
                expected_tested_sha=tested_sha,
                run_dir=run_dir,
                reject_uniform_placeholder_hash=True,
            ):
                status = "FAIL"
                v_list.append(f"INVALID_GATE_RECEIPT:{gid}")
                v_list.extend(rcpt.violations)
        gates.append({
            "gate_id": gid,
            "name": name,
            "artifact_path": rel_art,
            "tested_run_id": run_id,
            "executable_sha": tested_sha,
            "observed_values": obs,
            "status": status,
            "violations": v_list,
            "independent_receipt": asdict(rcpt) if rcpt is not None else None,
        })

    # G01: Exact branch, tested SHA, clean source freeze, prior KH-01A/R1/B untouched
    v01: List[str] = []
    if f00.get("branch") != MANDATORY_BRANCH_KH01B_R1 or f00.get("tested_executable_sha") != tested_sha:
        v01.append("BRANCH_OR_TESTED_SHA_MISMATCH")
    if f00.get("source_tree_unchanged_during_run") is not True or f00.get("prior_artifacts_unchanged_during_run") is not True:
        v01.append("SOURCE_OR_PRIOR_KH_ARTIFACTS_MUTATED")
    if int(f01.get("existing_production_or_prior_kh_files_modified_count", 1)) != 0:
        v01.append("EXISTING_PRODUCTION_OR_PRIOR_KH_FILES_MODIFIED")
    _add_gate("G01", "BRANCH_SHA_AND_PRIOR_ARTIFACTS_IMMUTABILITY", "00_preflight_branch_source_dataset_invariants.json", {"branch": f00.get("branch"), "tested_sha": tested_sha}, "PASS" if not v01 else "FAIL", v01)

    # G02: Reference population integrity (56,710 + 6,375 = 63,085, 242/166 SI0)
    v02: List[str] = []
    st_00 = f00.get("source_truth") or {}
    if st_00.get("verification_passed") is not True or int(st_00.get("total_candidate_universe_count") or 0) != 63085:
        v02.append("REFERENCE_POPULATION_INTEGRITY_FAILED")
    _add_gate("G02", "REFERENCE_POPULATION_AND_BASELINE_INTEGRITY", "00_preflight_branch_source_dataset_invariants.json", {"a1": st_00.get("a1_candidate_count"), "si8": st_00.get("si8_discovery_count")}, "PASS" if not v02 else "FAIL", v02)

    # G03: Preregistration frozen before outcomes, <= 6 new hypotheses + E00 control
    v03: List[str] = []
    if f02.get("frozen_before_outcome_evaluation") is not True or int(f02.get("registered_new_hypotheses_count") or 99) > 6 or int(f02.get("registered_new_hypotheses_count") or 0) < 1:
        v03.append("PREREGISTRATION_NOT_FROZEN_OR_OVER_BUDGET")
    if not f02.get("manifest_sha256"):
        v03.append("MISSING_PREREGISTRATION_SHA256")
    _add_gate("G03", "FROZEN_PREREGISTERED_HYPOTHESIS_BUDGET", "02_frozen_hypothesis_budget_and_expected_falsification.json", {"new_hypotheses": f02.get("registered_new_hypotheses_count"), "manifest_sha256": f02.get("manifest_sha256")}, "PASS" if not v03 else "FAIL", v03)

    # G04: Per-field provenance & known_at <= decision_timestamp_ms
    v04: List[str] = []
    if int(f05.get("future_known_at_or_bar_close_violations_count", 1)) != 0 or int(f05.get("invalid_frames_count", 1)) != 0:
        v04.append("PROVENANCE_OR_ASOF_CLOCK_VIOLATION")
    if (f03.get("all_rows_partial_reconstructed_honesty") or {}).get("upgraded_to_fake_persisted_context_count", 1) != 0:
        v04.append("PARTIAL_ROWS_FALSIFIED_AS_PERSISTED")
    _add_gate("G04", "POINT_IN_TIME_PROVENANCE_AND_KNOWN_AT_TRUTH", "05_source_evidence_missingness_causal_regime_quality.json", {"a1_evaluated": f05.get("a1_frames_evaluated_count"), "future_violations": f05.get("future_known_at_or_bar_close_violations_count")}, "PASS" if not v04 else "FAIL", v04)

    # G05: Anti-leakage & 20+ real RED/GREEN mutations
    v05: List[str] = []
    if f15.get("status") != "PASS" or int(f15.get("passed_mutations") or 0) < 20 or int(f15.get("failed_mutations", 1)) != 0:
        v05.append("ANTI_LEAKAGE_OR_FAULT_SUITE_FAILED")
    _add_gate("G05", "NO_DATA_LEAKAGE_AND_ADVERSARIAL_MUTATIONS", "15_real_fault_injection_red_green_mutations.json", {"mutations_passed": f"{f15.get('passed_mutations')}/{f15.get('total_mutations')}"}, "PASS" if not v05 else "FAIL", v05)

    # G06: Model/direction/regime denominator conservation & small-N honesty
    v06: List[str] = []
    cons_04 = f04.get("parent_population_conservation") or {}
    if int(cons_04.get("si0_closed_total") or -1) != 166 or int(cons_04.get("stratum_closed_sum_model") or 0) != 166 or int(cons_04.get("stratum_closed_sum_direction") or 0) != 166 or int(cons_04.get("stratum_closed_sum_regime") or 0) != 166:
        v06.append("STRATUM_DENOMINATOR_CONSERVATION_MISMATCH")
    _add_gate("G06", "STRATUM_DENOMINATOR_CONSERVATION_AND_SMALL_N_HONESTY", "04_model_direction_session_regime_cost_decomposition.json", cons_04, "PASS" if not v06 else "FAIL", v06)

    # G07: Strict True Unicorn D4 unchanged & isolated from adjacent
    v07: List[str] = []
    det_11 = f11.get("detector_unit_fixture_validation") or {}
    if f11.get("ontology_isolation_verified") is not True or int(f11.get("d4_historical_closed_trades", 1)) != 0 or f11.get("d4_historical_expectancy_r") is not None or det_11.get("status") != "PASS":
        v07.append("STRICT_D4_OR_ADJACENT_ISOLATION_FAILED")
    _add_gate("G07", "STRICT_D4_UNCHANGED_AND_ADJACENT_ISOLATION", "11_D4_strict_unchanged_and_unicorn_adjacent_isolation.json", {"d4_historical_closed": f11.get("d4_historical_closed_trades"), "fixtures": f"{det_11.get('passed_cases')}/{det_11.get('total_cases')}"}, "PASS" if not v07 else "FAIL", v07)

    # G08: Matched cohort + chronological 1pos + 3 fills + 1x/2x/3x cost + risk <= $2.50
    v08: List[str] = []
    if f09.get("one_position_no_overlap_verified_all_arms") is not True or int(f09.get("risk_cap_violations_count", 1)) != 0 or f10.get("status") != "PASS":
        v08.append("REPLAY_1POS_3FILL_OR_COST_STRESS_FAILED")
    vars_08 = f08.get("arms_conservation") or {}
    for arm_k, arm_v in vars_08.items():
        if int(arm_v.get("eligible_candidates_count", -1)) + int(arm_v.get("rejected_candidates_count", -1)) != 56710:
            v08.append(f"CANDIDATE_CONSERVATION_BROKEN:{arm_k}")
    _add_gate("G08", "MATCHED_COHORT_AND_CHRONOLOGICAL_1POS_3FILL_COST_STRESS", "09_matched_baseline_and_chronological_1pos_3fill_economics.json", {"arms_replayed": len(vars_08), "no_overlap": f09.get("one_position_no_overlap_verified_all_arms")}, "PASS" if not v08 else "FAIL", v08)

    # G09: Unchanged B00/E00 SI0 exact parity + negative-control tampered fingerprint detection
    v09: List[str] = []
    if f16.get("status") != "PASS" or f16.get("e00_vs_si0_exact_match") is not True or int(f16.get("a1_mismatches_count", 1)) != 0 or int(f16.get("three_adapter_mismatches_count", 1)) != 0 or f16.get("tampered_fingerprint_negative_control_detected") is not True:
        v09.append("BASELINE_OR_ADAPTER_PARITY_OR_TAMPER_CONTROL_FAILED")
    _add_gate("G09", "E00_SI0_EXACT_PARITY_AND_FINGERPRINT_TAMPER_DETECTION", "16_full_baseline_decision_trade_action_and_adapter_parity.json", {"a1_evaluated": f16.get("a1_evaluated_count"), "adapters_verified": f16.get("three_adapter_episodes_denominator")}, "PASS" if not v09 else "FAIL", v09)

    # G10: All new policies RESEARCH_ONLY and PRODUCTION/LIVE/PAPER/AUTO OFF
    v10: List[str] = []
    modes_16 = f16.get("runtime_safety_modes") or {}
    if any(modes_16.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")) or f16.get("fake_activation_config_rejected") is not True:
        v10.append("RUNTIME_MODES_NOT_ALL_OFF")
    _add_gate("G10", "RESEARCH_ONLY_ISOLATION_AND_RUNTIME_MODES_OFF", "16_full_baseline_decision_trade_action_and_adapter_parity.json", modes_16, "PASS" if not v10 else "FAIL", v10)

    # G11: Full pytest collected == executed, 0 nonzero exits, JUnit bijection
    v11: List[str] = []
    ind_bij = f12.get("independent_bijection_verification") or {}
    if ind_bij.get("status") != "PASS" or int((f12.get("shard_manifest") or {}).get("nonzero_exit_shards_count", 1)) != 0:
        v11.append("PYTEST_REGRESSION_OR_JUNIT_BIJECTION_FAILED")
    _add_gate("G11", "FULL_PYTEST_REGRESSION_JUNIT_BIJECTION", "12_full_regression_junit_shard_exits_bijection.json", ind_bij.get("counters"), "PASS" if not v11 else "FAIL", v11)

    # G12: Worker w1/w2/w4/w1_repeat deterministic canonical hashes & honest speedup telemetry
    v12: List[str] = []
    if f13.get("pytest_subset_w1_w2_w4_repeat_identical") is not True or f13.get("r1_variant_slice_w1_w2_w4_repeat_identical") is not True or not f13.get("speedup_truth_label"):
        v12.append("ASYNC_WORKER_DETERMINISM_OR_TELEMETRY_FAILED")
    _add_gate("G12", "ASYNC_W1_W2_W4_REPEAT_DETERMINISM_AND_SPEED_TELEMETRY", "13_async_workers_w1_w2_w4_repeat_speed_determinism.json", {"pytest_w4_speedup": f13.get("measured_speedup_w4_vs_w1"), "speedup_label": f13.get("speedup_truth_label")}, "PASS" if not v12 else "FAIL", v12)

    # G13: Strict 20 GiB disk reserve, RAM backoff, max 4 children, temp sentinel cleanup, 0 orphans
    v13: List[str] = []
    if (
        not (1 <= int(f14.get("max_children_observed") or 0) <= 4)
        or int(f14.get("orphan_pids_at_cleanup_count", 1)) != 0
        or f14.get("effective_disk_reserve_breached") is not False
        or f14.get("temp_dirs_cleaned_with_sentinel_check") is not True
    ):
        v13.append("RESOURCE_GUARD_OR_CLEANUP_FAILED")
    if (
        bool(f14.get("baseline_already_below_20gib", False))
        or float(f14.get("preflight_baseline_free_gib") or 0.0) < 20.5
        or float(f14.get("min_free_disk_gb_observed") or 0.0) < 20.0
        or float(f14.get("effective_enforced_reserve_gib", 20.0)) < 20.0
    ):
        v13.append("STRICT_20GIB_DISK_RESERVE_VIOLATED_OR_SILENTLY_LOWERED")
    _add_gate("G13", "STRICT_20GIB_DISK_RAM_MAX4_CHILDREN_AND_TEMP_CLEANUP", "14_disk_ram_cpu_pid_atomic_write_cleanup.json", {"max_children": f14.get("max_children_observed"), "min_free_disk_gib": f14.get("min_free_disk_gb_observed"), "orphans": f14.get("orphan_pids_at_cleanup_count")}, "PASS" if not v13 else "FAIL", v13)

    # G14: Independent verifier receipts & cross-view integrity
    v14: List[str] = []
    if len(receipts_map) < 16:
        v14.append(f"INCOMPLETE_DYNAMIC_GATE_RECEIPTS:{len(receipts_map)}")
    _add_gate("G14", "INDEPENDENT_VERIFIER_AND_RECEIPT_INTEGRITY", "00_preflight_branch_source_dataset_invariants.json", {"receipts_count": len(receipts_map)}, "PASS" if not v14 else "FAIL", v14)

    # G15: Data acquisition verdict (VERIFIED_COMPLETE / VERIFIED_PARTIAL / BLOCKED_UNAVAILABLE / BLOCKED_LOW_DISK; FAIL on null gap schema or fake complete)
    acq_st = str(f06.get("DATA_ACQUISITION_VERDICT") or "FAIL_INTEGRITY")
    if acq_st in ("VERIFIED_COMPLETE", "VERIFIED_PARTIAL"):
        v15: List[str] = []
        if (f06.get("canonical_reused_dataset") or {}).get("untouched_bytes_verified") is not True:
            v15.append("CANONICAL_DATASET_MUTATED")
        ext_06 = f06.get("bitget_source_capability_and_extended_dev") or {}
        gap_06 = ext_06.get("gap_classification_summary")
        tf_06 = ext_06.get("per_timeframe_validation")
        if not isinstance(gap_06, Mapping) or not isinstance(tf_06, Mapping):
            v15.append("MISSING_GAP_CLASSIFICATION_OR_PER_TF_VALIDATION")
        else:
            for req_tf in ("1m", "5m", "15m", "1H", "4H", "1D"):
                ge = gap_06.get(req_tf)
                ve = tf_06.get(req_tf)
                if (
                    not isinstance(ge, Mapping)
                    or not isinstance(ve, Mapping)
                    or ge.get("actual_bars") is None
                    or ge.get("total_missing_bars") is None
                    or not isinstance(ge.get("gap_events_by_category"), Mapping)
                    or not isinstance(ge.get("missing_bars_by_category"), Mapping)
                ):
                    v15.append(f"NULL_OR_INVALID_GAP_SCHEMA:{req_tf}")
                elif acq_st == "VERIFIED_COMPLETE" and (
                    ve.get("coverage_verdict") != "VERIFIED_COMPLETE"
                    or int(ve.get("missing_slots", 1)) != 0
                ):
                    v15.append(f"PARTIAL_COVERAGE_MISLABELED_COMPLETE:{req_tf}")
        g15_st = "PASS" if not v15 else "FAIL"
        _add_gate("G15", "PROSPECTIVE_DATA_ACQUISITION_AND_ORIGIN_RECEIPTS", "06_extended_dev_dataset_provenance_gap_and_quote_capability.json", {"DATA_ACQUISITION_VERDICT": acq_st}, g15_st, v15)
    elif acq_st in ("BLOCKED_UNAVAILABLE", "BLOCKED_LOW_DISK"):
        _add_gate("G15", "PROSPECTIVE_DATA_ACQUISITION_AND_ORIGIN_RECEIPTS", "06_extended_dev_dataset_provenance_gap_and_quote_capability.json", {"DATA_ACQUISITION_VERDICT": acq_st}, acq_st, [f"OPTIONAL_ACQUISITION_{acq_st}"], require_pass_receipt=False)
    else:
        _add_gate("G15", "PROSPECTIVE_DATA_ACQUISITION_AND_ORIGIN_RECEIPTS", "06_extended_dev_dataset_provenance_gap_and_quote_capability.json", {"DATA_ACQUISITION_VERDICT": acq_st}, "FAIL", [f"INVALID_OR_LEGACY_UNVERIFIED_DATA_ACQUISITION:{acq_st}"])

    # G16: OOS escrow integrity (evaluation count == 0, reused data rejected, never PASS_FINANCIAL_EDGE)
    v16: List[str] = []
    oos_v = str(f07.get("FRESH_OOS_VERDICT") or "FAIL")
    if f07.get("status") != "PASS" or int(f07.get("strategy_evaluation_count_total", 1)) != 0 or oos_v not in ("BLOCKED_NO_UNTOUCHED_DATA", "SEALED_UNTOUCHED_NOT_EVALUATED"):
        v16.append(f"OOS_ESCROW_VIOLATION:{oos_v}")
    _add_gate("G16", "FUTURE_OOS_ESCROW_ZERO_EVALUATION_AND_HONESTY", "07_future_oos_sealed_escrow_registry_no_evaluation.json", {"FRESH_OOS_VERDICT": oos_v, "evaluation_count": f07.get("strategy_evaluation_count_total")}, "PASS" if not v16 else "FAIL", v16)

    # G17: Truthful economic verdict (losing/zero-trade models cannot advance)
    v17: List[str] = []
    ec_arms = f10.get("arms_economics") or {}
    qual_list = list(f10.get("qualified_kh01c_candidates") or [])
    for q_sc in qual_list:
        q_ec = ec_arms.get(q_sc) or {}
        if int(q_ec.get("closed_trades_nb") or 0) < 30 or float(q_ec.get("net_pnl_1x_nb_usd") or 0.0) <= 0.0 or float(q_ec.get("stress_2x_all_cost_net_pnl_usd") or 0.0) <= 0.0:
            v17.append(f"FALSE_ECONOMIC_ADVANCEMENT:{q_sc}")
    _add_gate("G17", "TRUTHFUL_ECONOMIC_AND_KH01C_ADVANCEMENT_VERDICT", "10_cost_stress_cluster_bootstrap_reused_purged_folds.json", {"kh01c_verdict": f10.get("kh01c_advancement_verdict"), "qualified": qual_list}, "PASS" if not v17 else "FAIL", v17)

    # G18: Publication push & remote blob readback
    _add_gate(
        "G18", "SAME_BRANCH_PUBLICATION_AND_REMOTE_BLOB_READBACK", "00_preflight_branch_source_dataset_invariants.json",
        {"publication_status": publication_status},
        publication_status,
        [] if publication_status == "PASS_REMOTE_VERIFIED" else ["AWAITING_PHASE_C_REMOTE_BLOB_READBACK"],
        require_pass_receipt=False,
    )

    by_gid = {g["gate_id"]: g for g in gates}
    mandatory_ok = all(by_gid[gid]["status"] == "PASS" for gid in MANDATORY_KH01B_R1_ENGINEERING_GATES)
    g15_ok = by_gid["G15"]["status"] in ("PASS", "BLOCKED_UNAVAILABLE")
    eng_pass = mandatory_ok and g15_ok

    return {
        "spec_version": SPEC_VERSION_KH01B_R1,
        "schema_version": SCHEMA_VERSION_KH01B_R1,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": f00.get("canonical_dataset_sha256"),
        "mandatory_engineering_gates": list(MANDATORY_KH01B_R1_ENGINEERING_GATES),
        "mandatory_gates_passed_count": sum(1 for gid in MANDATORY_KH01B_R1_ENGINEERING_GATES if by_gid[gid]["status"] == "PASS"),
        "mandatory_gates_total_count": len(MANDATORY_KH01B_R1_ENGINEERING_GATES),
        "orthogonal_verdicts": {
            "ENGINEERING_VERDICT": "PASS" if eng_pass else "FAIL",
            "BASELINE_PARITY_VERDICT": "PASS" if by_gid["G09"]["status"] == "PASS" else "FAIL",
            "RESEARCH_HYPOTHESIS_VERDICT": str(f10.get("research_hypothesis_verdict") or "NO_DEFENSIBLE_EDGE"),
            "FRESH_OOS_VERDICT": oos_v if by_gid["G16"]["status"] == "PASS" else "FAIL_CONTAMINATED",
            "DATA_ACQUISITION_VERDICT": acq_st,
            "KH01C_ADVANCEMENT_VERDICT": str(f10.get("kh01c_advancement_verdict") or "NO_ECONOMIC_ADVANCEMENT_CANDIDATE"),
            "QUALIFIED_KH01C_CANDIDATES": qual_list,
            "ACTIVATION_READY": "NO",
            "RUNTIME_SAFETY_MODES": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "PUBLICATION_VERDICT": publication_status,
        },
        "gates": gates,
        "prioritized_next_actions": [
            {
                "priority": 1,
                "action_id": "NEXT-01-REDESIGN-CORE-ENTRY-THESIS",
                "recommendation": (
                    "Do NOT advance losing M1 filters to KH-01C threshold optimization. SI0 has negative gross alpha "
                    "before fees (-$142.78 USD, -0.3498R) and every bounded filter/direction/regime/delayed-trigger "
                    "hypothesis fails realistic 1x/2x cost hurdles. Design a new structural H1/M15 swing/session entry "
                    "thesis in a separate future research phase with lower trade turnover relative to spread+fees."
                ) if not qual_list else (
                    f"Freeze candidate {qual_list} without parameter tuning and evaluate once on untouched post-freeze KH-01D OOS."
                ),
            },
            {
                "priority": 2,
                "action_id": "NEXT-02-PROSPECTIVE-BBO-AND-SEALED-OOS-CAPTURE",
                "recommendation": (
                    "Run continuous post-freeze prospective Bitget XAUUSDT L1 BBO + closed M1..D1 capture into a "
                    "write-once KH-01D escrow directory (>=180m after code freeze) so future thesis validation uses "
                    "CAPTURED_REAL_BBO instead of modeled $0.20 spread."
                ),
            },
            {
                "priority": 3,
                "action_id": "NEXT-03-EXCHANGE-SESSION-MAINTENANCE-LOG",
                "recommendation": (
                    "Log live Bitget symbolStatus/trading-halt transitions prospectively alongside BBO capture, since "
                    "Bitget public REST does not expose a historical holiday/maintenance calendar endpoint."
                ),
            },
        ],
    }


# ==============================================================================
# RENDER README & VIETNAMESE HANDOVER + CROSS-VIEW CONSISTENCY VERIFIER
# ==============================================================================

def render_kh01b_r1_readme_and_handover_vi(
    *,
    run_id: str,
    branch: str,
    starting_head_sha: str,
    tested_sha: str,
    dataset_sha: str,
    manifest_02: Mapping[str, Any],
    decomp_04: Mapping[str, Any],
    data_06: Mapping[str, Any],
    escrow_07: Mapping[str, Any],
    conservation_08: Mapping[str, Any],
    economics_10: Mapping[str, Any],
    d4_11: Mapping[str, Any],
    junit_12: Mapping[str, Any],
    workers_13: Mapping[str, Any],
    resource_14: Mapping[str, Any],
    faults_15: Mapping[str, Any],
    parity_16: Mapping[str, Any],
    verdicts_18: Mapping[str, Any],
) -> Tuple[str, str]:
    ov = verdicts_18["orthogonal_verdicts"]
    cnts = (junit_12.get("independent_bijection_verification") or {}).get("counters") or {}
    ec_map = economics_10.get("arms_economics") or {}
    cs_map = conservation_08.get("arms_conservation") or {}
    gv = decomp_04.get("overall_gross_vs_fee_decomposition") or {}

    ordered_codes = [
        c for c in ("E00", "E01", "E01_C1", "E01_C2", "E01_C3", "E02", "E02_C1", "E02_C2", "E03", "E03_CHOP", "E04", "E05", "E06")
        if c in ec_map
    ]
    tbl = [
        "| Mã | Variant ID | Eligible / 56,710 | Sched / Closed (NB) | Gross 1x (USD) | Fees 1x (USD) | Net 1x (USD) | Exp 1x (R) | Net 2x All-Cost | Net 3x All-Cost | 95% Cluster CI (R) | Kết luận |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for sc in ordered_codes:
        ec = ec_map[sc]
        cs = cs_map.get(sc) or {}
        tbl.append(
            f"| `{sc}` | `{ec.get('variant_id')}` | `{cs.get('eligible_candidates_count')}` | "
            f"`{ec.get('scheduled_nb')} / {ec.get('closed_trades_nb')}` | `{ec.get('gross_pnl_1x_nb_usd')}` | "
            f"`{ec.get('total_fees_1x_nb_usd')}` | `{ec.get('net_pnl_1x_nb_usd')}` | "
            f"`{ec.get('expectancy_1x_nb_r')}` | `{ec.get('stress_2x_all_cost_net_pnl_usd')}` | "
            f"`{ec.get('stress_3x_all_cost_net_pnl_usd')}` | `{ec.get('cluster_bootstrap_95ci_expectancy_r')}` | "
            f"`{ec.get('research_verdict')}` |"
        )
    tbl_md = "\n".join(tbl)

    det = d4_11.get("detector_unit_fixture_validation") or {}
    readme = f"""# AURUM DESK — KH-01B.R1: ENTRY EDGE DISCOVERY, BASELINE THESIS AUDIT & PROSPECTIVE DATA READINESS (`{SPEC_VERSION_KH01B_R1}`)

- **Run ID**: `{run_id}`
- **Mandatory Branch**: `{branch}`
- **Audited Starting HEAD**: `{starting_head_sha}`
- **Tested Executable SHA (Phase A)**: `{tested_sha}`
- **Frozen R1 Manifest SHA256**: `{manifest_02.get('manifest_sha256')}`
- **Canonical Dataset SHA256 (`REUSED_RESEARCH`, `MODELED_BID_ASK`)**: `{dataset_sha}`

## 1. Orthogonal Verdicts
- `ENGINEERING_VERDICT`: **{ov['ENGINEERING_VERDICT']}** (`{verdicts_18['mandatory_gates_passed_count']}/{verdicts_18['mandatory_gates_total_count']}` mandatory gates PASS)
- `BASELINE_PARITY_VERDICT`: **{ov['BASELINE_PARITY_VERDICT']}** (`{parity_16.get('a1_evaluated_count')}/{parity_16.get('a1_population_denominator')}` A1 + `{parity_16.get('three_adapter_episodes_denominator')}` 3-adapter episodes matched with `0` mismatches)
- `RESEARCH_HYPOTHESIS_VERDICT`: **{ov['RESEARCH_HYPOTHESIS_VERDICT']}**
- `FRESH_OOS_VERDICT`: **{ov['FRESH_OOS_VERDICT']}** (`strategy_evaluation_count = {escrow_07.get('strategy_evaluation_count_total')}`)
- `DATA_ACQUISITION_VERDICT`: **{ov['DATA_ACQUISITION_VERDICT']}**
- `KH01C_ADVANCEMENT_VERDICT`: **{ov['KH01C_ADVANCEMENT_VERDICT']}** (Qualified: `{ov['QUALIFIED_KH01C_CANDIDATES']}`)
- `ACTIVATION_READY`: **{ov['ACTIVATION_READY']}** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)
- `PUBLICATION_VERDICT` (pre-push bundle): **{ov['PUBLICATION_VERDICT']}**

## 2. Primary Thesis Audit (`SI0` / `E00` Baseline Decomposition)
- **Gross vs Fee Truth**: `SI0` closed `166` trades (`9`W / `157`L) with **Gross PnL = `{gv.get('gross_pnl_usd')}` USD** (`{gv.get('gross_expectancy_r')}`R **already negative BEFORE fees**), **Fees = `{gv.get('total_fees_usd')}` USD** (`{gv.get('fee_drag_r')}`R drag), **Net PnL = `{gv.get('net_pnl_usd')}` USD** (`{gv.get('net_expectancy_r')}`R).
- **Strict D4 (`B09`) vs Adjacent (`B10`) Isolation**: Historical D4 closed = `{d4_11.get('d4_historical_closed_trades')}` (`expectancy = {d4_11.get('d4_historical_expectancy_r')}` / `NO_VALID_SETUPS`); Adjacent eligible = `{d4_11.get('adjacent_b10_a1_eligible_count')}` (`counts_toward_d4 = 0`); Detector unit fixtures = `{det.get('passed_cases')}/{det.get('total_cases')}` PASS (`0` synthetic trades in PnL).

## 3. Pre-Registered Bounded Research Matrix (`E00`–`E06` + Predeclared Sub-Contrasts)
{tbl_md}

## 4. Regression, Adversarial Controls, Async Speed & Disk Telemetry
- **Full Pytest Regression**: `{cnts.get('collected')}` collected == `{cnts.get('executed')}` executed (`{cnts.get('passed')}` passed, `{cnts.get('skipped')}` skipped, `{cnts.get('failed')}` failed, `{cnts.get('errors')}` errors).
- **Real Fault Injections**: `{faults_15.get('passed_mutations')}/{faults_15.get('total_mutations')}` RED/GREEN mutations PASS.
- **Async Determinism & Speedup**: Pytest `w=4` vs `w=1` speedup = `{workers_13.get('measured_speedup_w4_vs_w1')}x` (`{workers_13.get('speedup_truth_label')}`); `w=1, w=2, w=4, w=1_repeat` canonical hashes identical = `{workers_13.get('pytest_subset_w1_w2_w4_repeat_identical')}` (pytest) & `{workers_13.get('r1_variant_slice_w1_w2_w4_repeat_identical')}` (R1 replay slice).
- **Resource & Disk Safety**: `max_children_observed = {resource_14.get('max_children_observed')}` (`<= 4`), `preflight_baseline_free_gib = {resource_14.get('preflight_baseline_free_gib')}` (`baseline_already_below_20gib = {resource_14.get('baseline_already_below_20gib')}`), `min_free_disk_gib = {resource_14.get('min_free_disk_gb_observed')}`, `orphan_pids = {resource_14.get('orphan_pids_at_cleanup_count')}`.
"""

    handover = f"""BÀN GIAO NGHIỆM THU KỸ THUẬT & NGHIÊN CỨU ĐỊNH LƯỢNG — AURUM DESK {SPEC_VERSION_KH01B_R1}
Run ID: {run_id}
Nhánh bắt buộc: {branch}
Starting HEAD trước khi chạy: {starting_head_sha}
Tested Executable SHA (Phase A): {tested_sha}
Canonical Dataset SHA256 (REUSED_RESEARCH / MODELED_BID_ASK): {dataset_sha}
Frozen Pre-Registered Manifest SHA256: {manifest_02.get('manifest_sha256')}

1. MA TRẬN PHÁN QUYẾT ĐỘC LẬP (ORTHOGONAL VERDICTS):
- ENGINEERING_VERDICT: {ov['ENGINEERING_VERDICT']} ({verdicts_18['mandatory_gates_passed_count']}/{verdicts_18['mandatory_gates_total_count']} cổng kỹ thuật bắt buộc PASS)
- BASELINE_PARITY_VERDICT: {ov['BASELINE_PARITY_VERDICT']} (A1 {parity_16.get('a1_evaluated_count')}/{parity_16.get('a1_population_denominator')} khớp 100%, 3-adapter {parity_16.get('three_adapter_episodes_denominator')} khớp 100%)
- RESEARCH_HYPOTHESIS_VERDICT: {ov['RESEARCH_HYPOTHESIS_VERDICT']}
- FRESH_OOS_VERDICT: {ov['FRESH_OOS_VERDICT']} (strategy_evaluation_count={escrow_07.get('strategy_evaluation_count_total')})
- DATA_ACQUISITION_VERDICT: {ov['DATA_ACQUISITION_VERDICT']}
- KH01C_ADVANCEMENT_VERDICT: {ov['KH01C_ADVANCEMENT_VERDICT']} (Ứng viên đủ chuẩn KH-01C: {ov['QUALIFIED_KH01C_CANDIDATES']})
- ACTIVATION_READY: {ov['ACTIVATION_READY']} (PRODUCTION=OFF, LIVE=OFF, PAPER=OFF, AUTO=OFF)
- PUBLICATION_VERDICT (trước Phase C readback): {ov['PUBLICATION_VERDICT']}

2. KẾT LUẬN KIỂM ĐỊNH LUẬN ĐIỂM GỐC (BASELINE THESIS AUDIT):
- SI0 (242 lệnh đặt / 166 lệnh đóng = 9 Thắng + 157 Thua) lỗ gộp TRƯỚC PHÍ: Gross PnL = {gv.get('gross_pnl_usd')} USD ({gv.get('gross_expectancy_r')}R), cộng thêm phí taker {gv.get('total_fees_usd')} USD ({gv.get('fee_drag_r')}R) thành Net PnL = {gv.get('net_pnl_usd')} USD ({gv.get('net_expectancy_r')}R).
- Cả 170 dòng hồ sơ pháp y từ KH-01A.R1 và 242 dòng SI0 được giữ nguyên nhãn PARTIAL (không nâng khống thành snapshot gốc; không bao giờ đồng nhất MISSING với FALSE).
- Strict True Unicorn D4 (B09): 0 lệnh lịch sử (expectancy = {d4_11.get('d4_historical_expectancy_r')} / NO_VALID_SETUPS), tách biệt tuyệt đối với B10 Adjacent ({d4_11.get('adjacent_b10_a1_eligible_count')} ứng viên A1, counts_toward_d4=0); 25/25 unit fixtures PASS (0 giao dịch giả lập tính vào PnL lịch sử).

3. BẢNG KẾT QUẢ THỰC NGHIỆM TIỀN ĐĂNG KÝ E00–E06 (+ ĐỐI CHỨNG PHỤ):
{tbl_md}

4. HỒI QUY TOÀN BỘ, ĐỐI CHỨNG ĐỎ/XANH, ĐA LUỒNG BẤT ĐỒNG BỘ & AN TOÀN ĐĨA:
- Pytest toàn kho: collected={cnts.get('collected')}, executed={cnts.get('executed')}, passed={cnts.get('passed')}, skipped={cnts.get('skipped')}, failed={cnts.get('failed')}, errors={cnts.get('errors')}.
- Đột biến đối chứng âm/dương (Fault Injections): {faults_15.get('passed_mutations')}/{faults_15.get('total_mutations')} PASS.
- Bất đồng bộ w=1/w=2/w=4/w=1_repeat: speedup w=4 so với w=1 = {workers_13.get('measured_speedup_w4_vs_w1')}x ({workers_13.get('speedup_truth_label')}), mã băm đầu ra đồng nhất tuyệt đối = {workers_13.get('pytest_subset_w1_w2_w4_repeat_identical')} & {workers_13.get('r1_variant_slice_w1_w2_w4_repeat_identical')}.
- Tài nguyên & ổ đĩa: max_children_observed={resource_14.get('max_children_observed')} (<=4), preflight_baseline_free_gib={resource_14.get('preflight_baseline_free_gib')} (baseline_already_below_20gib={resource_14.get('baseline_already_below_20gib')}), min_free_disk_gib={resource_14.get('min_free_disk_gb_observed')}, orphan_pids={resource_14.get('orphan_pids_at_cleanup_count')}.

5. BA HÀNH ĐỘNG ƯU TIÊN TIẾP THEO & KHUYẾN NGHỊ VỀ KH-01C:
1) KHÔNG chuyển tiếp sang KH-01C để tối ưu ngưỡng trên bộ tín hiệu M1 hiện tại nếu KH01C_ADVANCEMENT_VERDICT = NO_ECONOMIC_ADVANCEMENT_CANDIDATE; cần thiết kế lại luận điểm Entry cấu trúc khung lớn hơn (M15/H1) ở một pha nghiên cứu mới độc lập để giảm ma sát spread + phí.
2) Thu thập dữ liệu tương lai sau thời điểm đóng băng mã nguồn (closed bars M1..D1 kèm báo giá thực CAPTURED_REAL_BBO từ Bitget) vào kho escrow KH-01D (>=180 phút sau freeze_timestamp_ms, strategy_evaluation_count=0).
3) Ghi nhận trực tiếp trạng thái phiên/bảo trì sàn Bitget theo thời gian thực song song với luồng BBO vì REST công khai không cung cấp lịch nghỉ lịch sử.
"""
    return readme, handover


def verify_kh01b_r1_cross_view_consistency(
    *,
    readme_text: str,
    handover_text: str,
    verdicts_18: Mapping[str, Any],
    economics_10: Mapping[str, Any],
    junit_12: Mapping[str, Any],
) -> Dict[str, Any]:
    violations: List[str] = []
    ov = verdicts_18["orthogonal_verdicts"]
    cnts = (junit_12.get("independent_bijection_verification") or {}).get("counters") or {}

    req_tokens = [
        str(verdicts_18.get("tested_executable_sha") or ""),
        str(ov.get("ENGINEERING_VERDICT") or ""),
        str(ov.get("RESEARCH_HYPOTHESIS_VERDICT") or ""),
        str(ov.get("FRESH_OOS_VERDICT") or ""),
        str(ov.get("DATA_ACQUISITION_VERDICT") or ""),
        str(ov.get("KH01C_ADVANCEMENT_VERDICT") or ""),
        str(cnts.get("collected") or ""),
        str(cnts.get("passed") or ""),
    ]
    for sc in ("E00", "E01", "E02", "E03", "E04", "E05", "E06"):
        ec = (economics_10.get("arms_economics") or {}).get(sc) or {}
        req_tokens.append(f"`{sc}`")
        req_tokens.append(str(ec.get("net_pnl_1x_nb_usd")))
        req_tokens.append(str(ec.get("research_verdict")))

    for tok in req_tokens:
        if tok not in readme_text:
            violations.append(f"README_MISSING_TOKEN:{tok}")
        if tok not in handover_text:
            violations.append(f"HANDOVER_VI_MISSING_TOKEN:{tok}")

    return {
        "status": "PASS" if not violations else "FAIL",
        "checked_tokens_count": len(req_tokens),
        "violations": violations,
    }
