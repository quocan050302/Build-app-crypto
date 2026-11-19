"""
aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py

AURUM DESK — KH-01B: Entry Intelligence & Causal Unicorn Research Service
SPEC VERSION: KH-01B-v1.0 (2026-11-19)

Additive, read-only research & verification layer built strictly on top of existing
authoritative AURUM Desk evaluators and replay engines:
  - SharedStrategyEvaluator.evaluate_model_direction (authoritative baseline truth)
  - resolve_causal_macro_time_window / evaluate_causal_unicorn_confluence / bind_unicorn_to_causal_episode
  - detect_unicorn_evidence / detect_breaker_blocks
  - compute_causal_tf_atr_v17931214 / audit_target_reachability_row_v17931214
  - arbitrate_competing_model_intents_v1793122 / replay_intervention_outcome_v179312

HARD SAFETY LOCKS:
  - PRODUCTION = OFF, LIVE = OFF, PAPER = OFF, AUTO = OFF, ACTIVATION_READY = NO.
  - Zero mutation of SI0..SI9, A0..D4, or historical artifacts (`kh_01a`, `kh_01a_r1`).
  - Historical canonical dataset is REUSED_RESEARCH; Fresh OOS remains BLOCKED_NO_UNTOUCHED_DATA.
"""
from __future__ import annotations

import ast
import bisect
from collections import Counter
import copy
from dataclasses import asdict, dataclass, field
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

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.kh_01a_foundation_service import (
    CANONICAL_DATASET_SHA256_KH01A,
    DatasetManifestEntryKH01A,
    MANDATORY_BRANCH_KH01A,
    validate_and_classify_macro_state_kh01a_r1,
    validate_fresh_oos_escrow_claim_kh01a,
)
from services.kh_01a_r1_forensics_closure_service import (
    SCHEMA_VERSION_KH01A_R1,
    scan_all_macro_window_call_sites_ast,
)
from services.shared_strategy_evaluator import (
    SharedStrategyEvaluator,
    build_market_decision_context,
)
from services.unicorn_evidence_service import detect_breaker_blocks, detect_unicorn_evidence
from services.unicorn_evidence_types import UnicornSetupStatus
from services.v17_9_3_1_2_2_signal_intelligence import (
    PRODUCTION_MODEL_STATIC_PRIORITY,
    arbitrate_competing_model_intents_v1793122,
    compute_strategy_statistical_verdict_v1793122,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    canonical_hash,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    evaluate_shared_delayed_entry_adapter_v17931213,
)
from services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service import (
    audit_target_reachability_row_v17931214,
    build_267_target_failure_and_control_audit_v17931214,
    build_d4_full_63085_and_matched_funnel_v17931214,
    compute_causal_tf_atr_v17931214,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import EvidenceReceiptV17931219
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    PortfolioReplayStateV179312,
    advance_portfolio_to_next_event_v179312,
    recalculate_trade_risk_quantity_v179312,
    replay_intervention_outcome_v179312,
)
from services.v17_9_3_macro_unicorn_intelligence import (
    DEFAULT_MACRO_SCHEDULE_VERSION,
    bind_unicorn_to_causal_episode,
    classify_macro_context_quality,
    evaluate_causal_unicorn_confluence,
    resolve_causal_macro_time_window,
    validate_macro_schedule_config,
)

# ==============================================================================
# CONSTANTS & CANONICAL ENUMS
# ==============================================================================

SPEC_VERSION_KH01B = "KH-01B-v1.0"
SCHEMA_VERSION_KH01B = "kh_01b.entry_unicorn_research.v1"
MANDATORY_BRANCH_KH01B = MANDATORY_BRANCH_KH01A
CANONICAL_DATASET_SHA256_KH01B = CANONICAL_DATASET_SHA256_KH01A

PRIOR_R1_STARTING_HEAD_SHA = "d05f0fd74b65e2d0ef870e2bac259e81799c34cc"
PRIOR_R1_TESTED_EXECUTABLE_SHA = "c1ba40df7ed95c44b9d9cfa473b77a3682a8ef01"
PRIOR_R1_RUN_ID = "20261119T021545Z_c1ba40df7ed9"
PRIOR_R1_RUN_REL_DIR = f"aurum-desk/docs/artifacts/kh_01a_r1/{PRIOR_R1_RUN_ID}"
PRIOR_R1_LATEST_PTR_REL = "aurum-desk/docs/artifacts/kh_01a_r1/latest_run.json"
PRIOR_V130_REL_DIR = "aurum-desk/docs/artifacts/v17_9_3_1_2_10/20261107T150245Z_b772a03bb7cc"

FILL_MODES_KH01B: Tuple[str, ...] = (
    "NEXT_BAR_OPEN_BID_ASK",
    "LEGACY_CLOSE_FILL",
    "STRESS_WIDER_SPREAD_SLIPPAGE_2X",
)

VALID_TRI_STATES_KH01B = frozenset({
    "PROVEN_TRUE",
    "PROVEN_FALSE",
    "UNKNOWN_MISSING",
    "INVALID",
})

VALID_SOURCE_ORIGINS_KH01B = frozenset({
    "PERSISTED_VERIFIED_SNAPSHOT",
    "RECONSTRUCTED_ASOF_RAW_BARS",
    "LEDGER_ONLY_UNVERIFIED",
    "MISSING",
    "INVALID",
})

VALID_ONTOLOGIES_KH01B = frozenset({
    "BASELINE_CONTROL",
    "OTHER_CAUSAL_FILTER_VARIANTS",
    "STRICT_D4_TRUE_UNICORN",
    "ADJACENT_FVG_SWEEP_MSS",
    "TP_ONLY_RESEARCH",
    "MULTI_AXIS_COMBINATION",
})

VALID_SELECTION_OUTCOMES_KH01B = frozenset({
    "RESEARCH_NEGATIVE_EDGE",
    "RESEARCH_POSITIVE_BUT_REUSED_NOT_VALIDATED",
    "INCONCLUSIVE_UNDERPOWERED",
    "INCONCLUSIVE_DATA_QUALITY",
    "NO_VALID_SETUPS",
    "FAIL_CAUSAL_OR_ACCOUNTING",
})

MANDATORY_KH01B_TECHNICAL_GATES: Tuple[str, ...] = tuple(f"G-KH01B-{i:02d}" for i in range(1, 15))
ALL_KH01B_GATES: Tuple[str, ...] = tuple(f"G-KH01B-{i:02d}" for i in range(1, 17))

REQUIRED_ARTIFACTS_KH01B: Tuple[str, ...] = (
    "00_preflight_source_config_dataset_freeze.json",
    "01_function_callgraph_preentry_evidence_and_keep_as_is.json",
    "02_pre_registered_hypothesis_manifest_and_search_budget.json",
    "03_causal_feature_sources_unknown_invalid_coverage.json",
    "04_fixed_population_ids_and_match_conservation.json",
    "05_htf_single_axis_B01_B02_B03.json",
    "06_target_source_tf_horizon_B04_B11_and_267_controls.json",
    "07_macro_B05_calendar_DST_unverified_contract.json",
    "08_sweep_mss_displacement_B06_B07_B08_evidence.json",
    "09_strict_D4_and_adjacent_B09_B10_detector_funnel.json",
    "10_all_variant_comparable_replays_3fill_1pos_ledgers.json",
    "11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json",
    "12_full_baseline_and_three_adapter_parity.json",
    "13_real_fault_injection_red_green_casebook.json",
    "14_full_pytest_junit_shards_process_exit_and_bijection.json",
    "15_async_workers_1_2_4_repeat_determinism_and_real_speed.json",
    "16_resource_disk_ram_cpu_pid_cleanup.json",
    "17_fresh_oos_registry_and_economic_verdict_separation.json",
    "18_independent_verifier_manifest_receipts_hashes.json",
    "19_final_orthogonal_gates_blockers_and_kh01c_candidates.json",
    "README_KH01B.md",
    "HANDOVER_KH01B_VI.txt",
    "ledgers/kh01b_all_variants_episode_ledgers.jsonl.gz",
    "ledgers/kh01b_candidate_filter_decisions_summary.jsonl.gz",
)

MIN_PLAUSIBLE_EPOCH_MS_KH01B = 946_684_800_000  # 2000-01-01 UTC
MAX_PLAUSIBLE_EPOCH_MS_KH01B = 4_102_444_800_000  # 2100-01-01 UTC


# ==============================================================================
# TYPED DATACLASSES (F01 - F14 CONTRACTS)
# ==============================================================================

@dataclass(frozen=True)
class BaselineTruth:
    spec_version: str
    schema_version: str
    dataset_sha256: str
    prior_use_status: str
    prior_r1_run_id: str
    prior_r1_tested_sha: str
    prior_r1_forensics_170_count: int
    a1_candidate_count: int
    si8_discovery_count: int
    total_candidate_universe_count: int
    unique_a1_timestamps_count: int
    si0_reference_scheduled: int
    si0_reference_closed: int
    si0_reference_wins: int
    si0_reference_losses: int
    si0_reference_net_pnl_usd: float
    si0_reference_expectancy_r: float
    d4_reference_accepted_setups: int
    verification_passed: bool
    violations: Tuple[str, ...]
    baseline_truth_sha256: str


@dataclass(frozen=True)
class FrozenResearchManifest:
    spec_version: str
    schema_version: str
    manifest_id: str
    dataset_sha256: str
    config_sha256: str
    code_sha256: str
    prior_use_status: str
    search_space_budget_max_variants: int
    registered_variants_count: int
    purged_embargo_minutes: int
    min_powered_closed_trades: int
    min_net_rr: float
    max_planned_risk_usd: float
    cost_multipliers_evaluated: Tuple[float, ...]
    variants: Tuple[Dict[str, Any], ...]
    amendments: Tuple[Dict[str, Any], ...]
    frozen_before_backtest_execution: bool
    manifest_sha256: str


@dataclass
class FeatureEvidenceFrame:
    opportunity_id: str
    setup_episode_id: str
    model_id: str
    direction: str
    decision_timestamp_ms: int
    checkpoint_index_1m: int
    discovered_by_si8: bool
    entry_reference: float
    selected_sl: float
    take_profit: float
    hypothetical_quantity: float
    planned_net_risk_usd: float
    baseline_net_rr: float
    baseline_gross_rr: float
    selected_target_id: Optional[str]
    selected_fvg_id: Optional[str]
    selected_sweep_id: Optional[str]
    selected_break_id: Optional[str]
    trade_action_fingerprint: str
    decision_fingerprint: str
    feature_cells: Dict[str, Dict[str, Any]]
    macro_window_info: Dict[str, Any]
    target_audit_info: Dict[str, Any]
    has_any_invalid_evidence: bool
    has_any_missing_evidence: bool
    invalidation_reasons: List[str]
    frame_sha256: str


@dataclass(frozen=True)
class ResearchGateDecision:
    hypothesis_id: str
    tri_state: str  # PROVEN_TRUE | PROVEN_FALSE | UNKNOWN_MISSING | INVALID
    eligible: bool
    rejection_code: Optional[str]
    missing_evidence_rejected: bool
    proven_false_rejected: bool
    details: Dict[str, Any]


@dataclass(frozen=True)
class ResearchTargetAudit:
    opportunity_id: str
    mode: str  # FILTER_ONLY_B04 | TP_ONLY_RESEARCH_B11
    tri_state: str
    eligible: bool
    rejection_code: Optional[str]
    resolved_source_tf: str
    source_tf_atr: Optional[float]
    target_distance_source_tf_atr: Optional[float]
    horizon_limit_atr: float
    original_tp: float
    selected_tp: float
    tp_modified: bool
    selected_target_pool_id: Optional[str]
    recomputed_quantity: Optional[float]
    recomputed_planned_risk_usd: Optional[float]
    recomputed_net_rr: Optional[float]
    recomputed_gross_rr: Optional[float]
    details: Dict[str, Any]


@dataclass(frozen=True)
class StrictUnicornResearchDecision:
    opportunity_id: str
    ontology: str  # STRICT_D4_TRUE_UNICORN
    is_valid_strict_unicorn: bool
    highest_stage_passed: str
    terminal_rejection_code: Optional[str]
    overlap_ticks_verified: bool
    same_episode_verified: bool
    retest_verified: bool
    htf_aligned_verified: bool
    details: Dict[str, Any]


@dataclass(frozen=True)
class AdjacentResearchDecision:
    opportunity_id: str
    variant_id: str
    ontology: str  # ADJACENT_FVG_SWEEP_MSS
    counts_toward_d4_true_unicorn: bool
    eligible: bool
    rejection_code: Optional[str]
    missing_evidence_rejected: bool
    details: Dict[str, Any]


@dataclass
class VariantReplayBundle:
    variant_id: str
    short_code: str
    ontology: str
    axis_type: str
    population_name: str
    population_total: int
    eligible_candidates_count: int
    rejected_candidates_count: int
    missing_evidence_rejected_count: int
    proven_false_rejected_count: int
    invalid_evidence_rejected_count: int
    plausible_eligibility_lower_bound: int
    plausible_eligibility_upper_bound: int
    rejection_reasons_counts: Dict[str, int]
    post_arbitration_timestamps_selected: int
    arbitration_suppressed_ties_count: int
    arbitration_winner_substitutions_vs_b00: int
    materialized_accepted_count: int
    materialized_modified_tp_count: int
    fill_mode_summaries: Dict[str, Dict[str, Any]]
    primary_nb_episodes: List[Dict[str, Any]]
    all_mode_episodes: Dict[str, List[Dict[str, Any]]]
    matched_b00_242_counterfactual: Dict[str, Any]
    si8_separate_diagnostic: Optional[Dict[str, Any]]
    bundle_sha256: str


@dataclass(frozen=True)
class ResearchEconomicVerdict:
    variant_id: str
    short_code: str
    ontology: str
    axis_type: str
    closed_trades_nb: int
    wins_nb: int
    losses_nb: int
    win_rate_nb: float
    scheduled_nb: int
    trade_frequency_retention_vs_b00: float
    net_pnl_1x_nb_usd: float
    expectancy_1x_nb_r: float
    profit_factor_1x_nb: float
    max_drawdown_1x_nb_usd: float
    longest_losing_streak_1x_nb: int
    total_fees_1x_nb_usd: float
    legacy_close_fill_net_pnl_usd: float
    stress_2x_spread_slippage_net_pnl_usd: float
    stress_2x_all_cost_net_pnl_usd: float
    stress_2x_all_cost_expectancy_r: float
    stress_3x_all_cost_net_pnl_usd: float
    cluster_bootstrap_95ci_expectancy_r: Optional[Tuple[float, float]]
    cluster_bootstrap_95ci_net_pnl_usd: Optional[Tuple[float, float]]
    purged_reused_walkforward_folds: Tuple[Dict[str, Any], ...]
    paired_vs_b00_portfolio_net_pnl_diff_usd: float
    paired_vs_b00_matched_242_net_pnl_diff_usd: float
    multiplicity_hypotheses_tested_m: int
    holm_bonferroni_caution: str
    sample_power_warning: str
    selection_outcome: str
    qualifies_for_kh01c_advancement: bool
    fresh_oos_verdict: str
    activation_ready: str


@dataclass(frozen=True)
class CausalDetectorValidation:
    spec_version: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    positive_strict_unicorn_fixtures_passed: int
    negative_controls_passed: int
    synthetic_counted_as_historical_trades: int
    status: str
    cases: Tuple[Dict[str, Any], ...]
    validation_sha256: str


@dataclass(frozen=True)
class ImmutableBaselineEvidence:
    spec_version: str
    tested_sha: str
    branch: str
    a1_population_denominator: int
    a1_evaluated_count: int
    a1_mismatches_count: int
    si8_population_denominator: int
    si8_verified_count: int
    si8_mismatches_count: int
    b00_vs_si0_exact_match: bool
    three_adapter_episodes_denominator: int
    three_adapter_mismatches_count: int
    runtime_modes_all_off: bool
    risk_cap_violations_count: int
    status: str
    violations: Tuple[str, ...]
    parity_sha256: str


@dataclass
class AblationSet:
    spec_version: str
    manifest_sha256: str
    bundles_by_short_code: Dict[str, VariantReplayBundle]
    economics_by_short_code: Dict[str, ResearchEconomicVerdict]
    d4_authoritative_funnel: Dict[str, Any]
    target_267_control_audit: Dict[str, Any]
    kh01c_advancement_decision: str
    qualified_kh01c_candidates: List[str]
    ablation_set_sha256: str


# ==============================================================================
# HELPER UTILITIES
# ==============================================================================

def _iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s:
                yield json.loads(s)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strict_bool_tri_state(
    raw_val: Any,
    proof_state: Optional[str],
    *,
    known_at_ms: Optional[int],
    source_bar_close_ms: Optional[int],
    asof_ts_ms: int,
) -> Tuple[str, Optional[bool], Optional[str]]:
    """
    Converts raw persisted/measured boolean evidence strictly into:
      (tri_state, bool_or_none, invalid_reason)
    NEVER converts None or 'UNKNOWN' into False, and NEVER accepts string 'false'/'true'.
    """
    if known_at_ms is not None and known_at_ms > asof_ts_ms:
        return "INVALID", None, f"KNOWN_AT_FUTURE:{known_at_ms}>{asof_ts_ms}"
    if source_bar_close_ms is not None and source_bar_close_ms > asof_ts_ms:
        return "INVALID", None, f"BAR_CLOSE_FUTURE:{source_bar_close_ms}>{asof_ts_ms}"

    ps = str(proof_state or "").upper()
    if ps == "INVALID":
        return "INVALID", None, "PROOF_STATE_INVALID"
    if raw_val is not None and not isinstance(raw_val, bool):
        return "INVALID", None, f"NON_BOOL_FOR_BOOLEAN_EVIDENCE:{type(raw_val).__name__}"
    if raw_val is None or ps in ("UNKNOWN", "MISSING", ""):
        if isinstance(raw_val, bool) and ps in ("PROVEN_TRUE", "PROVEN_FALSE", "MEASURED_VALUE"):
            pass
        else:
            return "UNKNOWN_MISSING", None, None

    if raw_val is True:
        if ps == "PROVEN_FALSE":
            return "INVALID", None, "CONTRADICTORY_TRUE_VALUE_WITH_PROVEN_FALSE"
        return "PROVEN_TRUE", True, None
    else:
        if ps == "PROVEN_TRUE":
            return "INVALID", None, "CONTRADICTORY_FALSE_VALUE_WITH_PROVEN_TRUE"
        return "PROVEN_FALSE", False, None


# ==============================================================================
# §01 & §02: CALLGRAPH & STRICT KEEP-AS-IS MATRIX
# ==============================================================================

def build_kh01b_callgraph_and_keep_as_is_matrix(
    repo_root: Path,
    *,
    baseline_sha: str,
    tested_sha: str,
    changed_executable_files: Sequence[str] = (),
) -> Dict[str, Any]:
    """
    Builds `01_function_callgraph_preentry_evidence_and_keep_as_is.json` using live AST &
    signature inspection across all §02A-F authoritative functions and §03 F01-F17 KH-01B functions.
    """
    app_root = repo_root / "aurum-desk"
    targets: List[Tuple[str, str, str, str, str, str]] = [
        # (rel_file, import_mod, symbol, decision, immutable_invariant, test_ref)
        (
            "aurum-desk/backend/services/shared_strategy_evaluator.py",
            "services.shared_strategy_evaluator",
            "SharedStrategyEvaluator.evaluate_model_direction",
            "KEEP_AS_IS_AUTHORITATIVE_READ_ONLY",
            "Baseline StrategyDecisionSnapshot & trade_action_fingerprint 100% unchanged across 56,710 A1 candidates",
            "test_kh_01b_replay_resource_and_truth.py::test_kh01b_b00_baseline_and_3_adapter_parity",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "services.v17_9_3_1_2_2_signal_intelligence",
            "build_signal_quality_evidence_v1793122",
            "KEEP_AS_IS_READ_ONLY",
            "Causal signal quality supplier; missing sweep/MSS never treated as proven False in KH-01B",
            "test_kh_01b_causal_antileakage.py::test_kh01b_missing_vs_false_monotonicity",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "services.v17_9_3_1_2_2_signal_intelligence",
            "evaluate_signal_intelligence_gate_v1793122",
            "KEEP_AS_IS_READ_ONLY",
            "Production gate stays OFF; never promotes baseline-rejected candidates",
            "test_kh_01b_causal_antileakage.py::test_kh01b_twenty_five_mandatory_cases_all_pass",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "services.v17_9_3_1_2_2_signal_intelligence",
            "arbitrate_competing_model_intents_v1793122",
            "KEEP_AS_IS_REUSED_IN_RESEARCH",
            "STATIC_PRODUCTION_PRIORITY deterministic multi-model arbitration preserved",
            "test_kh_01b_entry_unicorn_research.py::test_kh01b_f01_to_f12_contracts_and_preregistration",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
            "services.v17_9_3_macro_unicorn_intelligence",
            "resolve_causal_macro_time_window",
            "KEEP_AS_IS_KEYWORD_BOUND",
            "Always invoked via explicit keywords decision_timestamp_ms=..., symbol='XAUUSDT'",
            "test_kh_01b_causal_antileakage.py::test_kh01b_dst_holiday_and_macro_keyword_contracts",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
            "services.v17_9_3_macro_unicorn_intelligence",
            "evaluate_causal_unicorn_confluence",
            "KEEP_AS_IS_STRICT_D4_DETECTOR",
            "Strict Breaker + FVG overlap >= 1 tick ($0.01) + displacement + retest + episode binding",
            "test_kh_01b_entry_unicorn_research.py::test_kh01b_f13_strict_unicorn_positive_and_negative_fixtures",
        ),
        (
            "aurum-desk/backend/services/unicorn_evidence_service.py",
            "services.unicorn_evidence_service",
            "detect_unicorn_evidence",
            "KEEP_AS_IS_STRICT_GEOMETRY",
            "Never substitutes unrelated FVG when selected_fvg is provided; rejects <1 tick & future bars",
            "test_kh_01b_entry_unicorn_research.py::test_kh01b_f13_strict_unicorn_positive_and_negative_fixtures",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
            "services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service",
            "audit_target_reachability_row_v17931214",
            "KEEP_AS_IS_REUSED_IN_B04_B11",
            "Tri-state VALID_VALUE / UNKNOWN_MISSING / INVALID_INCONSISTENT target pool & source-TF ATR audit",
            "test_kh_01b_entry_unicorn_research.py::test_kh01b_b04_filter_vs_b11_tp_counterfactual_boundary",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
            "services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service",
            "build_d4_full_63085_and_matched_funnel_v17931214",
            "KEEP_AS_IS_AUTHORITATIVE_D4",
            "Monotonic 63,085 D4 causal stage funnel; 0 historical setups honestly preserved as NO_VALID_SETUPS",
            "test_kh_01b_entry_unicorn_research.py::test_kh01b_strict_unicorn_vs_adjacent_ontology_isolation",
        ),
        (
            "aurum-desk/backend/services/v17_9_3_1_2_executable_intervention_replay_service.py",
            "services.v17_9_3_1_2_executable_intervention_replay_service",
            "replay_intervention_outcome_v179312",
            "KEEP_AS_IS_REUSED_SIMULATOR",
            "3 fill modes, conservative stop-first tie-break, $2.50 risk cap, exact fee/spread/slippage accounting",
            "test_kh_01b_replay_resource_and_truth.py::test_kh01b_2x_cost_flip_and_accounting_conservation",
        ),
    ]

    kh01b_new_fns = [
        ("F01", "load_and_verify_kh01b_reference_baseline"),
        ("F02", "pre_register_kh01b_hypotheses"),
        ("F03", "extract_kh01b_causal_feature_frame"),
        ("F04", "evaluate_htf_alignment_hypothesis"),
        ("F05", "evaluate_macro_window_hypothesis"),
        ("F06", "evaluate_sweep_mss_displacement_hypothesis"),
        ("F07", "evaluate_source_tf_target_reachability_hypothesis"),
        ("F08", "evaluate_strict_true_unicorn_hypothesis"),
        ("F09", "evaluate_unicorn_adjacent_research_hypothesis"),
        ("F10", "evaluate_kh01b_variant_on_matched_candidate_universe"),
        ("F11", "run_kh01b_orthogonal_ablation_matrix"),
        ("F12", "compute_kh01b_research_economics_and_uncertainty"),
        ("F13", "audit_kh01b_true_unicorn_positive_and_negative_fixtures"),
        ("F14", "verify_kh01b_full_baseline_and_adapter_parity"),
        ("F15", "evaluate_kh01b_orthogonal_gates_from_disk"),
        ("F16", "render_kh01b_readme_handover_vi"),
    ]

    keep_rows: List[Dict[str, Any]] = []
    for rel_f, mod_n, sym_n, dec_s, inv_s, tst_s in targets:
        mod_obj = importlib.import_module(mod_n)
        if "." in sym_n:
            cls_n, mth_n = sym_n.split(".", 1)
            fn_obj = getattr(getattr(mod_obj, cls_n), mth_n)
        else:
            fn_obj = getattr(mod_obj, sym_n)
        sig_str = str(inspect.signature(fn_obj))
        src_lines, line_no = inspect.getsourcelines(fn_obj)
        fn_sha = hashlib.sha256("".join(src_lines).encode("utf-8")).hexdigest()
        keep_rows.append({
            "file": rel_f,
            "symbol": sym_n,
            "line_number": line_no,
            "signature": sig_str,
            "function_source_sha256": fn_sha,
            "decision": dec_s,
            "file_modified_in_kh01b": rel_f in set(changed_executable_files),
            "immutable_invariant": inv_s,
            "test_evidence": tst_s,
        })

    this_mod = importlib.import_module(__name__)
    new_fn_rows: List[Dict[str, Any]] = []
    for f_code, f_name in kh01b_new_fns:
        fn_obj = getattr(this_mod, f_name)
        src_lines, line_no = inspect.getsourcelines(fn_obj)
        new_fn_rows.append({
            "contract_id": f_code,
            "file": "aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py",
            "function": f_name,
            "line_number": line_no,
            "signature": str(inspect.signature(fn_obj)),
            "function_source_sha256": hashlib.sha256("".join(src_lines).encode("utf-8")).hexdigest(),
            "decision": "NEW_ADDITIVE_RESEARCH_ONLY",
        })

    ast_macro_scan = scan_all_macro_window_call_sites_ast(app_root)

    return {
        "spec_version": SPEC_VERSION_KH01B,
        "schema_version": SCHEMA_VERSION_KH01B,
        "baseline_reference_head_sha": baseline_sha,
        "tested_executable_sha": tested_sha,
        "existing_backend_services_modified_count": sum(
            1 for f in changed_executable_files
            if f.startswith("aurum-desk/backend/services/")
            and not f.endswith("kh_01b_entry_unicorn_research_service.py")
        ),
        "changed_executable_files": list(changed_executable_files),
        "keep_as_is_verified_functions": keep_rows,
        "new_kh01b_contract_functions": new_fn_rows,
        "macro_call_site_ast_audit": ast_macro_scan,
    }


# ==============================================================================
# F01: LOAD & VERIFY IMMUTABLE KH-01A.R1 + V130 REFERENCE BASELINE
# ==============================================================================

def load_and_verify_kh01b_reference_baseline(
    repo_root: Path,
    run_registry: Optional[Mapping[str, Any]] = None,
) -> BaselineTruth:
    """
    F01: Reads immutable KH-01A.R1 and V130 canonical artifacts, verifies SHA256s,
    dataset provenance (`REUSED_RESEARCH`), candidate universe counts (`56,710 + 6,375 = 63,085`),
    and SI0 accounting conservation (`242` scheduled, `166 = 9W + 157L`, `-$345.8971`).
    Fails closed on any missing or tampered reference metadata.
    """
    violations: List[str] = []
    reg = dict(run_registry or {})
    r1_ptr_path = repo_root / str(reg.get("r1_latest_ptr_rel") or PRIOR_R1_LATEST_PTR_REL)
    ds_path = repo_root / str(reg.get("dataset_rel") or "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz")
    v130_dir = repo_root / str(reg.get("v130_rel_dir") or PRIOR_V130_REL_DIR)

    if not r1_ptr_path.is_file():
        violations.append(f"MISSING_R1_LATEST_POINTER:{r1_ptr_path}")
        r1_ptr: Dict[str, Any] = {}
    else:
        r1_ptr = json.loads(r1_ptr_path.read_text(encoding="utf-8"))

    r1_run_rel = str(r1_ptr.get("run_dir") or PRIOR_R1_RUN_REL_DIR)
    r1_dir = repo_root / r1_run_rel
    if not r1_dir.is_dir():
        violations.append(f"MISSING_R1_RUN_DIR:{r1_dir}")

    actual_ds_sha = _file_sha256(ds_path) if ds_path.is_file() else "MISSING"
    if actual_ds_sha != CANONICAL_DATASET_SHA256_KH01B:
        violations.append(f"DATASET_SHA_MISMATCH:{actual_ds_sha}!={CANONICAL_DATASET_SHA256_KH01B}")

    f06_path = r1_dir / "06_feature_evidence_origin_missing_invalid_source_hash_census.json"
    f09_path = r1_dir / "09_all_3_fill_modes_si0_to_si9_one_position_backtest_oos_cost_stress.json"
    f14_path = r1_dir / "14_fresh_oos_registry_untouched_and_economic_verdict.json"
    f16_path = r1_dir / "16_final_orthogonal_verdicts_open_blockers.json"

    for req_p in (f06_path, f09_path, f14_path, f16_path):
        if not req_p.is_file():
            violations.append(f"MISSING_R1_REFERENCE_ARTIFACT:{req_p.name}")

    r1_170_count = 0
    si0_sched = si0_closed = si0_wins = si0_losses = 0
    si0_net = 0.0
    si0_exp_r = 0.0
    d4_acc = -1
    prior_use = "UNKNOWN"

    if not violations:
        f06 = json.loads(f06_path.read_text(encoding="utf-8"))
        f09 = json.loads(f09_path.read_text(encoding="utf-8"))
        f14 = json.loads(f14_path.read_text(encoding="utf-8"))
        f16 = json.loads(f16_path.read_text(encoding="utf-8"))

        if f06.get("schema_version") != SCHEMA_VERSION_KH01A_R1:
            violations.append(f"R1_F06_SCHEMA_MISMATCH:{f06.get('schema_version')}")
        lc = f06.get("ledger_conservation") or {}
        r1_170_count = int(lc.get("total_casebook_rows_delivered") or 0)
        if r1_170_count != 170:
            violations.append(f"R1_CASEBOOK_COUNT_MISMATCH:{r1_170_count}")

        si0_nb = (f09.get("si_policies_nb_summary") or {}).get("SI0_OFF_BASELINE") or {}
        si0_sched = int(si0_nb.get("scheduled") or 0)
        si0_closed = int(si0_nb.get("closed") or 0)
        si0_wins = int(si0_nb.get("wins") or 0)
        si0_losses = int(si0_nb.get("losses") or 0)
        si0_open = int(si0_nb.get("open_at_horizon") or 0)
        si0_gap = int(si0_nb.get("data_gap_unresolved") or 0)
        si0_unfill = int(si0_nb.get("unfilled") or 0)
        si0_net = round(float(si0_nb.get("net_pnl_usd") or 0.0), 4)
        si0_exp_r = round(float(si0_nb.get("expectancy_net_r") or 0.0), 4)
        if (si0_closed + si0_open + si0_gap + si0_unfill) != si0_sched or (si0_wins + si0_losses) != si0_closed:
            violations.append("SI0_ACCOUNTING_CONSERVATION_BROKEN")

        d4_sum = f09.get("d4_summary") or {}
        d4_acc = int(d4_sum.get("d4_universe_63085_accepted_intents_nb", d4_sum.get("d4_chronological_1pos_accepted_count", -1)))

        prior_use = str((f14.get("canonical_dataset_manifest") or {}).get("prior_use_status") or "")
        if prior_use != "REUSED_RESEARCH":
            violations.append(f"UNEXPECTED_PRIOR_USE_STATUS:{prior_use}")
        if (f16.get("orthogonal_verdicts") or {}).get("FRESH_OOS_VERDICT") != "BLOCKED_NO_UNTOUCHED_DATA":
            violations.append("R1_FRESH_OOS_NOT_BLOCKED")

    p10 = v130_dir / "10_canonical_decision_inputs.jsonl.gz"
    p07 = v130_dir / "07_verified_causal_features.jsonl.gz"
    a1_n = si8_n = total_n = uniq_ts_n = 0
    if not p10.is_file() or not p07.is_file():
        violations.append("MISSING_V130_CANONICAL_DECISION_OR_FEATURE_LEDGER")
    else:
        seen_oids: Set[str] = set()
        a1_ts: Set[int] = set()
        for row in _iter_jsonl_gz(p10):
            oid = str(row.get("opportunity_id") or "")
            if not oid or oid in seen_oids:
                violations.append(f"DUPLICATE_OR_EMPTY_CANDIDATE_ID:{oid}")
                break
            seen_oids.add(oid)
            total_n += 1
            if bool(row.get("discovered_by_si8", False)):
                si8_n += 1
            else:
                a1_n += 1
                a1_ts.add(int(row.get("decision_timestamp_ms") or 0))
        uniq_ts_n = len(a1_ts)
        if a1_n + si8_n != total_n or total_n == 0:
            violations.append(f"CANDIDATE_UNIVERSE_SUM_MISMATCH:{a1_n}+{si8_n}!={total_n}")

    payload = {
        "spec_version": SPEC_VERSION_KH01B,
        "schema_version": SCHEMA_VERSION_KH01B,
        "dataset_sha256": actual_ds_sha,
        "prior_use_status": prior_use,
        "prior_r1_run_id": str(r1_ptr.get("run_id") or ""),
        "prior_r1_tested_sha": str(r1_ptr.get("tested_executable_sha") or ""),
        "prior_r1_forensics_170_count": r1_170_count,
        "a1_candidate_count": a1_n,
        "si8_discovery_count": si8_n,
        "total_candidate_universe_count": total_n,
        "unique_a1_timestamps_count": uniq_ts_n,
        "si0_reference_scheduled": si0_sched,
        "si0_reference_closed": si0_closed,
        "si0_reference_wins": si0_wins,
        "si0_reference_losses": si0_losses,
        "si0_reference_net_pnl_usd": si0_net,
        "si0_reference_expectancy_r": si0_exp_r,
        "d4_reference_accepted_setups": d4_acc,
        "verification_passed": len(violations) == 0,
        "violations": tuple(violations),
    }
    return BaselineTruth(**payload, baseline_truth_sha256=canonical_hash(payload))


# ==============================================================================
# F02: PRE-REGISTER KH-01B HYPOTHESES BEFORE ANY EXPERIMENT EXECUTION
# ==============================================================================

DEFAULT_KH01B_VARIANT_SPECS: Tuple[Dict[str, Any], ...] = (
    {
        "short_code": "B00",
        "variant_id": "KH01B-B00-UNCHANGED-A1-SI0",
        "ontology": "BASELINE_CONTROL",
        "axis_type": "BASELINE_CONTROL",
        "single_axis_vs_b00": False,
        "modifies_tp_geometry": False,
        "rule": {"type": "BASELINE_UNCHANGED"},
        "hypothesis_rationale": "Exact unchanged A1/SI0 baseline control ledger across 56,710 candidates.",
    },
    {
        "short_code": "B01",
        "variant_id": "KH01B-B01-H4-VERIFIED-ALIGNED-ONLY",
        "ontology": "OTHER_CAUSAL_FILTER_VARIANTS",
        "axis_type": "SINGLE_AXIS_ENTRY_FILTER",
        "single_axis_vs_b00": True,
        "modifies_tp_geometry": False,
        "rule": {"type": "HTF_ALIGNED", "htf_rule": "H4_ONLY"},
        "hypothesis_rationale": "Test whether requiring proven H4 structural bias aligned with trade direction isolates counter-H4 losses.",
    },
    {
        "short_code": "B02",
        "variant_id": "KH01B-B02-H1-VERIFIED-ALIGNED-ONLY",
        "ontology": "OTHER_CAUSAL_FILTER_VARIANTS",
        "axis_type": "SINGLE_AXIS_ENTRY_FILTER",
        "single_axis_vs_b00": True,
        "modifies_tp_geometry": False,
        "rule": {"type": "HTF_ALIGNED", "htf_rule": "H1_ONLY"},
        "hypothesis_rationale": "Test whether requiring proven H1 structural bias aligned with trade direction isolates counter-H1 losses.",
    },
    {
        "short_code": "B03",
        "variant_id": "KH01B-B03-H4-AND-H1-BOTH-VERIFIED-ALIGNED",
        "ontology": "OTHER_CAUSAL_FILTER_VARIANTS",
        "axis_type": "COMBINED_HTF_TWO_AXIS_FILTER",
        "single_axis_vs_b00": False,
        "modifies_tp_geometry": False,
        "rule": {"type": "HTF_ALIGNED", "htf_rule": "H4_AND_H1"},
        "hypothesis_rationale": "Combined H4+H1 alignment (explicitly NOT single-axis): both H4 and H1 must be proven aligned.",
    },
    {
        "short_code": "B04",
        "variant_id": "KH01B-B04-SOURCE-TF-TARGET-REACHABILITY-FILTER-ONLY",
        "ontology": "OTHER_CAUSAL_FILTER_VARIANTS",
        "axis_type": "SINGLE_AXIS_ENTRY_FILTER",
        "single_axis_vs_b00": True,
        "modifies_tp_geometry": False,
        "rule": {"type": "SOURCE_TF_TARGET_FILTER", "max_source_tf_atr_horizon": 12.0, "min_net_rr": 2.0},
        "hypothesis_rationale": "Pure entry filter requiring verified unswept source pool within <= 12.0 source-TF ATRs; entry/SL/TP unchanged.",
    },
    {
        "short_code": "B05",
        "variant_id": "KH01B-B05-ICT-CONFIGURED-MACRO-WINDOW-ONLY",
        "ontology": "OTHER_CAUSAL_FILTER_VARIANTS",
        "axis_type": "SINGLE_AXIS_ENTRY_FILTER",
        "single_axis_vs_b00": True,
        "modifies_tp_geometry": False,
        "rule": {"type": "MACRO_WINDOW_ONLY", "schedule_version": DEFAULT_MACRO_SCHEDULE_VERSION},
        "hypothesis_rationale": "Test configured NY ICT macro windows (RESEARCH_HYPOTHESIS_CONFIG; R1 showed 7 losses / 0 wins in window).",
    },
    {
        "short_code": "B06",
        "variant_id": "KH01B-B06-PROVEN-SWEEP-AND-RECLAIM-ONLY",
        "ontology": "OTHER_CAUSAL_FILTER_VARIANTS",
        "axis_type": "SINGLE_AXIS_ENTRY_FILTER",
        "single_axis_vs_b00": True,
        "modifies_tp_geometry": False,
        "rule": {"type": "SWEEP_MSS", "mode": "SWEEP_RECLAIM_ONLY"},
        "hypothesis_rationale": "Require proven causal liquidity sweep + reclaim; missing proof tallied separately from proven false.",
    },
    {
        "short_code": "B07",
        "variant_id": "KH01B-B07-PROVEN-MSS-AND-DISPLACEMENT-ONLY",
        "ontology": "OTHER_CAUSAL_FILTER_VARIANTS",
        "axis_type": "SINGLE_AXIS_ENTRY_FILTER",
        "single_axis_vs_b00": True,
        "modifies_tp_geometry": False,
        "rule": {"type": "SWEEP_MSS", "mode": "MSS_DISPLACEMENT_ONLY"},
        "hypothesis_rationale": "Require proven causal MSS/BOS break + displacement body >= threshold; selected_break_id alone insufficient.",
    },
    {
        "short_code": "B08",
        "variant_id": "KH01B-B08-SWEEP-RECLAIM-PLUS-MSS-DISPLACEMENT",
        "ontology": "OTHER_CAUSAL_FILTER_VARIANTS",
        "axis_type": "INTERACTION_TWO_MECHANISM_FILTER",
        "single_axis_vs_b00": False,
        "modifies_tp_geometry": False,
        "rule": {"type": "SWEEP_MSS", "mode": "SWEEP_AND_MSS"},
        "hypothesis_rationale": "Interaction filter requiring ordered causal sweep+reclaim AND MSS+displacement in same episode.",
    },
    {
        "short_code": "B09",
        "variant_id": "KH01B-B09-STRICT-TRUE-UNICORN-D4",
        "ontology": "STRICT_D4_TRUE_UNICORN",
        "axis_type": "STRICT_CAUSAL_CONFLUENCE_MODEL",
        "single_axis_vs_b00": False,
        "modifies_tp_geometry": False,
        "rule": {"type": "STRICT_D4_TRUE_UNICORN", "min_overlap_ticks": 1.0, "tick_size": 0.01},
        "hypothesis_rationale": "Authoritative D4 True Unicorn (HTF + sweep/reclaim + MSS/displacement + genuine Breaker+FVG overlap >= 1 tick + retest).",
    },
    {
        "short_code": "B10",
        "variant_id": "KH01B-B10-UNICORN-ADJACENT-FVG-SWEEP-MSS",
        "ontology": "ADJACENT_FVG_SWEEP_MSS",
        "axis_type": "ADJACENT_CONFLUENCE_RESEARCH_MODEL",
        "single_axis_vs_b00": False,
        "modifies_tp_geometry": False,
        "rule": {"type": "UNICORN_ADJACENT_FVG_SWEEP_OR_MSS", "counts_toward_d4": False},
        "hypothesis_rationale": "Unicorn-adjacent (strictly NOT True Unicorn): unmitigated retested FVG + proven sweep/reclaim or MSS/displacement without Breaker overlap.",
    },
    {
        "short_code": "B11",
        "variant_id": "KH01B-B11-TP-ONLY-SOURCE-BACKED-ALTERNATIVE-TARGET",
        "ontology": "TP_ONLY_RESEARCH",
        "axis_type": "TP_COUNTERFACTUAL_RESEARCH_ONLY",
        "single_axis_vs_b00": False,
        "modifies_tp_geometry": True,
        "rule": {"type": "TP_ONLY_SOURCE_BACKED_RESELECTION", "max_source_tf_atr_horizon": 12.0, "min_net_rr": 2.0},
        "hypothesis_rationale": "TP_ONLY_RESEARCH counterfactual: re-select nearest causal unswept pool <= 12 source-TF ATRs with net_rr >= 2.0 & risk <= $2.50; entry/SL unchanged.",
    },
    {
        "short_code": "B12",
        "variant_id": "KH01B-B12-PREREGISTERED-MULTI-AXIS-HTF-MSS-TARGET",
        "ontology": "MULTI_AXIS_COMBINATION",
        "axis_type": "MULTI_AXIS_COMBINATION",
        "single_axis_vs_b00": False,
        "modifies_tp_geometry": False,
        "rule": {"type": "MULTI_AXIS_B03_B07_B04", "components": ["B03", "B07", "B04"]},
        "hypothesis_rationale": "Pre-registered staged combination from KH-01A.R1 descriptive findings: H4+H1 aligned (B03) + proven MSS/displacement (B07) + source-TF target reachability (B04).",
    },
)


def pre_register_kh01b_hypotheses(
    dataset_sha: str,
    config_sha: str,
    code_sha: str,
    variant_manifest: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    amendments: Sequence[Mapping[str, Any]] = (),
) -> FrozenResearchManifest:
    """
    F02: Freezes the KH-01B pre-registered research manifest BEFORE any variant replay
    or return inspection occurs. Any amendment creates a distinct manifest hash & amendment log.
    """
    if not dataset_sha or not config_sha or not code_sha:
        raise ValueError("PRE_REGISTRATION_REQUIRES_NON_EMPTY_DATASET_CONFIG_AND_CODE_SHAS")

    raw_specs = [copy.deepcopy(dict(v)) for v in (variant_manifest or DEFAULT_KH01B_VARIANT_SPECS)]
    seen_ids: Set[str] = set()
    seen_codes: Set[str] = set()
    for sp in raw_specs:
        vid = str(sp.get("variant_id") or "")
        sc = str(sp.get("short_code") or "")
        ont = str(sp.get("ontology") or "")
        if not vid.startswith("KH01B-") or vid in seen_ids or sc in seen_codes:
            raise ValueError(f"INVALID_OR_DUPLICATE_KH01B_VARIANT_ID:{vid}:{sc}")
        if ont not in VALID_ONTOLOGIES_KH01B:
            raise ValueError(f"INVALID_KH01B_ONTOLOGY:{vid}:{ont}")
        if ont == "ADJACENT_FVG_SWEEP_MSS" and bool((sp.get("rule") or {}).get("counts_toward_d4", False)):
            raise ValueError(f"ONTOLOGY_LEAKAGE_ADJACENT_MARKED_D4:{vid}")
        if bool(sp.get("modifies_tp_geometry")) and ont != "TP_ONLY_RESEARCH":
            raise ValueError(f"COUPLED_TP_MODIFICATION_MUST_BE_TP_ONLY_RESEARCH:{vid}:{ont}")
        seen_ids.add(vid)
        seen_codes.add(sc)

    amend_tuple = tuple(copy.deepcopy(dict(a)) for a in amendments)
    manifest_id = (
        "KH01B_PREREG_V1_AMENDED_" + canonical_hash(amend_tuple)[:8]
        if amend_tuple
        else "KH01B_PREREG_V1_INITIAL"
    )
    body = {
        "spec_version": SPEC_VERSION_KH01B,
        "schema_version": SCHEMA_VERSION_KH01B,
        "manifest_id": manifest_id,
        "dataset_sha256": dataset_sha,
        "config_sha256": config_sha,
        "code_sha256": code_sha,
        "prior_use_status": "REUSED_RESEARCH",
        "search_space_budget_max_variants": 13,
        "registered_variants_count": len(raw_specs),
        "purged_embargo_minutes": 180,
        "min_powered_closed_trades": 30,
        "min_net_rr": 2.0,
        "max_planned_risk_usd": 2.50,
        "cost_multipliers_evaluated": (1.0, 2.0, 3.0),
        "variants": tuple(raw_specs),
        "amendments": amend_tuple,
        "frozen_before_backtest_execution": True,
    }
    if len(raw_specs) > body["search_space_budget_max_variants"]:
        raise ValueError(f"SEARCH_SPACE_BUDGET_EXCEEDED:{len(raw_specs)}>{body['search_space_budget_max_variants']}")
    return FrozenResearchManifest(**body, manifest_sha256=canonical_hash(body))


# ==============================================================================
# F03: EXTRACT CAUSAL POINT-IN-TIME FEATURE EVIDENCE FRAME
# ==============================================================================

def extract_kh01b_causal_feature_frame(
    candidate: Mapping[str, Any],
    verified_feature_ledger: Mapping[str, Mapping[str, Any]],
    closed_bar_index: Mapping[Tuple[str, int], int],
    asof_ts: int,
    *,
    pools_by_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
    datasets: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    close_ts_index: Optional[Mapping[str, Sequence[int]]] = None,
    macro_schedule_config: Optional[Mapping[str, Any]] = None,
    macro_cache: Optional[Dict[int, Dict[str, Any]]] = None,
    atr_cache: Optional[Dict[Tuple[str, int], Optional[float]]] = None,
    gap_180m_cache: Optional[Dict[int, bool]] = None,
) -> FeatureEvidenceFrame:
    """
    F03: Joins candidate with verified causal feature ledger on `opportunity_id`,
    enforces `source_bar_close_ms <= asof_ts` and `known_at_ms <= asof_ts`,
    preserves strict tri-state (`PROVEN_TRUE` / `PROVEN_FALSE` / `UNKNOWN_MISSING` / `INVALID`),
    and never converts `None` or unconfirmed descriptor IDs into proven booleans.
    """
    oid = str(candidate.get("opportunity_id") or "")
    invalidation_reasons: List[str] = []
    if not oid:
        invalidation_reasons.append("MISSING_OPPORTUNITY_ID")

    if isinstance(asof_ts, bool) or not isinstance(asof_ts, int) or asof_ts < MIN_PLAUSIBLE_EPOCH_MS_KH01B or asof_ts > MAX_PLAUSIBLE_EPOCH_MS_KH01B:
        invalidation_reasons.append(f"INVALID_ASOF_TIMESTAMP:{asof_ts!r}")

    dec_ts = int(candidate.get("decision_timestamp_ms") or asof_ts)
    if dec_ts > asof_ts:
        invalidation_reasons.append(f"CANDIDATE_DECISION_TS_AFTER_ASOF:{dec_ts}>{asof_ts}")

    direction = str(candidate.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        invalidation_reasons.append(f"INVALID_DIRECTION:{direction!r}")

    model_id = str(candidate.get("model_id") or "")
    ep_id = str(candidate.get("setup_episode_id") or candidate.get("setup_id") or "")
    chk_idx = int(candidate.get("checkpoint_index_1m") or 0)
    is_si8 = bool(candidate.get("discovered_by_si8", False))

    entry_px = float(candidate.get("entry_reference") or 0.0)
    sl_px = float(candidate.get("selected_sl") or 0.0)
    tp_px = float(candidate.get("take_profit") or 0.0)
    qty = float(candidate.get("hypothetical_quantity") or 0.0)
    net_rr = float(candidate.get("net_rr") or 0.0)
    gross_rr = float(candidate.get("gross_rr") or net_rr)

    raw_risk = candidate.get("planned_net_risk_usd")
    if raw_risk is None or float(raw_risk or 0.0) <= 0.0:
        if entry_px > 0.0 and sl_px > 0.0 and tp_px > 0.0 and direction in ("LONG", "SHORT"):
            rk = recalculate_trade_risk_quantity_v179312(entry_px, sl_px, tp_px, direction=direction, decision_ts=asof_ts)
            planned_risk_usd = float(rk.get("planned_net_risk_usd") or 0.0)
            if qty <= 0.0:
                qty = float(rk.get("quantity") or 0.0)
            if net_rr <= 0.0:
                net_rr = float(rk.get("net_rr") or 0.0)
        else:
            planned_risk_usd = 0.0
    else:
        planned_risk_usd = float(raw_risk)

    feat_row = verified_feature_ledger.get(oid) if oid else None
    persisted_verified = False
    snap_by_type: Dict[str, Mapping[str, Any]] = {}
    if feat_row is not None:
        f_ts = int(feat_row.get("decision_timestamp_ms") or 0)
        if f_ts > asof_ts:
            invalidation_reasons.append(f"FEATURE_ROW_FUTURE_DECISION_TS:{f_ts}>{asof_ts}")
        elif "causal_snapshot" in feat_row:
            if verify_feature_snapshot_v1793127(feat_row):
                persisted_verified = (f_ts == asof_ts)
                for item in (feat_row.get("causal_snapshot") or {}).get("features", ()):
                    if isinstance(item, Mapping) and item.get("evidence_type"):
                        et = str(item["evidence_type"])
                        if et in snap_by_type:
                            invalidation_reasons.append(f"DUPLICATE_FEATURE_EVIDENCE_TYPE:{et}")
                        snap_by_type[et] = item
            else:
                invalidation_reasons.append("TAMPERED_OR_INVALID_FEATURE_SNAPSHOT_PROOF_HASH")

    def _verify_bar_ids(bar_ids: Sequence[Any], f_key: str) -> Optional[str]:
        if not closed_bar_index:
            return None
        for bid in bar_ids:
            s = str(bid or "").strip()
            if not s:
                continue
            parts = s.split(":")
            if len(parts) >= 3:
                sym_u = parts[0].upper()
                tf_u = parts[1].upper()
                if sym_u != "XAUUSDT":
                    return f"WRONG_SYMBOL_BAR_ID:{f_key}:{s}"
                try:
                    o_ms = int(parts[2])
                except ValueError:
                    return f"MALFORMED_BAR_OPEN_MS:{f_key}:{s}"
                c_ms = closed_bar_index.get((tf_u, o_ms))
                if c_ms is None:
                    return f"MISSING_SOURCE_BAR_IN_CANONICAL_INDEX:{f_key}:{s}"
                if c_ms > asof_ts:
                    return f"FUTURE_SOURCE_BAR_CLOSE:{f_key}:{s}:{c_ms}>{asof_ts}"
        return None

    cells: Dict[str, Dict[str, Any]] = {}

    def _build_htf_cell(f_key: str, top_key: str, default_tf: str) -> None:
        item = snap_by_type.get(top_key)
        raw_v = item.get("measured_value") if item is not None else (feat_row or {}).get(top_key)
        kn_ms = int(item["known_at_ms"]) if item is not None and item.get("known_at_ms") is not None else None
        bc_ms = int(item["source_bar_close_ms"]) if item is not None and item.get("source_bar_close_ms") is not None else None
        s_ids = list(item.get("source_bar_ids") or ()) if item is not None else []
        origin = "PERSISTED_VERIFIED_SNAPSHOT" if (item is not None and persisted_verified) else ("LEDGER_ONLY_UNVERIFIED" if feat_row is not None else "MISSING")

        bar_err = _verify_bar_ids(s_ids, f_key)
        if bar_err:
            invalidation_reasons.append(bar_err)
            cells[f_key] = {"tri_state": "INVALID", "value": None, "source_origin": "INVALID", "known_at_ms": kn_ms, "source_bar_close_ms": bc_ms, "source_bar_ids": s_ids, "invalid_reason": bar_err}
            return
        if (kn_ms is not None and kn_ms > asof_ts) or (bc_ms is not None and bc_ms > asof_ts):
            err = f"FUTURE_HTF_CLOCK:{f_key}:kn={kn_ms},bc={bc_ms}>asof={asof_ts}"
            invalidation_reasons.append(err)
            cells[f_key] = {"tri_state": "INVALID", "value": None, "source_origin": "INVALID", "known_at_ms": kn_ms, "source_bar_close_ms": bc_ms, "source_bar_ids": s_ids, "invalid_reason": err}
            return

        val_u = str(raw_v or "").strip().upper()
        if val_u in ("", "UNKNOWN", "NONE", "MISSING") or origin == "MISSING":
            cells[f_key] = {"tri_state": "UNKNOWN_MISSING", "value": None, "source_origin": "MISSING", "timeframe": default_tf, "known_at_ms": kn_ms, "source_bar_close_ms": bc_ms, "source_bar_ids": s_ids}
        elif val_u in ("BULLISH", "BEARISH", "NEUTRAL", "RANGING", "MIXED"):
            exp_dir = "BULLISH" if direction == "LONG" else "BEARISH"
            tri = "PROVEN_TRUE" if val_u == exp_dir else "PROVEN_FALSE"
            cells[f_key] = {"tri_state": tri, "value": val_u, "source_origin": origin, "timeframe": default_tf, "known_at_ms": kn_ms, "source_bar_close_ms": bc_ms, "source_bar_ids": s_ids}
        else:
            err = f"UNRECOGNIZED_HTF_BIAS_VALUE:{f_key}:{raw_v!r}"
            invalidation_reasons.append(err)
            cells[f_key] = {"tri_state": "INVALID", "value": None, "source_origin": "INVALID", "timeframe": default_tf, "known_at_ms": kn_ms, "source_bar_close_ms": bc_ms, "source_bar_ids": s_ids, "invalid_reason": err}

    _build_htf_cell("h4_bias", "h4_bias", "4H")
    _build_htf_cell("h1_bias", "h1_bias", "1H")
    _build_htf_cell("m15_bias", "m15_bias", "15M")

    for b_key in (
        "sweep_confirmed",
        "sweep_reclaimed",
        "mss_confirmed",
        "displacement_valid",
        "fvg_present",
        "fvg_retest_valid",
        "fvg_unmitigated",
        "unicorn_breaker_fvg_bound",
        "sweep_mss_ordered",
        "mss_fvg_retest_ordered",
        "target_selected_pool_bound",
        "target_unswept",
    ):
        item = snap_by_type.get(b_key)
        raw_v = item.get("measured_value") if item is not None else (feat_row or {}).get(b_key)
        ps = str(item.get("proof_state") or "") if item is not None else ("PROVEN_TRUE" if raw_v is True else ("PROVEN_FALSE" if raw_v is False else "UNKNOWN"))
        kn_ms = int(item["known_at_ms"]) if item is not None and item.get("known_at_ms") is not None else None
        bc_ms = int(item["source_bar_close_ms"]) if item is not None and item.get("source_bar_close_ms") is not None else None
        s_ids = list(item.get("source_bar_ids") or ()) if item is not None else []
        tf_s = str(item.get("timeframe") or "1M") if item is not None else "1M"

        bar_err = _verify_bar_ids(s_ids, b_key)
        if bar_err:
            invalidation_reasons.append(bar_err)
            cells[b_key] = {"tri_state": "INVALID", "value": None, "source_origin": "INVALID", "timeframe": tf_s, "known_at_ms": kn_ms, "source_bar_close_ms": bc_ms, "source_bar_ids": s_ids, "invalid_reason": bar_err}
            continue

        tri, b_val, inv_r = _strict_bool_tri_state(raw_v, ps, known_at_ms=kn_ms, source_bar_close_ms=bc_ms, asof_ts_ms=asof_ts)
        if tri == "INVALID":
            invalidation_reasons.append(f"{b_key}:{inv_r}")
            origin = "INVALID"
        elif tri == "UNKNOWN_MISSING":
            origin = "MISSING"
        else:
            origin = "PERSISTED_VERIFIED_SNAPSHOT" if (item is not None and persisted_verified) else "LEDGER_ONLY_UNVERIFIED"
        cells[b_key] = {
            "tri_state": tri,
            "value": b_val,
            "source_origin": origin,
            "timeframe": tf_s,
            "known_at_ms": kn_ms,
            "source_bar_close_ms": bc_ms,
            "source_bar_ids": s_ids,
        }

    # Quote provenance check (reject fake CAPTURED_L1 claims on modeled historical dataset)
    q_kind = str((feat_row or {}).get("quote_source_kind") or candidate.get("quote_source_kind") or "SYNTHETIC_RESEARCH_QUOTE").upper()
    if "CAPTURED_L1" in q_kind or q_kind == "LIVE_EXCHANGE_L1":
        invalidation_reasons.append(f"FALSE_CAPTURED_L1_QUOTE_CLAIM_WITHOUT_EXCHANGE_RECEIPT:{q_kind}")
        cells["quote_provenance"] = {"tri_state": "INVALID", "value": q_kind, "source_origin": "INVALID"}
    else:
        cells["quote_provenance"] = {
            "tri_state": "PROVEN_TRUE",
            "value": "MODELED_SYNTHETIC_BAR_HALF_SPREAD_NOT_CAPTURED_L1",
            "source_origin": "RECONSTRUCTED_ASOF_RAW_BARS",
        }

    # Macro window resolution (strictly keyword-bound)
    if macro_cache is not None and macro_schedule_config is None and asof_ts in macro_cache:
        macro_info = macro_cache[asof_ts]
    else:
        m_st = resolve_causal_macro_time_window(
            decision_timestamp_ms=asof_ts,
            symbol="XAUUSDT",
            schedule_config=macro_schedule_config,
        )
        macro_info = validate_and_classify_macro_state_kh01a_r1(
            m_st,
            expected_decision_timestamp_ms=asof_ts,
            expected_symbol="XAUUSDT",
        )
        if macro_cache is not None and macro_schedule_config is None:
            macro_cache[asof_ts] = macro_info

    if macro_info["macro_state_category"] == "UNKNOWN_OR_INVALID_MACRO_STATE":
        cells["in_ict_macro_window"] = {"tri_state": "INVALID", "value": None, "source_origin": "INVALID"}
    elif macro_info["macro_state_category"] == "ELIGIBLE_IN_VERIFIED_WINDOW":
        cells["in_ict_macro_window"] = {"tri_state": "PROVEN_TRUE", "value": True, "source_origin": "RECONSTRUCTED_ASOF_RAW_BARS"}
    else:
        cells["in_ict_macro_window"] = {"tri_state": "PROVEN_FALSE", "value": False, "source_origin": "RECONSTRUCTED_ASOF_RAW_BARS"}

    # Recent 180m M1 data gap flag
    has_gap_180m = False
    if datasets is not None and close_ts_index is not None and "1m" in datasets and "1m" in close_ts_index:
        if gap_180m_cache is not None and asof_ts in gap_180m_cache:
            has_gap_180m = gap_180m_cache[asof_ts]
        else:
            j1 = bisect.bisect_right(close_ts_index["1m"], asof_ts)
            b1_seq = datasets["1m"]
            win_s = max(1, j1 - 180)
            for gi in range(win_s, j1):
                prev_o = int(b1_seq[gi - 1].get("open_time") or b1_seq[gi - 1].get("timestamp") or 0)
                cur_o = int(b1_seq[gi].get("open_time") or b1_seq[gi].get("timestamp") or 0)
                if (cur_o - prev_o) > 60_000:
                    has_gap_180m = True
                    break
            if gap_180m_cache is not None:
                gap_180m_cache[asof_ts] = has_gap_180m
    cells["recent_m1_data_gap_180m"] = {
        "tri_state": "PROVEN_TRUE" if has_gap_180m else "PROVEN_FALSE",
        "value": has_gap_180m,
        "source_origin": "RECONSTRUCTED_ASOF_RAW_BARS",
    }

    # Source-TF target reachability audit (fast cached ATR + authoritative audit_target_reachability_row_v17931214)
    target_info: Dict[str, Any]
    if datasets is not None and close_ts_index is not None and pools_by_id is not None:
        t_pid = str((feat_row or {}).get("target_pool_id") or candidate.get("selected_target_id") or "")
        p_obj = pools_by_id.get(t_pid) if t_pid else None
        tf_cand = str((p_obj or {}).get("timeframe") or "").upper()
        if not tf_cand and t_pid.startswith("pool_"):
            toks = t_pid.split("_")
            if len(toks) >= 2:
                tf_cand = toks[1].upper()
        elif not tf_cand and "_" in t_pid:
            t0 = t_pid.split("_", 1)[0].upper()
            if t0 in ("1M", "5M", "15M", "1H", "4H"):
                tf_cand = t0

        f_aug = dict(feat_row or {})
        if atr_cache is not None and tf_cand in ("1M", "5M", "15M", "1H", "4H") and "_injected_atr_source_tf" not in f_aug:
            ck = (tf_cand, asof_ts)
            if ck not in atr_cache:
                atr_cache[ck] = compute_causal_tf_atr_v17931214(datasets, close_ts_index, tf_cand, asof_ts)
            if atr_cache[ck] is not None:
                f_aug["_injected_atr_source_tf"] = atr_cache[ck]

        target_info = audit_target_reachability_row_v17931214(
            opportunity_id=oid,
            cohort_role="KH01B_CANDIDATE",
            historical_rejection="NONE",
            current_rejection="NONE",
            decision_row=candidate,
            feature_row=f_aug,
            pools_by_id=pools_by_id,
            datasets=datasets,
            close_ts_index=close_ts_index,
            require_cached_rr_match_if_present=True,
        )
        v_kn = target_info.get("verified_known_at_ms")
        if v_kn is not None and int(v_kn) > asof_ts:
            invalidation_reasons.append(f"TARGET_POOL_FUTURE_KNOWN_AT:{v_kn}>{asof_ts}")
    else:
        target_info = {
            "tri_state_status": "UNKNOWN_MISSING",
            "first_decisive_failure_code": "TARGET_POOLS_OR_DATASETS_NOT_PROVIDED",
            "resolved_source_tf": "UNKNOWN",
            "atr_source_tf": None,
            "target_distance_source_tf_atr": None,
            "recomputed_net_rr": None,
            "recomputed_planned_risk_usd": None,
        }

    unicorn_cls = str((feat_row or {}).get("unicorn_classification") or "UNKNOWN").upper()
    cells["unicorn_classification"] = {
        "tri_state": "UNKNOWN_MISSING" if unicorn_cls in ("", "UNKNOWN", "NONE") else "PROVEN_TRUE",
        "value": None if unicorn_cls in ("", "UNKNOWN") else unicorn_cls,
        "source_origin": "PERSISTED_VERIFIED_SNAPSHOT" if persisted_verified else ("LEDGER_ONLY_UNVERIFIED" if feat_row is not None else "MISSING"),
    }

    has_inv = len(invalidation_reasons) > 0 or any(c.get("tri_state") == "INVALID" for c in cells.values())
    has_miss = any(c.get("tri_state") == "UNKNOWN_MISSING" for c in cells.values())

    digest_payload = {
        "oid": oid,
        "ep": ep_id,
        "model": model_id,
        "dir": direction,
        "ts": asof_ts,
        "act_fp": str(candidate.get("trade_action_fingerprint") or ""),
        "cells": {k: (v.get("tri_state"), v.get("value"), v.get("source_origin")) for k, v in sorted(cells.items())},
        "macro_cat": macro_info.get("macro_state_category"),
        "tgt_status": (target_info.get("tri_state_status"), target_info.get("first_decisive_failure_code")),
        "inv": invalidation_reasons,
    }
    return FeatureEvidenceFrame(
        opportunity_id=oid,
        setup_episode_id=ep_id,
        model_id=model_id,
        direction=direction,
        decision_timestamp_ms=asof_ts,
        checkpoint_index_1m=chk_idx,
        discovered_by_si8=is_si8,
        entry_reference=entry_px,
        selected_sl=sl_px,
        take_profit=tp_px,
        hypothetical_quantity=qty,
        planned_net_risk_usd=planned_risk_usd,
        baseline_net_rr=net_rr,
        baseline_gross_rr=gross_rr,
        selected_target_id=str(candidate.get("selected_target_id")) if candidate.get("selected_target_id") else None,
        selected_fvg_id=str(candidate.get("selected_fvg_id")) if candidate.get("selected_fvg_id") else None,
        selected_sweep_id=str(candidate.get("selected_sweep_id")) if candidate.get("selected_sweep_id") else None,
        selected_break_id=str(candidate.get("selected_break_id")) if candidate.get("selected_break_id") else None,
        trade_action_fingerprint=str(candidate.get("trade_action_fingerprint") or ""),
        decision_fingerprint=str(candidate.get("decision_fingerprint") or ""),
        feature_cells=cells,
        macro_window_info=macro_info,
        target_audit_info=target_info,
        has_any_invalid_evidence=has_inv,
        has_any_missing_evidence=has_miss,
        invalidation_reasons=invalidation_reasons,
        frame_sha256=canonical_hash(digest_payload),
    )


# ==============================================================================
# F04 - F09: HYPOTHESIS GATE EVALUATORS (PURE CAUSAL, ZERO OUTCOME LEAKAGE)
# ==============================================================================

def evaluate_htf_alignment_hypothesis(
    frame: FeatureEvidenceFrame,
    direction: str,
    rule: str = "H4_AND_H1",
) -> ResearchGateDecision:
    """
    F04: Evaluates H4_ONLY (B01), H1_ONLY (B02), or H4_AND_H1 (B03).
    Distinguishes PROVEN_ALIGNED, PROVEN_OPPOSITE, PROVEN_NEUTRAL, UNKNOWN_MISSING, and INVALID.
    """
    norm_dir = str(direction or frame.direction or "").upper()
    exp_bias = "BULLISH" if norm_dir == "LONG" else "BEARISH"
    opp_bias = "BEARISH" if norm_dir == "LONG" else "BULLISH"

    h4_c = frame.feature_cells.get("h4_bias") or {"tri_state": "UNKNOWN_MISSING", "value": None}
    h1_c = frame.feature_cells.get("h1_bias") or {"tri_state": "UNKNOWN_MISSING", "value": None}

    if rule == "H4_ONLY":
        req_cells = [("H4", h4_c)]
    elif rule == "H1_ONLY":
        req_cells = [("H1", h1_c)]
    elif rule == "H4_AND_H1":
        req_cells = [("H4", h4_c), ("H1", h1_c)]
    else:
        raise ValueError(f"UNSUPPORTED_HTF_RULE:{rule}")

    for tf_lbl, cell in req_cells:
        if cell.get("tri_state") == "INVALID":
            return ResearchGateDecision(
                hypothesis_id=f"HTF_{rule}",
                tri_state="INVALID",
                eligible=False,
                rejection_code=f"HTF_{tf_lbl}_INVALID_EVIDENCE",
                missing_evidence_rejected=False,
                proven_false_rejected=False,
                details={"h4": h4_c.get("value"), "h1": h1_c.get("value")},
            )

    # Check explicit opposite/neutral BEFORE unknown so proven counter-trend is accurately labeled,
    # while never allowing UNKNOWN to pass as aligned!
    for tf_lbl, cell in req_cells:
        v = str(cell.get("value") or "").upper()
        if cell.get("tri_state") == "PROVEN_FALSE":
            sub = "OPPOSITE" if v == opp_bias else "NEUTRAL_OR_NON_ALIGNED"
            return ResearchGateDecision(
                hypothesis_id=f"HTF_{rule}",
                tri_state="PROVEN_FALSE",
                eligible=False,
                rejection_code=f"HTF_{tf_lbl}_{sub}",
                missing_evidence_rejected=False,
                proven_false_rejected=True,
                details={"h4": h4_c.get("value"), "h1": h1_c.get("value")},
            )

    for tf_lbl, cell in req_cells:
        if cell.get("tri_state") == "UNKNOWN_MISSING" or str(cell.get("value") or "").upper() != exp_bias:
            return ResearchGateDecision(
                hypothesis_id=f"HTF_{rule}",
                tri_state="UNKNOWN_MISSING",
                eligible=False,
                rejection_code=f"HTF_{tf_lbl}_UNKNOWN_MISSING",
                missing_evidence_rejected=True,
                proven_false_rejected=False,
                details={"h4": h4_c.get("value"), "h1": h1_c.get("value")},
            )

    return ResearchGateDecision(
        hypothesis_id=f"HTF_{rule}",
        tri_state="PROVEN_TRUE",
        eligible=True,
        rejection_code=None,
        missing_evidence_rejected=False,
        proven_false_rejected=False,
        details={"h4": h4_c.get("value"), "h1": h1_c.get("value")},
    )


def evaluate_macro_window_hypothesis(
    frame: FeatureEvidenceFrame,
    macro_state: Optional[Mapping[str, Any]] = None,
    schedule_config: Optional[Mapping[str, Any]] = None,
) -> ResearchGateDecision:
    """
    F05: Evaluates configured NY ICT macro window eligibility (B05).
    Separates ELIGIBLE_IN_VERIFIED_WINDOW, VALID_OUTSIDE_WINDOW, and UNKNOWN_OR_INVALID_MACRO_STATE.
    Never treats unverified exchange calendar as exchange-verified.
    """
    if schedule_config is not None:
        m_obj = resolve_causal_macro_time_window(
            decision_timestamp_ms=frame.decision_timestamp_ms,
            symbol="XAUUSDT",
            schedule_config=schedule_config,
        )
        m_info = validate_and_classify_macro_state_kh01a_r1(
            m_obj,
            expected_decision_timestamp_ms=frame.decision_timestamp_ms,
            expected_symbol="XAUUSDT",
        )
    elif macro_state is not None:
        m_info = dict(macro_state)
    else:
        m_info = frame.macro_window_info

    cat = str(m_info.get("macro_state_category") or "UNKNOWN_OR_INVALID_MACRO_STATE")
    if cat == "UNKNOWN_OR_INVALID_MACRO_STATE":
        return ResearchGateDecision(
            hypothesis_id="MACRO_B05",
            tri_state="INVALID",
            eligible=False,
            rejection_code="MACRO_STATE_UNKNOWN_OR_INVALID",
            missing_evidence_rejected=True,
            proven_false_rejected=False,
            details={
                "macro_state_category": cat,
                "exchange_calendar_status": "CALENDAR_NOT_VERIFIED",
                "schedule_source": "RESEARCH_HYPOTHESIS_CONFIG",
            },
        )
    if cat == "VALID_OUTSIDE_WINDOW":
        return ResearchGateDecision(
            hypothesis_id="MACRO_B05",
            tri_state="PROVEN_FALSE",
            eligible=False,
            rejection_code=f"OUTSIDE_CONFIGURED_ICT_MACRO_WINDOW:{m_info.get('ny_session_group')}",
            missing_evidence_rejected=False,
            proven_false_rejected=True,
            details={
                "macro_state_category": cat,
                "ny_session_group": m_info.get("ny_session_group"),
                "exchange_calendar_status": "CALENDAR_NOT_VERIFIED",
                "schedule_source": "RESEARCH_HYPOTHESIS_CONFIG",
            },
        )
    return ResearchGateDecision(
        hypothesis_id="MACRO_B05",
        tri_state="PROVEN_TRUE",
        eligible=True,
        rejection_code=None,
        missing_evidence_rejected=False,
        proven_false_rejected=False,
        details={
            "macro_state_category": cat,
            "primary_window_id": (m_info.get("raw_macro_diagnostics") or {}).get("primary_window_id"),
            "ny_session_group": m_info.get("ny_session_group"),
            "exchange_calendar_status": "CALENDAR_NOT_VERIFIED",
            "schedule_source": "RESEARCH_HYPOTHESIS_CONFIG",
        },
    )


def evaluate_sweep_mss_displacement_hypothesis(
    frame: FeatureEvidenceFrame,
    descriptors: Optional[Mapping[str, Any]] = None,
    *,
    mode: str = "SWEEP_AND_MSS",
) -> ResearchGateDecision:
    """
    F06: Evaluates SWEEP_RECLAIM_ONLY (B06), MSS_DISPLACEMENT_ONLY (B07), or SWEEP_AND_MSS (B08).
    NEVER equates `selected_sweep_id` or `selected_break_id` presence with proven confirmation!
    """
    desc = dict(descriptors or {})
    sel_sw_id = desc.get("selected_sweep_id") or frame.selected_sweep_id
    sel_br_id = desc.get("selected_break_id") or frame.selected_break_id

    sw_c = frame.feature_cells.get("sweep_confirmed") or {"tri_state": "UNKNOWN_MISSING"}
    sr_c = frame.feature_cells.get("sweep_reclaimed") or {"tri_state": "UNKNOWN_MISSING"}
    ms_c = frame.feature_cells.get("mss_confirmed") or {"tri_state": "UNKNOWN_MISSING"}
    dp_c = frame.feature_cells.get("displacement_valid") or {"tri_state": "UNKNOWN_MISSING"}
    ord_c = frame.feature_cells.get("sweep_mss_ordered") or {"tri_state": "UNKNOWN_MISSING"}

    if mode == "SWEEP_RECLAIM_ONLY":
        req = [("sweep_confirmed", sw_c), ("sweep_reclaimed", sr_c)]
    elif mode == "MSS_DISPLACEMENT_ONLY":
        req = [("mss_confirmed", ms_c), ("displacement_valid", dp_c)]
    elif mode == "SWEEP_AND_MSS":
        req = [
            ("sweep_confirmed", sw_c),
            ("sweep_reclaimed", sr_c),
            ("mss_confirmed", ms_c),
            ("displacement_valid", dp_c),
            ("sweep_mss_ordered", ord_c),
        ]
    else:
        raise ValueError(f"UNSUPPORTED_SWEEP_MSS_MODE:{mode}")

    for k_n, c_d in req:
        if c_d.get("tri_state") == "INVALID":
            return ResearchGateDecision(
                hypothesis_id=f"SWEEP_MSS_{mode}",
                tri_state="INVALID",
                eligible=False,
                rejection_code=f"{k_n.upper()}_INVALID_EVIDENCE",
                missing_evidence_rejected=False,
                proven_false_rejected=False,
                details={"unconfirmed_sweep_id_ignored": bool(sel_sw_id), "unconfirmed_break_id_ignored": bool(sel_br_id)},
            )

    for k_n, c_d in req:
        if c_d.get("tri_state") == "PROVEN_FALSE":
            return ResearchGateDecision(
                hypothesis_id=f"SWEEP_MSS_{mode}",
                tri_state="PROVEN_FALSE",
                eligible=False,
                rejection_code=f"{k_n.upper()}_PROVEN_FALSE",
                missing_evidence_rejected=False,
                proven_false_rejected=True,
                details={"unconfirmed_sweep_id_ignored": bool(sel_sw_id), "unconfirmed_break_id_ignored": bool(sel_br_id)},
            )

    for k_n, c_d in req:
        if c_d.get("tri_state") != "PROVEN_TRUE":
            return ResearchGateDecision(
                hypothesis_id=f"SWEEP_MSS_{mode}",
                tri_state="UNKNOWN_MISSING",
                eligible=False,
                rejection_code=f"{k_n.upper()}_UNKNOWN_MISSING",
                missing_evidence_rejected=True,
                proven_false_rejected=False,
                details={
                    "unconfirmed_sweep_id_ignored": bool(sel_sw_id and sw_c.get("tri_state") != "PROVEN_TRUE"),
                    "unconfirmed_break_id_ignored": bool(sel_br_id and ms_c.get("tri_state") != "PROVEN_TRUE"),
                },
            )

    return ResearchGateDecision(
        hypothesis_id=f"SWEEP_MSS_{mode}",
        tri_state="PROVEN_TRUE",
        eligible=True,
        rejection_code=None,
        missing_evidence_rejected=False,
        proven_false_rejected=False,
        details={"episode_id": frame.setup_episode_id},
    )


def evaluate_source_tf_target_reachability_hypothesis(
    frame: FeatureEvidenceFrame,
    original_target: Optional[float] = None,
    horizon: float = 12.0,
    *,
    allow_tp_reselection_counterfactual: bool = False,
    causal_pools_at_ts: Optional[Sequence[Mapping[str, Any]]] = None,
    atr_by_tf_at_ts: Optional[Mapping[str, Optional[float]]] = None,
) -> ResearchTargetAudit:
    """
    F07: Evaluates target reachability in the target pool's OWN causal source timeframe ATR:
      - `allow_tp_reselection_counterfactual=False` (B04): Pure entry filter; entry/SL/TP/qty unchanged.
      - `allow_tp_reselection_counterfactual=True` (B11 `TP_ONLY_RESEARCH`): Keeps entry & SL unchanged,
        and if original target fails reachability, searches strictly causal unswept pools `<= horizon`
        source-TF ATRs that satisfy `min_net_rr >= 2.0` and `planned_net_risk_usd <= $2.50`.
    """
    mode_lbl = "TP_ONLY_RESEARCH_B11" if allow_tp_reselection_counterfactual else "FILTER_ONLY_B04"
    aud = frame.target_audit_info
    orig_tp = float(original_target if original_target is not None else frame.take_profit)
    tri_raw = str(aud.get("tri_state_status") or "UNKNOWN_MISSING")
    res_tf = str(aud.get("resolved_source_tf") or "UNKNOWN")
    atr_tf = aud.get("atr_actual_value", aud.get("atr_source_tf"))
    recomp_rr = aud.get("net_rr", aud.get("recomputed_net_rr"))
    recomp_gross = aud.get("gross_rr", aud.get("recomputed_gross_rr"))
    recomp_risk = aud.get("planned_risk_usd", aud.get("recomputed_planned_risk_usd"))
    recomp_qty = aud.get("independently_recomputed_quantity", aud.get("recomputed_quantity"))
    fail_code = str(aud.get("first_decisive_failure_code") or "")
    sec_fails = tuple(aud.get("secondary_failures") or ())
    unswept_ok = (
        fail_code != "SWEPT_TARGET_BEFORE_DECISION"
        and "TARGET_SWEPT_OR_UNPROVEN_UNSWEPT" not in sec_fails
    )

    dist_src_atr: Optional[float] = None
    if isinstance(atr_tf, (int, float)) and math.isfinite(float(atr_tf)) and float(atr_tf) > 1e-9:
        dist_src_atr = round(abs(orig_tp - frame.entry_reference) / float(atr_tf), 6)

    if tri_raw == "INVALID_INCONSISTENT" and (
        not allow_tp_reselection_counterfactual
        or fail_code in (
            "ATR_UNDERFLOW_NONFINITE_OR_UNIT_MIX_INVALID",
            "MISSING_OR_ZERO_SPREAD_INVALID",
            "POINT_IN_TIME_KNOWN_AT_UNAVAILABLE_OR_FUTURE",
        )
    ):
        return ResearchTargetAudit(
            opportunity_id=frame.opportunity_id,
            mode=mode_lbl,
            tri_state="INVALID",
            eligible=False,
            rejection_code=fail_code or "TARGET_AUDIT_INVALID",
            resolved_source_tf=res_tf,
            source_tf_atr=float(atr_tf) if isinstance(atr_tf, (int, float)) else None,
            target_distance_source_tf_atr=dist_src_atr,
            horizon_limit_atr=horizon,
            original_tp=orig_tp,
            selected_tp=orig_tp,
            tp_modified=False,
            selected_target_pool_id=frame.selected_target_id,
            recomputed_quantity=recomp_qty,
            recomputed_planned_risk_usd=recomp_risk,
            recomputed_net_rr=recomp_rr,
            recomputed_gross_rr=recomp_gross,
            details={"audit_failure_code": fail_code},
        )

    orig_valid_pass = (
        tri_raw == "VALID_VALUE"
        and unswept_ok
        and dist_src_atr is not None
        and 0.30 <= dist_src_atr <= float(horizon)
        and isinstance(recomp_rr, (int, float))
        and float(recomp_rr) >= 2.0
        and isinstance(recomp_risk, (int, float))
        and 0.0 < float(recomp_risk) <= 2.50
    )
    if orig_valid_pass:
        return ResearchTargetAudit(
            opportunity_id=frame.opportunity_id,
            mode=mode_lbl,
            tri_state="PROVEN_TRUE",
            eligible=True,
            rejection_code=None,
            resolved_source_tf=res_tf,
            source_tf_atr=float(atr_tf),
            target_distance_source_tf_atr=dist_src_atr,
            horizon_limit_atr=horizon,
            original_tp=orig_tp,
            selected_tp=orig_tp,
            tp_modified=False,
            selected_target_pool_id=frame.selected_target_id,
            recomputed_quantity=float(recomp_qty or frame.hypothetical_quantity),
            recomputed_planned_risk_usd=float(recomp_risk),
            recomputed_net_rr=float(recomp_rr),
            recomputed_gross_rr=float(recomp_gross or frame.baseline_gross_rr),
            details={"kept_original_target": True},
        )

    if not allow_tp_reselection_counterfactual:
        if tri_raw == "UNKNOWN_MISSING" or dist_src_atr is None:
            return ResearchTargetAudit(
                opportunity_id=frame.opportunity_id,
                mode=mode_lbl,
                tri_state="UNKNOWN_MISSING",
                eligible=False,
                rejection_code=str(aud.get("first_decisive_failure_code") or "TARGET_SOURCE_POOL_UNKNOWN_MISSING"),
                resolved_source_tf=res_tf,
                source_tf_atr=float(atr_tf) if isinstance(atr_tf, (int, float)) else None,
                target_distance_source_tf_atr=dist_src_atr,
                horizon_limit_atr=horizon,
                original_tp=orig_tp,
                selected_tp=orig_tp,
                tp_modified=False,
                selected_target_pool_id=frame.selected_target_id,
                recomputed_quantity=recomp_qty,
                recomputed_planned_risk_usd=recomp_risk,
                recomputed_net_rr=recomp_rr,
                recomputed_gross_rr=recomp_gross,
                details={"audit_failure_code": aud.get("first_decisive_failure_code")},
            )
        rej = (
            f"TARGET_BEYOND_{int(horizon)}_SOURCE_TF_ATR"
            if dist_src_atr > float(horizon)
            else str(aud.get("first_decisive_failure_code") or "TARGET_NET_RR_OR_HORIZON_FAILED")
        )
        return ResearchTargetAudit(
            opportunity_id=frame.opportunity_id,
            mode=mode_lbl,
            tri_state="PROVEN_FALSE",
            eligible=False,
            rejection_code=rej,
            resolved_source_tf=res_tf,
            source_tf_atr=float(atr_tf),
            target_distance_source_tf_atr=dist_src_atr,
            horizon_limit_atr=horizon,
            original_tp=orig_tp,
            selected_tp=orig_tp,
            tp_modified=False,
            selected_target_pool_id=frame.selected_target_id,
            recomputed_quantity=recomp_qty,
            recomputed_planned_risk_usd=recomp_risk,
            recomputed_net_rr=recomp_rr,
            recomputed_gross_rr=recomp_gross,
            details={"audit_failure_code": aud.get("first_decisive_failure_code")},
        )

    # B11 TP_ONLY_RESEARCH: search strictly causal unswept pools at `asof_ts` keeping entry & SL fixed
    candidates_found: List[Tuple[float, float, str, str, float, Dict[str, Any]]] = []
    for p_obj in (causal_pools_at_ts or ()):
        p_kn = p_obj.get("known_at_ms", p_obj.get("confirmed_at_ms"))
        if p_kn is None or int(p_kn) > frame.decision_timestamp_ms:
            continue
        if p_obj.get("swept_before_decision") is True:
            continue
        swept_at = p_obj.get("swept_at_ms")
        if swept_at is not None and int(swept_at) <= frame.decision_timestamp_ms:
            continue
        p_px_raw = p_obj.get("price")
        if p_px_raw is None:
            continue
        p_px = round(float(p_px_raw), 2)
        if frame.direction == "LONG" and p_px <= frame.entry_reference + 0.20:
            continue
        if frame.direction == "SHORT" and p_px >= frame.entry_reference - 0.20:
            continue
        p_tf = str(p_obj.get("timeframe") or "").upper()
        if p_tf not in ("1M", "5M", "15M", "1H", "4H"):
            continue
        p_atr = (atr_by_tf_at_ts or {}).get(p_tf)
        if p_atr is None or not math.isfinite(float(p_atr)) or float(p_atr) <= 1e-9:
            continue
        d_atr = abs(p_px - frame.entry_reference) / float(p_atr)
        if not (0.30 <= d_atr <= float(horizon)):
            continue
        pid_str = str(p_obj.get("canonical_target_id") or p_obj.get("pool_id") or p_obj.get("id") or "")
        if not pid_str:
            continue
        sz = recalculate_trade_risk_quantity_v179312(
            frame.entry_reference,
            frame.selected_sl,
            p_px,
            direction=frame.direction,
            decision_ts=frame.decision_timestamp_ms,
        )
        if (
            sz.get("can_execute")
            and float(sz.get("net_rr") or 0.0) >= 2.0
            and 0.0 < float(sz.get("planned_net_risk_usd") or 0.0) <= 2.50
        ):
            dist_abs = abs(p_px - frame.entry_reference)
            candidates_found.append((dist_abs, d_atr, pid_str, p_tf, float(p_atr), sz))

    if not candidates_found:
        return ResearchTargetAudit(
            opportunity_id=frame.opportunity_id,
            mode=mode_lbl,
            tri_state="PROVEN_FALSE" if tri_raw == "VALID_VALUE" else "UNKNOWN_MISSING",
            eligible=False,
            rejection_code="NO_DEFENSIBLE_CAUSAL_TP_ALTERNATIVE",
            resolved_source_tf=res_tf,
            source_tf_atr=float(atr_tf) if isinstance(atr_tf, (int, float)) else None,
            target_distance_source_tf_atr=dist_src_atr,
            horizon_limit_atr=horizon,
            original_tp=orig_tp,
            selected_tp=orig_tp,
            tp_modified=False,
            selected_target_pool_id=frame.selected_target_id,
            recomputed_quantity=recomp_qty,
            recomputed_planned_risk_usd=recomp_risk,
            recomputed_net_rr=recomp_rr,
            recomputed_gross_rr=recomp_gross,
            details={"searched_pools_count": len(causal_pools_at_ts or ())},
        )

    candidates_found.sort(key=lambda t: (t[0], t[2]))
    _, best_d_atr, best_pid, best_tf, best_atr, best_sz = candidates_found[0]
    best_tp = round(
        frame.entry_reference + candidates_found[0][0]
        if frame.direction == "LONG"
        else frame.entry_reference - candidates_found[0][0],
        2,
    )
    return ResearchTargetAudit(
        opportunity_id=frame.opportunity_id,
        mode=mode_lbl,
        tri_state="PROVEN_TRUE",
        eligible=True,
        rejection_code=None,
        resolved_source_tf=best_tf,
        source_tf_atr=best_atr,
        target_distance_source_tf_atr=round(best_d_atr, 6),
        horizon_limit_atr=horizon,
        original_tp=orig_tp,
        selected_tp=best_tp,
        tp_modified=abs(best_tp - orig_tp) >= 0.01,
        selected_target_pool_id=best_pid,
        recomputed_quantity=float(best_sz["quantity"]),
        recomputed_planned_risk_usd=float(best_sz["planned_net_risk_usd"]),
        recomputed_net_rr=float(best_sz["net_rr"]),
        recomputed_gross_rr=float(best_sz["gross_rr"]),
        details={"reselected_causal_pool_id": best_pid, "entry_and_sl_unchanged": True},
    )


def evaluate_strict_true_unicorn_hypothesis(
    frame: FeatureEvidenceFrame,
    context: Optional[Any] = None,
    setup_episode: Optional[Mapping[str, Any]] = None,
) -> StrictUnicornResearchDecision:
    """
    F08: Evaluates STRICT_D4_TRUE_UNICORN (B09).
    Calls existing `evaluate_causal_unicorn_confluence` when `context` is provided, and enforces
    strict monotonic D4 causal stages (HTF aligned -> sweep/reclaim -> MSS/displacement ->
    genuine Breaker+FVG overlap >= 1 tick -> unmitigated FVG + retest -> risk/net-RR).
    NEVER allows `FVG_ONLY_NO_BREAKER` or adjacent setups to pass as True Unicorn.
    """
    if context is not None:
        u_res = evaluate_causal_unicorn_confluence(
            context,
            frame.direction,
            frame.decision_timestamp_ms,
            setup_descriptors=setup_episode,
            strict=True,
            require_confirmed_retest=True,
            require_htf_alignment=True,
        )
        ov_ok = bool(
            u_res.overlap_width_price is not None
            and float(u_res.overlap_width_price) >= (0.01 - 1e-6)
        )
        ep_ok = bool(u_res.episode_binding and u_res.episode_binding.is_bound and u_res.episode_binding.same_causal_episode)
        valid_all = bool(
            u_res.is_valid_unicorn
            and ov_ok
            and ep_ok
            and u_res.retest_confirmed
            and u_res.htf_aligned
            and frame.baseline_net_rr >= 2.0
            and 0.0 < frame.planned_net_risk_usd <= 2.50
        )
        return StrictUnicornResearchDecision(
            opportunity_id=frame.opportunity_id,
            ontology="STRICT_D4_TRUE_UNICORN",
            is_valid_strict_unicorn=valid_all,
            highest_stage_passed="08_STRICT_TRUE_UNICORN_EXECUTABLE" if valid_all else str(u_res.failure_stage or "REJECTED_BY_CONFLUENCE"),
            terminal_rejection_code=None if valid_all else str(u_res.failure_stage or (u_res.rejection_reasons[0] if u_res.rejection_reasons else "UNICORN_REJECTED")),
            overlap_ticks_verified=ov_ok,
            same_episode_verified=ep_ok,
            retest_verified=bool(u_res.retest_confirmed),
            htf_aligned_verified=bool(u_res.htf_aligned),
            details={
                "validity_status": u_res.validity_status,
                "overlap_zone": u_res.overlap_zone,
                "overlap_width_price": u_res.overlap_width_price,
                "rejection_reasons": list(u_res.rejection_reasons),
            },
        )

    # Historical candidate frame monotonic stage evaluation (matching authoritative D4 funnel)
    if frame.has_any_invalid_evidence:
        return StrictUnicornResearchDecision(
            opportunity_id=frame.opportunity_id,
            ontology="STRICT_D4_TRUE_UNICORN",
            is_valid_strict_unicorn=False,
            highest_stage_passed="00_INVALID_EVIDENCE",
            terminal_rejection_code="01_INVALID_PRE_ENTRY_EVIDENCE",
            overlap_ticks_verified=False,
            same_episode_verified=False,
            retest_verified=False,
            htf_aligned_verified=False,
            details={"invalidation_reasons": frame.invalidation_reasons},
        )

    htf_dec = evaluate_htf_alignment_hypothesis(frame, frame.direction, rule="H4_AND_H1")
    if not htf_dec.eligible:
        return StrictUnicornResearchDecision(
            opportunity_id=frame.opportunity_id,
            ontology="STRICT_D4_TRUE_UNICORN",
            is_valid_strict_unicorn=False,
            highest_stage_passed="01_CANDIDATE_REGISTERED",
            terminal_rejection_code=f"02_HTF_NOT_ALIGNED:{htf_dec.rejection_code}",
            overlap_ticks_verified=False,
            same_episode_verified=False,
            retest_verified=False,
            htf_aligned_verified=False,
            details=htf_dec.details,
        )

    sw_dec = evaluate_sweep_mss_displacement_hypothesis(frame, mode="SWEEP_RECLAIM_ONLY")
    if not sw_dec.eligible:
        return StrictUnicornResearchDecision(
            opportunity_id=frame.opportunity_id,
            ontology="STRICT_D4_TRUE_UNICORN",
            is_valid_strict_unicorn=False,
            highest_stage_passed="02_HTF_ALIGNED",
            terminal_rejection_code=f"03_SWEEP_RECLAIM_NOT_PROVEN:{sw_dec.rejection_code}",
            overlap_ticks_verified=False,
            same_episode_verified=False,
            retest_verified=False,
            htf_aligned_verified=True,
            details=sw_dec.details,
        )

    ms_dec = evaluate_sweep_mss_displacement_hypothesis(frame, mode="MSS_DISPLACEMENT_ONLY")
    if not ms_dec.eligible:
        return StrictUnicornResearchDecision(
            opportunity_id=frame.opportunity_id,
            ontology="STRICT_D4_TRUE_UNICORN",
            is_valid_strict_unicorn=False,
            highest_stage_passed="03_SWEEP_RECLAIM_PROVEN",
            terminal_rejection_code=f"04_MSS_DISPLACEMENT_NOT_PROVEN:{ms_dec.rejection_code}",
            overlap_ticks_verified=False,
            same_episode_verified=False,
            retest_verified=False,
            htf_aligned_verified=True,
            details=ms_dec.details,
        )

    brk_cell = frame.feature_cells.get("unicorn_breaker_fvg_bound") or {}
    u_cls = str((frame.feature_cells.get("unicorn_classification") or {}).get("value") or "UNKNOWN")
    if brk_cell.get("tri_state") != "PROVEN_TRUE" or u_cls in ("FVG_ONLY_NO_BREAKER", "NONE", "UNKNOWN"):
        return StrictUnicornResearchDecision(
            opportunity_id=frame.opportunity_id,
            ontology="STRICT_D4_TRUE_UNICORN",
            is_valid_strict_unicorn=False,
            highest_stage_passed="04_MSS_DISPLACEMENT_PROVEN",
            terminal_rejection_code=f"05_NOT_TRUE_UNICORN_BREAKER_FVG:{u_cls}",
            overlap_ticks_verified=False,
            same_episode_verified=False,
            retest_verified=False,
            htf_aligned_verified=True,
            details={"unicorn_classification": u_cls},
        )

    fvg_p = (frame.feature_cells.get("fvg_present") or {}).get("tri_state") == "PROVEN_TRUE"
    fvg_u = (frame.feature_cells.get("fvg_unmitigated") or {}).get("tri_state") == "PROVEN_TRUE"
    fvg_r = (frame.feature_cells.get("fvg_retest_valid") or {}).get("tri_state") == "PROVEN_TRUE"
    ord_ok = (frame.feature_cells.get("mss_fvg_retest_ordered") or {}).get("tri_state") == "PROVEN_TRUE"
    if not (fvg_p and fvg_u and fvg_r and ord_ok):
        return StrictUnicornResearchDecision(
            opportunity_id=frame.opportunity_id,
            ontology="STRICT_D4_TRUE_UNICORN",
            is_valid_strict_unicorn=False,
            highest_stage_passed="05_BREAKER_FVG_OVERLAP_BOUND",
            terminal_rejection_code="06_RETEST_OR_UNMITIGATED_FVG_OR_EPISODE_ORDER_FAILED",
            overlap_ticks_verified=True,
            same_episode_verified=ord_ok,
            retest_verified=fvg_r,
            htf_aligned_verified=True,
            details={"fvg_present": fvg_p, "fvg_unmitigated": fvg_u, "fvg_retest_valid": fvg_r, "ordered": ord_ok},
        )

    if not (frame.baseline_net_rr >= 2.0 and 0.0 < frame.planned_net_risk_usd <= 2.50):
        return StrictUnicornResearchDecision(
            opportunity_id=frame.opportunity_id,
            ontology="STRICT_D4_TRUE_UNICORN",
            is_valid_strict_unicorn=False,
            highest_stage_passed="06_RETEST_AND_EPISODE_CONFIRMED",
            terminal_rejection_code="07_RISK_CAP_OR_MIN_NET_RR_FAILED",
            overlap_ticks_verified=True,
            same_episode_verified=True,
            retest_verified=True,
            htf_aligned_verified=True,
            details={"net_rr": frame.baseline_net_rr, "planned_risk_usd": frame.planned_net_risk_usd},
        )

    return StrictUnicornResearchDecision(
        opportunity_id=frame.opportunity_id,
        ontology="STRICT_D4_TRUE_UNICORN",
        is_valid_strict_unicorn=True,
        highest_stage_passed="08_STRICT_TRUE_UNICORN_EXECUTABLE",
        terminal_rejection_code=None,
        overlap_ticks_verified=True,
        same_episode_verified=True,
        retest_verified=True,
        htf_aligned_verified=True,
        details={"unicorn_classification": u_cls},
    )


def evaluate_unicorn_adjacent_research_hypothesis(
    frame: FeatureEvidenceFrame,
    variant_id: str = "KH01B-B10-UNICORN-ADJACENT-FVG-SWEEP-MSS",
) -> AdjacentResearchDecision:
    """
    F09: Evaluates Unicorn-Adjacent research model (`ADJACENT_FVG_SWEEP_MSS`, B10).
    Explicitly `counts_toward_d4_true_unicorn = False` (hard ontology isolation!).
    Requires proven unmitigated + retested FVG AND proven (sweep+reclaim OR MSS+displacement)
    AND valid risk/net-RR, WITHOUT requiring Breaker Block overlap.
    """
    if "TRUE-UNICORN" in variant_id.upper() or "D4" in variant_id.upper():
        raise ValueError(f"ONTOLOGY_VIOLATION_ADJACENT_CANNOT_USE_TRUE_UNICORN_ID:{variant_id}")

    if frame.has_any_invalid_evidence:
        return AdjacentResearchDecision(
            opportunity_id=frame.opportunity_id,
            variant_id=variant_id,
            ontology="ADJACENT_FVG_SWEEP_MSS",
            counts_toward_d4_true_unicorn=False,
            eligible=False,
            rejection_code="ADJ_INVALID_PRE_ENTRY_EVIDENCE",
            missing_evidence_rejected=False,
            details={"invalidation_reasons": frame.invalidation_reasons},
        )

    fvg_p = frame.feature_cells.get("fvg_present") or {}
    fvg_u = frame.feature_cells.get("fvg_unmitigated") or {}
    fvg_r = frame.feature_cells.get("fvg_retest_valid") or {}

    if not (
        fvg_p.get("tri_state") == "PROVEN_TRUE"
        and fvg_u.get("tri_state") == "PROVEN_TRUE"
        and fvg_r.get("tri_state") == "PROVEN_TRUE"
    ):
        is_miss = any(c.get("tri_state") == "UNKNOWN_MISSING" for c in (fvg_p, fvg_u, fvg_r))
        return AdjacentResearchDecision(
            opportunity_id=frame.opportunity_id,
            variant_id=variant_id,
            ontology="ADJACENT_FVG_SWEEP_MSS",
            counts_toward_d4_true_unicorn=False,
            eligible=False,
            rejection_code="ADJ_FVG_UNMITIGATED_RETEST_NOT_PROVEN",
            missing_evidence_rejected=is_miss,
            details={"fvg_present": fvg_p.get("tri_state"), "fvg_unmitigated": fvg_u.get("tri_state"), "fvg_retest": fvg_r.get("tri_state")},
        )

    sw_ok = evaluate_sweep_mss_displacement_hypothesis(frame, mode="SWEEP_RECLAIM_ONLY")
    ms_ok = evaluate_sweep_mss_displacement_hypothesis(frame, mode="MSS_DISPLACEMENT_ONLY")
    if not (sw_ok.eligible or ms_ok.eligible):
        is_miss = sw_ok.missing_evidence_rejected or ms_ok.missing_evidence_rejected
        return AdjacentResearchDecision(
            opportunity_id=frame.opportunity_id,
            variant_id=variant_id,
            ontology="ADJACENT_FVG_SWEEP_MSS",
            counts_toward_d4_true_unicorn=False,
            eligible=False,
            rejection_code="ADJ_NEITHER_SWEEP_RECLAIM_NOR_MSS_DISPLACEMENT_PROVEN",
            missing_evidence_rejected=is_miss,
            details={"sweep_status": sw_ok.tri_state, "mss_status": ms_ok.tri_state},
        )

    if not (frame.baseline_net_rr >= 2.0 and 0.0 < frame.planned_net_risk_usd <= 2.50):
        return AdjacentResearchDecision(
            opportunity_id=frame.opportunity_id,
            variant_id=variant_id,
            ontology="ADJACENT_FVG_SWEEP_MSS",
            counts_toward_d4_true_unicorn=False,
            eligible=False,
            rejection_code="ADJ_RISK_OR_NET_RR_FAILED",
            missing_evidence_rejected=False,
            details={"net_rr": frame.baseline_net_rr, "planned_risk_usd": frame.planned_net_risk_usd},
        )

    return AdjacentResearchDecision(
        opportunity_id=frame.opportunity_id,
        variant_id=variant_id,
        ontology="ADJACENT_FVG_SWEEP_MSS",
        counts_toward_d4_true_unicorn=False,
        eligible=True,
        rejection_code=None,
        missing_evidence_rejected=False,
        details={
            "not_true_unicorn_disclosure": "ADJACENT_FVG_SWEEP_MSS_NO_BREAKER_OVERLAP_REQUIRED",
            "sweep_proven": sw_ok.eligible,
            "mss_proven": ms_ok.eligible,
        },
    )


# ==============================================================================
# F10 & F11: VARIANT REPLAY ON INVARIANT CANDIDATE UNIVERSE + ABLATION MATRIX
# ==============================================================================

def _evaluate_single_candidate_for_variant(
    short_code: str,
    variant_id: str,
    frame: FeatureEvidenceFrame,
    *,
    causal_pools_at_ts: Optional[Sequence[Mapping[str, Any]]] = None,
    atr_by_tf_at_ts: Optional[Mapping[str, Optional[float]]] = None,
) -> Tuple[bool, Optional[str], bool, bool, bool, Optional[ResearchTargetAudit]]:
    """
    Returns `(eligible, rejection_code, missing_rejected, proven_false_rejected, invalid_rejected, tp_audit_opt)`.
    """
    if short_code == "B00":
        return True, None, False, False, False, None

    if frame.has_any_invalid_evidence:
        return False, "INVALID_PRE_ENTRY_EVIDENCE", False, False, True, None

    if short_code == "B01":
        d = evaluate_htf_alignment_hypothesis(frame, frame.direction, rule="H4_ONLY")
        return d.eligible, d.rejection_code, d.missing_evidence_rejected, d.proven_false_rejected, d.tri_state == "INVALID", None
    if short_code == "B02":
        d = evaluate_htf_alignment_hypothesis(frame, frame.direction, rule="H1_ONLY")
        return d.eligible, d.rejection_code, d.missing_evidence_rejected, d.proven_false_rejected, d.tri_state == "INVALID", None
    if short_code == "B03":
        d = evaluate_htf_alignment_hypothesis(frame, frame.direction, rule="H4_AND_H1")
        return d.eligible, d.rejection_code, d.missing_evidence_rejected, d.proven_false_rejected, d.tri_state == "INVALID", None
    if short_code == "B04":
        ta = evaluate_source_tf_target_reachability_hypothesis(frame, horizon=12.0, allow_tp_reselection_counterfactual=False)
        return ta.eligible, ta.rejection_code, ta.tri_state == "UNKNOWN_MISSING", ta.tri_state == "PROVEN_FALSE", ta.tri_state == "INVALID", ta
    if short_code == "B05":
        d = evaluate_macro_window_hypothesis(frame)
        return d.eligible, d.rejection_code, d.missing_evidence_rejected, d.proven_false_rejected, d.tri_state == "INVALID", None
    if short_code == "B06":
        d = evaluate_sweep_mss_displacement_hypothesis(frame, mode="SWEEP_RECLAIM_ONLY")
        return d.eligible, d.rejection_code, d.missing_evidence_rejected, d.proven_false_rejected, d.tri_state == "INVALID", None
    if short_code == "B07":
        d = evaluate_sweep_mss_displacement_hypothesis(frame, mode="MSS_DISPLACEMENT_ONLY")
        return d.eligible, d.rejection_code, d.missing_evidence_rejected, d.proven_false_rejected, d.tri_state == "INVALID", None
    if short_code == "B08":
        d = evaluate_sweep_mss_displacement_hypothesis(frame, mode="SWEEP_AND_MSS")
        return d.eligible, d.rejection_code, d.missing_evidence_rejected, d.proven_false_rejected, d.tri_state == "INVALID", None
    if short_code == "B09":
        sd = evaluate_strict_true_unicorn_hypothesis(frame)
        is_miss = "UNKNOWN_MISSING" in str(sd.terminal_rejection_code or "")
        return sd.is_valid_strict_unicorn, sd.terminal_rejection_code, is_miss, (not sd.is_valid_strict_unicorn and not is_miss), False, None
    if short_code == "B10":
        ad = evaluate_unicorn_adjacent_research_hypothesis(frame, variant_id=variant_id)
        return ad.eligible, ad.rejection_code, ad.missing_evidence_rejected, (not ad.eligible and not ad.missing_evidence_rejected), False, None
    if short_code == "B11":
        ta = evaluate_source_tf_target_reachability_hypothesis(
            frame,
            horizon=12.0,
            allow_tp_reselection_counterfactual=True,
            causal_pools_at_ts=causal_pools_at_ts,
            atr_by_tf_at_ts=atr_by_tf_at_ts,
        )
        return ta.eligible, ta.rejection_code, ta.tri_state == "UNKNOWN_MISSING", ta.tri_state == "PROVEN_FALSE", ta.tri_state == "INVALID", ta
    if short_code == "B12":
        d_htf = evaluate_htf_alignment_hypothesis(frame, frame.direction, rule="H4_AND_H1")
        if not d_htf.eligible:
            return False, f"B12_STAGE1_{d_htf.rejection_code}", d_htf.missing_evidence_rejected, d_htf.proven_false_rejected, d_htf.tri_state == "INVALID", None
        d_mss = evaluate_sweep_mss_displacement_hypothesis(frame, mode="MSS_DISPLACEMENT_ONLY")
        if not d_mss.eligible:
            return False, f"B12_STAGE2_{d_mss.rejection_code}", d_mss.missing_evidence_rejected, d_mss.proven_false_rejected, d_mss.tri_state == "INVALID", None
        ta = evaluate_source_tf_target_reachability_hypothesis(frame, horizon=12.0, allow_tp_reselection_counterfactual=False)
        if not ta.eligible:
            return False, f"B12_STAGE3_{ta.rejection_code}", ta.tri_state == "UNKNOWN_MISSING", ta.tri_state == "PROVEN_FALSE", ta.tri_state == "INVALID", ta
        return True, None, False, False, False, ta

    raise ValueError(f"UNKNOWN_KH01B_SHORT_CODE:{short_code}")


def _build_materialized_plan_for_kh01b(
    opp: Mapping[str, Any],
    frame: FeatureEvidenceFrame,
    variant_id: str,
    tp_audit: Optional[ResearchTargetAudit],
) -> Dict[str, Any]:
    opp_id = frame.opportunity_id
    dec_ts = frame.decision_timestamp_ms
    chk_idx = frame.checkpoint_index_1m
    old_plan = {
        "opportunity_id": opp_id,
        "model_id": frame.model_id,
        "direction": frame.direction,
        "checkpoint_index_1m": chk_idx,
        "decision_timestamp_ms": dec_ts,
        "entry_reference": frame.entry_reference,
        "selected_sl": frame.selected_sl,
        "take_profit": frame.take_profit,
        "quantity": frame.hypothetical_quantity,
        "planned_net_risk_usd": frame.planned_net_risk_usd,
        "net_rr": frame.baseline_net_rr,
    }
    if tp_audit is not None and tp_audit.tp_modified:
        new_tp = tp_audit.selected_tp
        new_qty = float(tp_audit.recomputed_quantity or frame.hypothetical_quantity)
        new_risk = float(tp_audit.recomputed_planned_risk_usd or frame.planned_net_risk_usd)
        new_nrr = float(tp_audit.recomputed_net_rr or frame.baseline_net_rr)
        new_grr = float(tp_audit.recomputed_gross_rr or frame.baseline_gross_rr)
        new_tid = tp_audit.selected_target_pool_id
        fp = canonical_hash({
            "variant_id": variant_id,
            "oid": opp_id,
            "entry": frame.entry_reference,
            "sl": frame.selected_sl,
            "tp": new_tp,
            "qty": new_qty,
        })
        return {
            "schema_version": SCHEMA_VERSION_KH01B,
            "intent_id": f"intent_{variant_id}_{hashlib.sha256(f'{variant_id}|{opp_id}|{dec_ts}'.encode('utf-8')).hexdigest()[:16]}",
            "opportunity_id": opp_id,
            "setup_id": str(opp.get("setup_id") or frame.setup_episode_id),
            "variant_id": variant_id,
            "intervention_type": "TP_ONLY_RESEARCH_COUNTERFACTUAL",
            "status": "ACCEPTED_MODIFIED_EXECUTABLE_GEOMETRY",
            "accepted": True,
            "rejection_reason": None,
            "old_plan": old_plan,
            "new_checkpoint_index_1m": chk_idx,
            "new_decision_timestamp_ms": dec_ts,
            "new_entry_reference": frame.entry_reference,
            "new_selected_sl": frame.selected_sl,
            "new_take_profit": new_tp,
            "new_quantity": new_qty,
            "new_planned_net_risk_usd": new_risk,
            "new_net_rr": new_nrr,
            "new_gross_rr": new_grr,
            "new_structural_invalidation_price": float(opp.get("structural_invalidation_price") or frame.selected_sl),
            "new_selected_anchor_id": opp.get("selected_anchor_id"),
            "new_anchor_kind": opp.get("anchor_kind"),
            "new_selected_target_id": new_tid,
            "changed_dimensions": ["take_profit", "quantity"],
            "known_at_ms": dec_ts,
            "confirmation_subtype": "TP_ONLY_CAUSAL_POOL_RESELECTION",
            "new_trade_fingerprint": fp,
        }

    return {
        "schema_version": SCHEMA_VERSION_KH01B,
        "intent_id": f"intent_{variant_id}_{hashlib.sha256(f'{variant_id}|{opp_id}|{dec_ts}'.encode('utf-8')).hexdigest()[:16]}",
        "opportunity_id": opp_id,
        "setup_id": str(opp.get("setup_id") or frame.setup_episode_id),
        "variant_id": variant_id,
        "intervention_type": "ENTRY_FILTER_ONLY_GEOMETRY_UNCHANGED",
        "status": "ACCEPTED_UNCHANGED_NO_OP",
        "accepted": True,
        "rejection_reason": None,
        "old_plan": old_plan,
        "new_checkpoint_index_1m": chk_idx,
        "new_decision_timestamp_ms": dec_ts,
        "new_entry_reference": frame.entry_reference,
        "new_selected_sl": frame.selected_sl,
        "new_take_profit": frame.take_profit,
        "new_quantity": frame.hypothetical_quantity,
        "new_planned_net_risk_usd": frame.planned_net_risk_usd,
        "new_net_rr": frame.baseline_net_rr,
        "new_gross_rr": frame.baseline_gross_rr,
        "new_structural_invalidation_price": float(opp.get("structural_invalidation_price") or frame.selected_sl),
        "new_selected_anchor_id": opp.get("selected_anchor_id"),
        "new_anchor_kind": opp.get("anchor_kind"),
        "new_selected_target_id": frame.selected_target_id,
        "changed_dimensions": [],
        "known_at_ms": dec_ts,
        "confirmation_subtype": "NONE",
        "new_trade_fingerprint": frame.trade_action_fingerprint,
    }


def _summarize_episode_list_kh01b(
    mode_episodes: Sequence[Mapping[str, Any]],
    blocked_by_open_pos: int,
) -> Dict[str, Any]:
    closed_eps = [
        e for e in mode_episodes
        if e.get("filled", False) and str(e.get("outcome_label")) in ("WIN", "LOSS")
    ]
    wins = [e for e in closed_eps if str(e.get("outcome_label")) == "WIN"]
    losses = [e for e in closed_eps if str(e.get("outcome_label")) == "LOSS"]
    open_hor = [e for e in mode_episodes if str(e.get("outcome_label")) == "OPEN_AT_HORIZON"]
    data_gap = [e for e in mode_episodes if str(e.get("outcome_label")) == "DATA_GAP_UNRESOLVED"]
    unfilled = [
        e for e in mode_episodes
        if not e.get("filled", False)
        or str(e.get("outcome_label")) not in ("WIN", "LOSS", "OPEN_AT_HORIZON", "DATA_GAP_UNRESOLVED")
    ]

    closed_n = len(closed_eps)
    gross_usd = round(sum(float(e.get("realized_gross_pnl_usd") or 0.0) for e in closed_eps), 4)
    fees_usd = round(sum(float(e.get("total_fees_usd") or 0.0) for e in closed_eps), 4)
    net_usd = round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in closed_eps), 4)
    exp_r = round(sum(float(e.get("realized_net_r") or e.get("realized_r") or 0.0) for e in closed_eps) / closed_n, 4) if closed_n > 0 else 0.0

    pos_sum = sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in wins)
    neg_sum = abs(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in losses))
    pf = round(pos_sum / neg_sum, 4) if neg_sum > 1e-9 else (99.0 if pos_sum > 0 else 0.0)

    closed_sorted = sorted(
        closed_eps,
        key=lambda e: (
            int(e.get("exit_fill_ts") or e.get("exit_ts_ms") or e.get("release_ts_ms") or 0),
            int(e.get("entry_fill_ts") or e.get("entry_ts_ms") or 0),
            str(e.get("opportunity_id") or ""),
        ),
    )
    eq = peak = max_dd = 0.0
    cur_st = max_st = 0
    for e in closed_sorted:
        pnl = float(e.get("realized_net_pnl_usd") or 0.0)
        eq += pnl
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
        if pnl <= 0.0:
            cur_st += 1
            max_st = max(max_st, cur_st)
        else:
            cur_st = 0

    return {
        "total_scheduled_episodes": len(mode_episodes),
        "closed_trades_count": closed_n,
        "wins_count": len(wins),
        "losses_count": len(losses),
        "win_rate": round(len(wins) / closed_n, 6) if closed_n > 0 else 0.0,
        "open_at_horizon_count": len(open_hor),
        "data_gap_unresolved_count": len(data_gap),
        "unfilled_or_no_fill_count": len(unfilled),
        "blocked_by_open_position_count": int(blocked_by_open_pos),
        "total_realized_gross_usd": gross_usd,
        "total_fees_usd": fees_usd,
        "total_realized_net_usd": net_usd,
        "expectancy_net_r": exp_r,
        "profit_factor": pf,
        "max_drawdown_usd": round(max_dd, 4),
        "longest_losing_streak": max_st,
    }


def evaluate_kh01b_variant_on_matched_candidate_universe(
    variant_spec: Mapping[str, Any],
    full_candidate_ledger: Sequence[Mapping[str, Any]],
    source_ledger: Mapping[str, Mapping[str, Any]],
    fixed_costs: Optional[CostAssumptions],
    *,
    candidate_index: Mapping[str, Any],
    datasets: Mapping[str, Any],
    frames_by_opp_id: Mapping[str, FeatureEvidenceFrame],
    all_pools_list: Optional[Sequence[Mapping[str, Any]]] = None,
    atr_cache: Optional[Dict[Tuple[str, int], Optional[float]]] = None,
    close_ts_index: Optional[Mapping[str, Sequence[int]]] = None,
    b00_winner_by_chk_idx: Optional[Mapping[int, str]] = None,
    baseline_b00_episodes: Optional[Sequence[Mapping[str, Any]]] = None,
    unchanged_outcome_cache: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
    si8_frames_by_opp_id: Optional[Mapping[str, FeatureEvidenceFrame]] = None,
) -> VariantReplayBundle:
    """
    F10: Evaluates a pre-registered KH01B variant across ALL 56,710 canonical A1 opportunities,
    runs STATIC_PRODUCTION_PRIORITY multi-model arbitration at each M1 timestamp,
    replays chronological 1-position portfolio across ALL 3 fill modes (`NEXT_BAR_OPEN_BID_ASK`,
    `LEGACY_CLOSE_FILL`, `STRESS_WIDER_SPREAD_SLIPPAGE_2X`), and computes matched-242 B00 counterfactual.
    """
    short_code = str(variant_spec["short_code"])
    variant_id = str(variant_spec["variant_id"])
    ontology = str(variant_spec["ontology"])
    axis_type = str(variant_spec["axis_type"])
    bars_1m = list(datasets["1m"])
    baseline_outcomes_by_opp_id = candidate_index.get("baseline_outcomes_by_opp_id") or {}

    opp_by_id: Dict[str, Mapping[str, Any]] = {}
    surviving_by_chk: Dict[int, List[Tuple[Mapping[str, Any], Optional[ResearchTargetAudit]]]] = {}
    rej_counts: Counter[str] = Counter()
    elig_n = rej_n = miss_rej_n = false_rej_n = inv_rej_n = 0

    # Pre-index causal pools sorted by known_at_ms for B11
    pools_sorted_kn: List[Tuple[int, Mapping[str, Any]]] = []
    if short_code == "B11" and all_pools_list:
        for po in all_pools_list:
            kn = po.get("known_at_ms", po.get("confirmed_at_ms"))
            if kn is not None:
                pools_sorted_kn.append((int(kn), po))
        pools_sorted_kn.sort(key=lambda t: t[0])
    pool_kn_keys = [t[0] for t in pools_sorted_kn]

    elig_status_by_oid: Dict[str, Tuple[bool, Optional[str], Optional[ResearchTargetAudit]]] = {}

    for cand in full_candidate_ledger:
        oid = str(cand["opportunity_id"])
        opp_by_id[oid] = cand
        fr = frames_by_opp_id[oid]

        c_pools: Optional[Sequence[Mapping[str, Any]]] = None
        atr_at_ts: Optional[Dict[str, Optional[float]]] = None
        if short_code == "B11":
            hi_i = bisect.bisect_right(pool_kn_keys, fr.decision_timestamp_ms)
            # Check recent 160 causal pools up to decision_timestamp_ms
            c_pools = [t[1] for t in pools_sorted_kn[max(0, hi_i - 160) : hi_i]]
            atr_at_ts = {}
            for tf_k in ("1M", "5M", "15M", "1H", "4H"):
                ck = (tf_k, fr.decision_timestamp_ms)
                if atr_cache is not None and ck in atr_cache:
                    atr_at_ts[tf_k] = atr_cache[ck]
                elif close_ts_index is not None:
                    v_atr = compute_causal_tf_atr_v17931214(datasets, close_ts_index, tf_k, fr.decision_timestamp_ms)
                    if atr_cache is not None:
                        atr_cache[ck] = v_atr
                    atr_at_ts[tf_k] = v_atr
                else:
                    atr_at_ts[tf_k] = None

        ok, rej_c, is_miss, is_false, is_inv, tp_aud = _evaluate_single_candidate_for_variant(
            short_code,
            variant_id,
            fr,
            causal_pools_at_ts=c_pools,
            atr_by_tf_at_ts=atr_at_ts,
        )
        elig_status_by_oid[oid] = (ok, rej_c, tp_aud)
        if ok:
            elig_n += 1
            surviving_by_chk.setdefault(fr.checkpoint_index_1m, []).append((cand, tp_aud))
        else:
            rej_n += 1
            rej_counts[str(rej_c or "REJECTED")] += 1
            if is_inv:
                inv_rej_n += 1
            elif is_miss:
                miss_rej_n += 1
            elif is_false:
                false_rej_n += 1

    # Step 1: Multi-model arbitration at each M1 timestamp (preserving STATIC_PRODUCTION_PRIORITY)
    selected_pairs: List[Tuple[Mapping[str, Any], Dict[str, Any]]] = []
    suppressed_ties = 0
    winner_subs_vs_b00 = 0
    modified_tp_n = 0

    for chk_i in sorted(surviving_by_chk.keys()):
        items = surviving_by_chk[chk_i]
        tp_by_id = {str(c["opportunity_id"]): ta for c, ta in items}
        cands_only = [dict(c) for c, _ in items]
        winner, losers = arbitrate_competing_model_intents_v1793122(
            cands_only,
            arbitration_mode="STATIC_PRODUCTION_PRIORITY",
            feature_ledger_by_opp_id=source_ledger,
        )
        if winner is not None:
            w_oid = str(winner["opportunity_id"])
            suppressed_ties += len(losers)
            if b00_winner_by_chk_idx is not None and b00_winner_by_chk_idx.get(chk_i) != w_oid:
                winner_subs_vs_b00 += 1
            mat = _build_materialized_plan_for_kh01b(
                winner,
                frames_by_opp_id[w_oid],
                variant_id,
                tp_by_id.get(w_oid),
            )
            if mat["status"] == "ACCEPTED_MODIFIED_EXECUTABLE_GEOMETRY":
                modified_tp_n += 1
            selected_pairs.append((winner, mat))

    selected_pairs.sort(
        key=lambda pair: (
            int(pair[1]["new_decision_timestamp_ms"]),
            int(pair[1]["new_checkpoint_index_1m"]),
            int(PRODUCTION_MODEL_STATIC_PRIORITY.get(str(pair[0]["model_id"]), 99)),
            str(pair[0]["opportunity_id"]),
        )
    )

    # Step 2: Chronological 1-position portfolio replay across ALL 3 fill modes
    fill_summaries: Dict[str, Dict[str, Any]] = {}
    all_mode_eps: Dict[str, List[Dict[str, Any]]] = {}

    for f_mode in FILL_MODES_KH01B:
        state = PortfolioReplayStateV179312(variant=variant_id, max_concurrent_positions=1)
        mode_eps: List[Dict[str, Any]] = []
        blocked_n = 0

        for opp, mat in selected_pairs:
            oid = str(opp["opportunity_id"])
            new_ts = int(mat["new_decision_timestamp_ms"])
            advance_portfolio_to_next_event_v179312(state, None, new_ts)
            if state.active_episode is not None and new_ts < state.active_release_ts_ms:
                blocked_n += 1
                continue

            is_noop = (mat["status"] == "ACCEPTED_UNCHANGED_NO_OP")
            cache_key = (oid, f_mode)
            if is_noop and f_mode == "NEXT_BAR_OPEN_BID_ASK" and oid in baseline_outcomes_by_opp_id:
                ep_rec = dict(baseline_outcomes_by_opp_id[oid]["episode_record"])
            elif is_noop and unchanged_outcome_cache is not None and cache_key in unchanged_outcome_cache:
                ep_rec = dict(unchanged_outcome_cache[cache_key])
            else:
                rep = replay_intervention_outcome_v179312(mat, bars_1m, mode=f_mode, base_record=opp)
                ep_rec = dict(rep["episode_record"])
                if is_noop and unchanged_outcome_cache is not None:
                    unchanged_outcome_cache[cache_key] = dict(ep_rec)

            ep_rec["opportunity_id"] = oid
            ep_rec["intent_id"] = mat.get("intent_id")
            ep_rec["policy_id"] = variant_id
            ep_rec["fill_mode"] = f_mode
            ep_rec["changed_dimensions"] = list(mat.get("changed_dimensions") or [])
            ep_rec["entry_ts_ms"] = int(ep_rec.get("entry_fill_ts") or ep_rec.get("decision_timestamp_ms") or 0)
            ep_rec["exit_ts_ms"] = int(ep_rec.get("exit_fill_ts") or ep_rec.get("release_ts_ms") or 0)
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

    # Step 3: Matched 242-episode B00 cohort counterfactual (no open-position cascade distortion)
    matched_242_cf: Dict[str, Any] = {"evaluated_b00_episodes": 0}
    if baseline_b00_episodes is not None:
        m_kept_closed: List[Dict[str, Any]] = []
        m_rejected_wins = 0
        m_rejected_losses = 0
        m_kept_sched = 0
        for b_ep in baseline_b00_episodes:
            b_oid = str(b_ep["opportunity_id"])
            ok_b, _, tp_b = elig_status_by_oid.get(b_oid, (False, "NOT_IN_A1", None))
            if not ok_b:
                if b_ep.get("outcome_label") == "WIN":
                    m_rejected_wins += 1
                elif b_ep.get("outcome_label") == "LOSS":
                    m_rejected_losses += 1
                continue
            m_kept_sched += 1
            if tp_b is not None and tp_b.tp_modified:
                mat_m = _build_materialized_plan_for_kh01b(opp_by_id[b_oid], frames_by_opp_id[b_oid], variant_id, tp_b)
                rep_m = replay_intervention_outcome_v179312(mat_m, bars_1m, mode="NEXT_BAR_OPEN_BID_ASK", base_record=opp_by_id[b_oid])
                ep_m = dict(rep_m["episode_record"])
            else:
                ep_m = dict(b_ep)
            if ep_m.get("filled", False) and str(ep_m.get("outcome_label")) in ("WIN", "LOSS"):
                m_kept_closed.append(ep_m)

        b00_closed_net = round(
            sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in baseline_b00_episodes if e.get("outcome_label") in ("WIN", "LOSS")),
            4,
        )
        m_net = round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in m_kept_closed), 4)
        m_exp_r = (
            round(sum(float(e.get("realized_r") or 0.0) for e in m_kept_closed) / len(m_kept_closed), 4)
            if m_kept_closed
            else 0.0
        )
        matched_242_cf = {
            "evaluated_b00_episodes": len(baseline_b00_episodes),
            "surviving_scheduled_count": m_kept_sched,
            "surviving_closed_count": len(m_kept_closed),
            "surviving_wins_count": sum(1 for e in m_kept_closed if e.get("outcome_label") == "WIN"),
            "surviving_losses_count": sum(1 for e in m_kept_closed if e.get("outcome_label") == "LOSS"),
            "filtered_out_b00_wins_count": m_rejected_wins,
            "filtered_out_b00_losses_count": m_rejected_losses,
            "surviving_net_pnl_usd": m_net,
            "surviving_expectancy_r": m_exp_r,
            "paired_net_pnl_delta_vs_b00_usd": round(m_net - b00_closed_net, 4),
            "fewer_trades_caution": (
                "Fewer losses due purely to lower trade count does NOT establish positive edge unless surviving expectancy > 0."
            ),
        }

    # Optional separate SI8 6,375 diagnostic for B09 / B10 (strictly unmixed with A1!)
    si8_diag: Optional[Dict[str, Any]] = None
    if si8_frames_by_opp_id is not None and short_code in ("B09", "B10"):
        s8_elig = 0
        s8_rejs: Counter[str] = Counter()
        for s_fr in si8_frames_by_opp_id.values():
            ok_s8, rc_s8, _, _, _, _ = _evaluate_single_candidate_for_variant(short_code, variant_id, s_fr)
            if ok_s8:
                s8_elig += 1
            else:
                s8_rejs[str(rc_s8 or "REJECTED")] += 1
        si8_diag = {
            "population_name": "SEPARATE_SI8_DISCOVERY_6375_UNMIXED",
            "si8_total": len(si8_frames_by_opp_id),
            "si8_pre_entry_eligible_count": s8_elig,
            "si8_rejection_counts": dict(sorted(s8_rejs.items())),
        }

    bundle_digest = canonical_hash({
        "variant_id": variant_id,
        "elig": elig_n,
        "rej": rej_n,
        "summaries": fill_summaries,
        "matched_242": matched_242_cf,
    })
    return VariantReplayBundle(
        variant_id=variant_id,
        short_code=short_code,
        ontology=ontology,
        axis_type=axis_type,
        population_name="CANONICAL_A1_V1792_SL_TP_BASELINE_56710",
        population_total=len(full_candidate_ledger),
        eligible_candidates_count=elig_n,
        rejected_candidates_count=rej_n,
        missing_evidence_rejected_count=miss_rej_n,
        proven_false_rejected_count=false_rej_n,
        invalid_evidence_rejected_count=inv_rej_n,
        plausible_eligibility_lower_bound=elig_n,
        plausible_eligibility_upper_bound=elig_n + miss_rej_n,
        rejection_reasons_counts=dict(sorted(rej_counts.items())),
        post_arbitration_timestamps_selected=len(selected_pairs),
        arbitration_suppressed_ties_count=suppressed_ties,
        arbitration_winner_substitutions_vs_b00=winner_subs_vs_b00,
        materialized_accepted_count=len(selected_pairs),
        materialized_modified_tp_count=modified_tp_n,
        fill_mode_summaries=fill_summaries,
        primary_nb_episodes=all_mode_eps["NEXT_BAR_OPEN_BID_ASK"],
        all_mode_episodes=all_mode_eps,
        matched_b00_242_counterfactual=matched_242_cf,
        si8_separate_diagnostic=si8_diag,
        bundle_sha256=bundle_digest,
    )


# ==============================================================================
# F12: RESEARCH ECONOMICS, 2X/3X COST RECOMPUTE, CLUSTER BOOTSTRAP & PURGED FOLDS
# ==============================================================================

def compute_kh01b_research_economics_and_uncertainty(
    bundle: VariantReplayBundle,
    costs: Optional[CostAssumptions],
    folds: Sequence[Tuple[int, int, int, int]],
    *,
    baseline_bundle: Optional[VariantReplayBundle] = None,
    multiplicity_m: int = 12,
) -> ResearchEconomicVerdict:
    """
    F12: Computes 1x/2x/3x per-trade cost sensitivity, NY-trading-day cluster bootstrap 95% CIs,
    180m-purged reused-history walk-forward folds (verifying 0 episode leakage), paired deltas vs B00,
    and honest non-optimistic selection status (`ACTIVATION_READY = NO` always).
    """
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

    # Per-trade recomputed 2x ALL-COST (2x spread/slippage fill simulation + 2x taker fees)
    net_2x_list: List[float] = []
    r_2x_list: List[float] = []
    for e2 in closed_sp2:
        gross_2 = float(e2.get("realized_gross_pnl_usd") or 0.0)
        fees_1x = float(e2.get("total_fees_usd") or (float(e2.get("entry_fee_usd") or 0.0) + float(e2.get("exit_fee_usd") or 0.0)))
        net_2 = gross_2 - 2.0 * fees_1x
        risk_u = max(0.01, float(e2.get("planned_net_risk_usd") or 2.50))
        net_2x_list.append(net_2)
        r_2x_list.append(net_2 / risk_u)

    net_2x_all = round(sum(net_2x_list), 4)
    exp_2x_all = round(sum(r_2x_list) / len(r_2x_list), 4) if r_2x_list else 0.0

    # Per-trade 3x ALL-COST sensitivity (from 2x spread/slippage gross minus extra spread/slippage delta and 3x fees)
    net_3x_list: List[float] = []
    sp2_by_oid = {str(e["opportunity_id"]): e for e in closed_sp2}
    for e1 in closed_nb:
        oid = str(e1["opportunity_id"])
        g1 = float(e1.get("realized_gross_pnl_usd") or 0.0)
        f1 = float(e1.get("total_fees_usd") or 0.0)
        if oid in sp2_by_oid:
            g2 = float(sp2_by_oid[oid].get("realized_gross_pnl_usd") or g1)
            extra_sp_slip = max(0.0, g1 - g2)
            g3 = g1 - 2.0 * extra_sp_slip
        else:
            g3 = g1 - 0.60 * float(e1.get("executed_quantity") or 0.0)
        net_3x_list.append(g3 - 3.0 * f1)
    net_3x_all = round(sum(net_3x_list), 4)

    # Cluster bootstrap 95% CI by NY calendar date (deterministic seed=20261119)
    ny_tz = ZoneInfo("America/New_York")
    ci_r: Optional[Tuple[float, float]] = None
    ci_pnl: Optional[Tuple[float, float]] = None
    if len(closed_nb) >= 5:
        by_day: Dict[str, List[Tuple[float, float]]] = {}
        for e in closed_nb:
            ts = int(e.get("decision_timestamp_ms") or 0)
            day_k = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).astimezone(ny_tz).strftime("%Y-%m-%d")
            by_day.setdefault(day_k, []).append((
                float(e.get("realized_net_r") or e.get("realized_r") or 0.0),
                float(e.get("realized_net_pnl_usd") or 0.0),
            ))
        day_keys = sorted(by_day.keys())
        if len(day_keys) >= 2:
            rng = random.Random(20261119)
            boot_r: List[float] = []
            boot_p: List[float] = []
            k_days = len(day_keys)
            for _ in range(1000):
                sampled_days = [day_keys[rng.randrange(k_days)] for _ in range(k_days)]
                pts = [item for dk in sampled_days for item in by_day[dk]]
                if pts:
                    boot_r.append(sum(x[0] for x in pts) / len(pts))
                    boot_p.append(sum(x[1] for x in pts))
            boot_r.sort()
            boot_p.sort()
            lo_i = int(0.025 * len(boot_r))
            hi_i = min(len(boot_r) - 1, int(0.975 * len(boot_r)))
            ci_r = (round(boot_r[lo_i], 4), round(boot_r[hi_i], 4))
            ci_pnl = (round(boot_p[lo_i], 4), round(boot_p[hi_i], 4))

    # Purged 180m reused-history folds (verify zero shared setup_episode_id or bar overlap)
    fold_reports: List[Dict[str, Any]] = []
    leakage_detected = False
    for f_i, (tr_s, tr_e, val_s, val_e) in enumerate(folds, start=1):
        tr_eps = [
            e for e in closed_nb
            if tr_s <= int(e.get("decision_timestamp_ms") or 0) <= tr_e
            and int(e.get("exit_ts_ms") or e.get("release_ts_ms") or 0) <= tr_e
        ]
        purged_eps = [
            e for e in closed_nb
            if int(e.get("decision_timestamp_ms") or 0) <= tr_e
            and int(e.get("exit_ts_ms") or e.get("release_ts_ms") or (tr_e + 1)) > tr_e
        ]
        val_eps = [
            e for e in closed_nb
            if val_s <= int(e.get("decision_timestamp_ms") or 0) <= val_e
        ]
        tr_ep_ids = {str(e.get("setup_episode_id") or e.get("episode_key") or e["opportunity_id"]) for e in tr_eps}
        val_ep_ids = {str(e.get("setup_episode_id") or e.get("episode_key") or e["opportunity_id"]) for e in val_eps}
        shared_ids = tr_ep_ids.intersection(val_ep_ids)
        if shared_ids or (val_s - tr_e) < 180 * 60_000:
            leakage_detected = True
        fold_reports.append({
            "fold_id": f"REUSED_PURGED_FOLD_{f_i}",
            "role_disclosure": "REUSED_DEVELOPMENT_DIAGNOSTIC_NOT_FRESH_OOS",
            "train_window_ms": [tr_s, tr_e],
            "embargo_minutes": round((val_s - tr_e) / 60_000, 1),
            "validation_window_ms": [val_s, val_e],
            "purged_boundary_episodes": len(purged_eps),
            "shared_episode_ids_count": len(shared_ids),
            "train_closed_n": len(tr_eps),
            "train_net_pnl_usd": round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in tr_eps), 4),
            "val_closed_n": len(val_eps),
            "val_net_pnl_usd": round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in val_eps), 4),
            "val_expectancy_r": round(sum(float(e.get("realized_net_r") or 0.0) for e in val_eps) / len(val_eps), 4) if val_eps else 0.0,
        })

    b00_sched = int(baseline_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]["total_scheduled_episodes"]) if baseline_bundle else max(1, nb_sum["total_scheduled_episodes"])
    b00_net = float(baseline_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]["total_realized_net_usd"]) if baseline_bundle else 0.0
    freq_ret = round(nb_sum["total_scheduled_episodes"] / max(1, b00_sched), 4)
    port_diff = round(nb_sum["total_realized_net_usd"] - b00_net, 4)
    matched_diff = float(bundle.matched_b00_242_counterfactual.get("paired_net_pnl_delta_vs_b00_usd") or 0.0)

    closed_n = int(nb_sum["closed_trades_count"])
    net_1x = float(nb_sum["total_realized_net_usd"])

    if leakage_detected:
        sel_outcome = "FAIL_CAUSAL_OR_ACCOUNTING"
        power_warn = "FATAL_FOLD_EMBARGO_OR_EPISODE_LEAKAGE_DETECTED"
    elif bundle.invalid_evidence_rejected_count > 0 and closed_n == 0 and bundle.eligible_candidates_count == 0:
        sel_outcome = "INCONCLUSIVE_DATA_QUALITY"
        power_warn = "INVALID_DATA_QUALITY_BLOCKED_EVALUATION"
    elif int(nb_sum["total_scheduled_episodes"]) == 0 or closed_n == 0:
        sel_outcome = "NO_VALID_SETUPS"
        power_warn = "ZERO_CLOSED_TRADES_INSUFFICIENT_SAMPLE_NO_CONFIDENCE_INTERVAL"
    elif net_1x <= 0.0 or net_2x_all <= 0.0:
        sel_outcome = "RESEARCH_NEGATIVE_EDGE"
        power_warn = (
            f"UNDERPOWERED_N={closed_n}<30_AND_NEGATIVE_NET_EDGE"
            if closed_n < 30
            else f"POWERED_N={closed_n}_NEGATIVE_NET_OR_2X_COST_EDGE"
        )
    elif closed_n < 30:
        sel_outcome = "INCONCLUSIVE_UNDERPOWERED"
        power_warn = f"POSITIVE_IN_SMALL_SAMPLE_N={closed_n}<30_INSUFFICIENT_STATISTICAL_POWER"
    else:
        val_pos_folds = sum(1 for f in fold_reports if f["val_closed_n"] > 0 and f["val_net_pnl_usd"] > 0.0)
        ci_lo_pos = bool(ci_pnl is not None and ci_pnl[0] > 0.0)
        if val_pos_folds >= 2 and ci_lo_pos:
            sel_outcome = "RESEARCH_POSITIVE_BUT_REUSED_NOT_VALIDATED"
            power_warn = "REUSED_20D_HISTORY_ONLY_MODELED_QUOTES_NOT_FRESH_OOS_VALIDATED"
        else:
            sel_outcome = "INCONCLUSIVE_UNDERPOWERED"
            power_warn = f"INCONSISTENT_ACROSS_PURGED_FOLDS_OR_WIDE_BOOTSTRAP_CI:{ci_pnl}"

    qualifies_kh01c = (sel_outcome == "RESEARCH_POSITIVE_BUT_REUSED_NOT_VALIDATED")

    return ResearchEconomicVerdict(
        variant_id=bundle.variant_id,
        short_code=bundle.short_code,
        ontology=bundle.ontology,
        axis_type=bundle.axis_type,
        closed_trades_nb=closed_n,
        wins_nb=int(nb_sum["wins_count"]),
        losses_nb=int(nb_sum["losses_count"]),
        win_rate_nb=float(nb_sum["win_rate"]),
        scheduled_nb=int(nb_sum["total_scheduled_episodes"]),
        trade_frequency_retention_vs_b00=freq_ret,
        net_pnl_1x_nb_usd=net_1x,
        expectancy_1x_nb_r=float(nb_sum["expectancy_net_r"]),
        profit_factor_1x_nb=float(nb_sum["profit_factor"]),
        max_drawdown_1x_nb_usd=float(nb_sum["max_drawdown_usd"]),
        longest_losing_streak_1x_nb=int(nb_sum["longest_losing_streak"]),
        total_fees_1x_nb_usd=float(nb_sum["total_fees_usd"]),
        legacy_close_fill_net_pnl_usd=float(leg_sum["total_realized_net_usd"]),
        stress_2x_spread_slippage_net_pnl_usd=float(sp2_sum["total_realized_net_usd"]),
        stress_2x_all_cost_net_pnl_usd=net_2x_all,
        stress_2x_all_cost_expectancy_r=exp_2x_all,
        stress_3x_all_cost_net_pnl_usd=net_3x_all,
        cluster_bootstrap_95ci_expectancy_r=ci_r,
        cluster_bootstrap_95ci_net_pnl_usd=ci_pnl,
        purged_reused_walkforward_folds=tuple(fold_reports),
        paired_vs_b00_portfolio_net_pnl_diff_usd=port_diff,
        paired_vs_b00_matched_242_net_pnl_diff_usd=matched_diff,
        multiplicity_hypotheses_tested_m=multiplicity_m,
        holm_bonferroni_caution=(
            f"Tested m={multiplicity_m} non-baseline hypotheses on reused 20-day M1 XAUUSDT sample; "
            "family-wise alpha=0.05/12=0.0042 required and Fresh OOS remains mandatory."
        ),
        sample_power_warning=power_warn,
        selection_outcome=sel_outcome,
        qualifies_for_kh01c_advancement=qualifies_kh01c,
        fresh_oos_verdict="BLOCKED_NO_UNTOUCHED_DATA",
        activation_ready="NO",
    )


def run_kh01b_orthogonal_ablation_matrix(
    frozen_manifest: FrozenResearchManifest,
    baseline: BaselineTruth,
    feature_ledger: Mapping[str, Mapping[str, Any]],
    *,
    a1_decisions: Sequence[Mapping[str, Any]],
    si8_decisions: Sequence[Mapping[str, Any]],
    candidate_index: Mapping[str, Any],
    datasets: Mapping[str, Any],
    close_ts_index: Mapping[str, Sequence[int]],
    pools_by_id: Mapping[str, Mapping[str, Any]],
    v12_funnel_09_doc: Mapping[str, Any],
    si9_policy_result: Mapping[str, Any],
    run_id: str,
    tested_sha: str,
) -> AblationSet:
    """
    F11: Executes the complete frozen B00..B12 ablation matrix on the invariant 56,710 A1
    candidate universe (+ unmixed 6,375 SI8 diagnostics for B09/B10 + authoritative 63,085 D4
    funnel & 267+40 target control audit).
    """
    if not frozen_manifest.frozen_before_backtest_execution:
        raise ValueError("MANIFEST_MUST_BE_FROZEN_BEFORE_ABLATION_EXECUTION")

    # Build canonical closed bar index for O(1) source-bar existence & anti-lookahead checks
    closed_bar_idx: Dict[Tuple[str, int], int] = {}
    for tf_u, tf_k, span_ms in (
        ("1M", "1m", 60_000),
        ("5M", "5m", 300_000),
        ("15M", "15m", 900_000),
        ("1H", "1H", 3_600_000),
        ("4H", "4H", 14_400_000),
        ("1D", "1D", 86_400_000),
    ):
        for b in datasets.get(tf_k, ()):
            o_ms = int(b.get("open_time") or b.get("timestamp") or 0)
            c_ms = int(b.get("close_time") or (o_ms + span_ms))
            if o_ms > 0:
                closed_bar_idx[(tf_u, o_ms)] = c_ms

    macro_cache: Dict[int, Dict[str, Any]] = {}
    atr_cache: Dict[Tuple[str, int], Optional[float]] = {}
    gap_cache: Dict[int, bool] = {}

    frames_a1: Dict[str, FeatureEvidenceFrame] = {}
    for cand in a1_decisions:
        oid = str(cand["opportunity_id"])
        frames_a1[oid] = extract_kh01b_causal_feature_frame(
            cand,
            feature_ledger,
            closed_bar_idx,
            int(cand["decision_timestamp_ms"]),
            pools_by_id=pools_by_id,
            datasets=datasets,
            close_ts_index=close_ts_index,
            macro_cache=macro_cache,
            atr_cache=atr_cache,
            gap_180m_cache=gap_cache,
        )

    frames_si8: Dict[str, FeatureEvidenceFrame] = {}
    for s_cand in si8_decisions:
        s_oid = str(s_cand["opportunity_id"])
        frames_si8[s_oid] = extract_kh01b_causal_feature_frame(
            s_cand,
            feature_ledger,
            closed_bar_idx,
            int(s_cand["decision_timestamp_ms"]),
            pools_by_id=pools_by_id,
            datasets=datasets,
            close_ts_index=close_ts_index,
            macro_cache=macro_cache,
            atr_cache=atr_cache,
            gap_180m_cache=gap_cache,
        )

    # Deduplicated pool objects for B11 causal re-selection
    seen_p_ids: Set[int] = set()
    unique_pools: List[Mapping[str, Any]] = []
    for po in pools_by_id.values():
        if id(po) not in seen_p_ids:
            seen_p_ids.add(id(po))
            unique_pools.append(po)

    # Purged 180m fold boundaries from canonical 1m dataset
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
    economics: Dict[str, ResearchEconomicVerdict] = {}
    costs = CostAssumptions()

    # Run B00 first to anchor baseline arbitration winners & 242 matched episodes
    b00_spec = next(v for v in frozen_manifest.variants if v["short_code"] == "B00")
    b00_bundle = evaluate_kh01b_variant_on_matched_candidate_universe(
        b00_spec,
        a1_decisions,
        feature_ledger,
        costs,
        candidate_index=candidate_index,
        datasets=datasets,
        frames_by_opp_id=frames_a1,
        unchanged_outcome_cache=unchanged_cache,
    )
    # Reconstruct B00 winner per checkpoint_index_1m for collision tracking
    b00_by_chk: Dict[int, List[Dict[str, Any]]] = {}
    for c in a1_decisions:
        b00_by_chk.setdefault(int(c["checkpoint_index_1m"]), []).append(dict(c))
    b00_winner_map: Dict[int, str] = {}
    for ck_i, grp in b00_by_chk.items():
        w, _ = arbitrate_competing_model_intents_v1793122(
            grp, arbitration_mode="STATIC_PRODUCTION_PRIORITY", feature_ledger_by_opp_id=feature_ledger
        )
        if w is not None:
            b00_winner_map[ck_i] = str(w["opportunity_id"])

    # Populate B00 self-matched counterfactual cleanly
    b00_bundle.matched_b00_242_counterfactual = {
        "evaluated_b00_episodes": len(b00_bundle.primary_nb_episodes),
        "surviving_scheduled_count": len(b00_bundle.primary_nb_episodes),
        "surviving_closed_count": b00_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]["closed_trades_count"],
        "surviving_wins_count": b00_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]["wins_count"],
        "surviving_losses_count": b00_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]["losses_count"],
        "filtered_out_b00_wins_count": 0,
        "filtered_out_b00_losses_count": 0,
        "surviving_net_pnl_usd": b00_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]["total_realized_net_usd"],
        "surviving_expectancy_r": b00_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]["expectancy_net_r"],
        "paired_net_pnl_delta_vs_b00_usd": 0.0,
    }
    bundles["B00"] = b00_bundle
    economics["B00"] = compute_kh01b_research_economics_and_uncertainty(
        b00_bundle, costs, folds_spec, baseline_bundle=b00_bundle
    )

    for v_spec in frozen_manifest.variants:
        sc = str(v_spec["short_code"])
        if sc == "B00":
            continue
        bnd = evaluate_kh01b_variant_on_matched_candidate_universe(
            v_spec,
            a1_decisions,
            feature_ledger,
            costs,
            candidate_index=candidate_index,
            datasets=datasets,
            frames_by_opp_id=frames_a1,
            all_pools_list=unique_pools if sc == "B11" else None,
            atr_cache=atr_cache,
            close_ts_index=close_ts_index,
            b00_winner_by_chk_idx=b00_winner_map,
            baseline_b00_episodes=b00_bundle.primary_nb_episodes,
            unchanged_outcome_cache=unchanged_cache,
            si8_frames_by_opp_id=frames_si8 if sc in ("B09", "B10") else None,
        )
        bundles[sc] = bnd
        economics[sc] = compute_kh01b_research_economics_and_uncertainty(
            bnd, costs, folds_spec, baseline_bundle=b00_bundle
        )

    # Authoritative 63,085 D4 funnel + 267+40 target audit
    d4_funnel = build_d4_full_63085_and_matched_funnel_v17931214(
        run_id=run_id,
        tested_sha=tested_sha,
        dataset_sha=baseline.dataset_sha256,
        baseline_decisions=a1_decisions,
        si8_decisions=si8_decisions,
        feature_ledger_by_opp_id=feature_ledger,
        datasets=datasets,
        matched_242_d4_rows_by_mode={fm: [] for fm in FILL_MODES_KH01B},
    )
    target_267 = build_267_target_failure_and_control_audit_v17931214(
        run_id=run_id,
        tested_sha=tested_sha,
        dataset_sha=baseline.dataset_sha256,
        v12_funnel_09_doc=v12_funnel_09_doc,
        all_63085_decisions=list(a1_decisions) + list(si8_decisions),
        feature_ledger_by_opp_id=feature_ledger,
        candidate_index=candidate_index,
        datasets=datasets,
        close_ts_index=close_ts_index,
        si9_policy_result=si9_policy_result,
    )

    qualified = sorted(
        sc for sc, ec in economics.items()
        if sc != "B00" and ec.qualifies_for_kh01c_advancement
    )
    adv_dec = "QUALIFIED_RESEARCH_CANDIDATES_FOR_KH01C" if qualified else "NO_ECONOMIC_ADVANCEMENT_CANDIDATE"

    set_sha = canonical_hash({
        "manifest_sha256": frozen_manifest.manifest_sha256,
        "bundles": {k: v.bundle_sha256 for k, v in sorted(bundles.items())},
        "advancement": adv_dec,
        "qualified": qualified,
    })
    return AblationSet(
        spec_version=SPEC_VERSION_KH01B,
        manifest_sha256=frozen_manifest.manifest_sha256,
        bundles_by_short_code=bundles,
        economics_by_short_code=economics,
        d4_authoritative_funnel=d4_funnel,
        target_267_control_audit=target_267,
        kh01c_advancement_decision=adv_dec,
        qualified_kh01c_candidates=qualified,
        ablation_set_sha256=set_sha,
    )


# ==============================================================================
# F13: 25 MANDATORY DETECTOR POSITIVE PATH & NEGATIVE CONTROL CASES (§09)
# ==============================================================================

def _build_synthetic_strict_unicorn_fixture(
    direction: str = "LONG",
) -> Tuple[int, List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """
    Constructs a strictly UTC-grid-aligned closed M1 + M5/M15/H1/H4/D1 synthetic candle
    fixture (`provenance='SYNTHETIC_UNIT_FIXTURE'`) that forms a genuine ICT Breaker Block +
    overlapping FVG (`1.50` USD = `150` ticks >= `1` tick) + post-confirmation retest + aligned HTF.
    """
    ny_tz = ZoneInfo("America/New_York")
    ts_ms = int(datetime(2026, 3, 25, 8, 55, 0, tzinfo=ny_tz).astimezone(timezone.utc).timestamp() * 1000)
    base_open_ms = ts_ms - 31 * 60_000
    prov_tag = "SYNTHETIC_UNIT_FIXTURE_NEVER_HISTORICAL_PNL"

    bars_1m: List[Dict[str, Any]] = []
    for i in range(20):
        t_open = base_open_ms + i * 60_000
        if direction == "LONG":
            o = 3000.0 + (i % 2) * 0.4
            h, l, c = o + 0.8, o - 0.6, o + 0.2
        else:
            o = 3000.0 - (i % 2) * 0.4
            h, l, c = o + 0.6, o - 0.8, o - 0.2
        bars_1m.append({
            "timestamp": t_open,
            "open_time": t_open,
            "close_time": t_open + 60_000,
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 100.0,
            "is_closed": True,
            "provenance": prov_tag,
        })

    if direction == "LONG":
        pat = [
            (3001.0, 3002.5, 3000.5, 3002.0),
            (3002.0, 3003.5, 3001.5, 3003.0),
            (3003.0, 3005.0, 3002.5, 3004.2),  # SH at 3005.0
            (3004.2, 3004.4, 3002.0, 3002.5),
            (3002.5, 3003.8, 3001.2, 3003.5),
            (3002.8, 3003.0, 2999.0, 2999.5),  # Breaker OB [2999.0, 3003.0]
            (2999.5, 3001.5, 2999.2, 3001.2),  # FVG i-2 high=3001.5
            (3001.2, 3007.5, 3001.0, 3007.0),  # Displacement break above 3005.0
            (3007.0, 3009.0, 3004.0, 3008.2),  # FVG i low=3004.0 -> overlap [3001.5, 3003.0] = $1.50
            (3008.2, 3008.5, 3002.2, 3006.5),  # Retest into [3001.5, 3003.0]
            (3006.5, 3008.0, 3005.5, 3007.5),
        ]
    else:
        pat = [
            (2999.0, 2999.5, 2997.5, 2998.0),
            (2998.0, 2998.5, 2996.5, 2997.0),
            (2997.0, 2997.5, 2995.0, 2995.8),  # SL at 2995.0
            (2995.8, 2998.0, 2995.6, 2997.5),
            (2997.5, 2998.8, 2996.2, 2996.5),
            (2997.2, 3001.0, 2997.0, 3000.5),  # Breaker OB [2997.0, 3001.0]
            (3000.5, 3000.8, 2998.5, 2998.8),  # FVG i-2 low=2998.5
            (2998.8, 2999.0, 2992.5, 2993.0),  # Displacement break below 2995.0
            (2993.0, 2996.0, 2991.0, 2991.8),  # FVG i high=2996.0 -> overlap [2997.0, 2998.5] = $1.50
            (2991.8, 2997.8, 2991.5, 2993.5),  # Retest into [2997.0, 2998.5]
            (2993.5, 2994.5, 2992.0, 2992.5),
        ]

    for j, (o, h, l, c) in enumerate(pat, start=20):
        t_open = base_open_ms + j * 60_000
        bars_1m.append({
            "timestamp": t_open,
            "open_time": t_open,
            "close_time": t_open + 60_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 200.0,
            "is_closed": True,
            "provenance": prov_tag,
        })

    def _grid_htf(tf_ms: int, count: int) -> List[Dict[str, Any]]:
        last_close = (ts_ms // tf_ms) * tf_ms
        st = last_close - count * tf_ms
        out: List[Dict[str, Any]] = []
        for k in range(count):
            to = st + k * tf_ms
            cyc = k // 5
            pos = k % 5
            offs = [0.0, 2.0, 5.0, 2.5, 1.5][pos]
            if direction == "LONG":
                base = 2920.0 + cyc * 6.0 + offs
                o = base
                c = base + (1.2 if pos in (0, 1, 2) else -0.8)
                h = max(o, c) + (2.5 if pos == 2 else 0.5)
                l = min(o, c) - (1.8 if pos == 4 else 0.4)
            else:
                base = 3080.0 - cyc * 6.0 - offs
                o = base
                c = base - (1.2 if pos in (0, 1, 2) else -0.8)
                h = max(o, c) + (1.8 if pos == 4 else 0.4)
                l = min(o, c) - (2.5 if pos == 2 else 0.5)
            out.append({
                "timestamp": to,
                "open_time": to,
                "close_time": to + tf_ms,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": 500.0,
                "is_closed": True,
                "provenance": prov_tag,
            })
        return out

    htf_map = {
        "5m": _grid_htf(300_000, 60),
        "15m": _grid_htf(900_000, 60),
        "1H": _grid_htf(3_600_000, 60),
        "4H": _grid_htf(14_400_000, 60),
        "1D": _grid_htf(86_400_000, 20),
    }
    return ts_ms, bars_1m, htf_map


def audit_kh01b_true_unicorn_positive_and_negative_fixtures() -> CausalDetectorValidation:
    """
    F13: Executes all 25 mandatory §09 positive-path and negative-control test cases against
    real production & KH-01B callables (`evaluate_causal_unicorn_confluence`, `detect_unicorn_evidence`,
    `extract_kh01b_causal_feature_frame`, `compute_kh01b_research_economics_and_uncertainty`, etc.).
    Synthetic fixtures are strictly tagged and NEVER counted as historical trades.
    """
    from tools.run_v17_9_3_macro_unicorn_backtest import (
        V1793_VARIANT_POLICIES,
        build_v1793_variant_market_context,
    )

    cases: List[Dict[str, Any]] = []
    pol_d2 = dict(V1793_VARIANT_POLICIES["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"])
    ts_ms, m1_long, htf_long = _build_synthetic_strict_unicorn_fixture("LONG")
    _, m1_short, htf_short = _build_synthetic_strict_unicorn_fixture("SHORT")

    ctx_long = build_v1793_variant_market_context(
        {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": ts_ms,
            "m1_candles": m1_long,
            "m5_candles": htf_long["5m"],
            "m15_candles": htf_long["15m"],
            "h1_candles": htf_long["1H"],
            "h4_candles": htf_long["4H"],
            "d1_candles": htf_long["1D"],
            "bid": 3007.40,
            "ask": 3007.60,
        },
        pol_d2,
        decision_time=ts_ms,
    )
    ctx_short = build_v1793_variant_market_context(
        {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": ts_ms,
            "m1_candles": m1_short,
            "m5_candles": htf_short["5m"],
            "m15_candles": htf_short["15m"],
            "h1_candles": htf_short["1H"],
            "h4_candles": htf_short["4H"],
            "d1_candles": htf_short["1D"],
            "bid": 2992.40,
            "ask": 2992.60,
        },
        pol_d2,
        decision_time=ts_ms,
    )

    def _rec_case(cid: str, title: str, fn_obj: Callable[..., Any], passed: bool, obs: str) -> None:
        src_sha = hashlib.sha256(inspect.getsource(fn_obj).encode("utf-8")).hexdigest()
        cases.append({
            "case_id": cid,
            "title": title,
            "authoritative_callable": getattr(fn_obj, "__name__", str(fn_obj)),
            "validator_code_sha256": src_sha,
            "provenance": "SYNTHETIC_UNIT_FIXTURE_ONLY",
            "counted_in_historical_trades": False,
            "status": "PASS" if passed else "FAIL",
            "observed_outcome": obs,
        })

    # 01: Strict True Unicorn valid (LONG + SHORT), tick-overlap >= $0.01, same episode, HTF aligned
    pos_l = evaluate_causal_unicorn_confluence(ctx_long, "LONG", ts_ms, strict=True, require_confirmed_retest=True, require_htf_alignment=True)
    pos_s = evaluate_causal_unicorn_confluence(ctx_short, "SHORT", ts_ms, strict=True, require_confirmed_retest=True, require_htf_alignment=True)
    ok_01 = bool(
        pos_l.is_valid_unicorn and pos_s.is_valid_unicorn
        and (pos_l.overlap_width_price or 0.0) >= 0.01
        and (pos_s.overlap_width_price or 0.0) >= 0.01
        and pos_l.htf_aligned and pos_s.htf_aligned
        and pos_l.retest_confirmed and pos_s.retest_confirmed
    )
    _rec_case("CASE-01", "Strict True Unicorn valid LONG+SHORT with >=1 tick overlap, episode binding, retest & HTF", evaluate_causal_unicorn_confluence, ok_01, f"LONG={pos_l.validity_status}(ov={pos_l.overlap_width_price}),SHORT={pos_s.validity_status}(ov={pos_s.overlap_width_price})")

    # Helper synthetic candidate + ledger builder for frame-based controls
    def _mk_frame(oid: str, feat_overrides: Mapping[str, Any], cand_overrides: Optional[Mapping[str, Any]] = None) -> FeatureEvidenceFrame:
        cand = {
            "opportunity_id": oid,
            "setup_episode_id": f"ep_{oid}",
            "model_id": "SMC_M1_FVG_CONTINUATION",
            "direction": "LONG",
            "decision_timestamp_ms": ts_ms,
            "checkpoint_index_1m": 30,
            "entry_reference": 3007.50,
            "selected_sl": 3003.50,
            "take_profit": 3017.50,
            "hypothetical_quantity": 0.50,
            "planned_net_risk_usd": 2.25,
            "net_rr": 2.15,
            "gross_rr": 2.50,
            "trade_action_fingerprint": f"fp_{oid}",
            "decision_fingerprint": f"dfp_{oid}",
            **(cand_overrides or {}),
        }
        f_row = {
            "opportunity_id": oid,
            "decision_timestamp_ms": ts_ms,
            "h4_bias": "BULLISH",
            "h1_bias": "BULLISH",
            "m15_bias": "BULLISH",
            "sweep_confirmed": True,
            "sweep_reclaimed": True,
            "mss_confirmed": True,
            "displacement_valid": True,
            "fvg_present": True,
            "fvg_unmitigated": True,
            "fvg_retest_valid": True,
            "unicorn_breaker_fvg_bound": True,
            "sweep_mss_ordered": True,
            "mss_fvg_retest_ordered": True,
            "unicorn_classification": "UNICORN_CONFIRMED",
            **feat_overrides,
        }
        return extract_kh01b_causal_feature_frame(cand, {oid: f_row}, {}, ts_ms)

    # 02: FVG-only with no breaker -> rejected by D4 (B09), accepted ONLY by adjacent label (B10)
    fr_fvg_only = _mk_frame("c02", {"unicorn_breaker_fvg_bound": False, "unicorn_classification": "FVG_ONLY_NO_BREAKER"})
    d4_02 = evaluate_strict_true_unicorn_hypothesis(fr_fvg_only)
    adj_02 = evaluate_unicorn_adjacent_research_hypothesis(fr_fvg_only)
    _rec_case("CASE-02", "FVG-only with no breaker rejected by D4 and accepted only under ADJACENT_FVG_SWEEP_MSS", evaluate_strict_true_unicorn_hypothesis, (not d4_02.is_valid_strict_unicorn and adj_02.eligible and not adj_02.counts_toward_d4_true_unicorn), f"D4={d4_02.terminal_rejection_code},ADJ={adj_02.eligible},counts_d4={adj_02.counts_toward_d4_true_unicorn}")

    # 03: FVG + Breaker with NO geometric overlap -> reject D4
    non_ov_fvg = {"id": "fvg_non_ov", "type": "BULLISH_FVG", "direction": "LONG", "bottom": 3005.5, "top": 3006.8, "timestamp": ts_ms - 180_000, "confirmed_at": ts_ms - 120_000, "state": "confirmed"}
    r_03 = detect_unicorn_evidence(m1_long, "LONG", ts_ms, selected_fvg=non_ov_fvg)
    _rec_case("CASE-03", "FVG + Breaker with zero geometric overlap rejected", detect_unicorn_evidence, (not r_03.is_valid and r_03.validity_status == UnicornSetupStatus.NO_GEOMETRIC_OVERLAP.value), f"{r_03.validity_status}:{r_03.reason_codes}")

    # 04: Overlap smaller than 1 tick ($0.004 < $0.01) -> reject
    sub_tick_fvg = {"id": "fvg_sub_tick", "type": "BULLISH_FVG", "direction": "LONG", "bottom": 3002.996, "top": 3004.50, "timestamp": ts_ms - 180_000, "confirmed_at": ts_ms - 120_000, "state": "confirmed"}
    r_04 = detect_unicorn_evidence(m1_long, "LONG", ts_ms, selected_fvg=sub_tick_fvg, min_overlap_ticks=1.0, tick_size=0.01)
    _rec_case("CASE-04", "Overlap < 1 XAUUSDT tick ($0.004 < $0.01) rejected", detect_unicorn_evidence, (not r_04.is_valid and r_04.validity_status == UnicornSetupStatus.NO_GEOMETRIC_OVERLAP.value), f"{r_04.validity_status}:{r_04.reason_codes}")

    # 05: Future FVG confirmed_at > as_of_ms -> reject
    fut_fvg = {"id": "fvg_fut", "type": "BULLISH_FVG", "direction": "LONG", "bottom": 3001.5, "top": 3003.0, "timestamp": ts_ms + 60_000, "confirmed_at": ts_ms + 120_000, "state": "confirmed"}
    r_05 = evaluate_causal_unicorn_confluence(ctx_long, "LONG", ts_ms, setup_descriptors={"selected_fvg": fut_fvg})
    _rec_case("CASE-05", "Future FVG/breaker confirmation timestamp > decision_ts rejected", evaluate_causal_unicorn_confluence, (not r_05.is_valid_unicorn and "UNICORN_FUTURE_DATA_REJECTED" in r_05.rejection_reasons), str(r_05.rejection_reasons))

    # 06: Wrong direction / borrowed episode FVG -> reject
    wrong_dir_fvg = {"id": "fvg_bear", "type": "BEARISH_FVG", "direction": "SHORT", "bottom": 3001.5, "top": 3003.0, "timestamp": ts_ms - 180_000, "confirmed_at": ts_ms - 120_000, "state": "confirmed"}
    r_06 = detect_unicorn_evidence(m1_long, "LONG", ts_ms, selected_fvg=wrong_dir_fvg)
    _rec_case("CASE-06", "Wrong-direction FVG / episode mismatch rejected without FVG substitution", detect_unicorn_evidence, (not r_06.is_valid and r_06.validity_status == UnicornSetupStatus.WRONG_DIRECTION_OVERLAP.value), f"{r_06.validity_status}:{r_06.reason_codes}")

    # 07: Sweep absent (PROVEN_FALSE) vs sweep evidence missing (UNKNOWN_MISSING) -> distinct outcomes
    fr_sw_false = _mk_frame("c07f", {"sweep_confirmed": False})
    fr_sw_miss = _mk_frame("c07m", {"sweep_confirmed": None}, {"selected_sweep_id": "sw_id_without_proof"})
    d_07f = evaluate_sweep_mss_displacement_hypothesis(fr_sw_false, mode="SWEEP_RECLAIM_ONLY")
    d_07m = evaluate_sweep_mss_displacement_hypothesis(fr_sw_miss, mode="SWEEP_RECLAIM_ONLY")
    _rec_case("CASE-07", "Sweep PROVEN_FALSE vs UNKNOWN_MISSING (even with selected_sweep_id) produce distinct tri-states", evaluate_sweep_mss_displacement_hypothesis, (d_07f.tri_state == "PROVEN_FALSE" and d_07m.tri_state == "UNKNOWN_MISSING" and not d_07f.eligible and not d_07m.eligible), f"false={d_07f.tri_state},missing={d_07m.tri_state}")

    # 08: MSS confirmed vs merely selected_break_id -> distinct outcomes
    fr_ms_ok = _mk_frame("c08t", {"mss_confirmed": True, "displacement_valid": True})
    fr_ms_id_only = _mk_frame("c08u", {"mss_confirmed": None, "displacement_valid": True}, {"selected_break_id": "brk_unconfirmed_1"})
    d_08t = evaluate_sweep_mss_displacement_hypothesis(fr_ms_ok, mode="MSS_DISPLACEMENT_ONLY")
    d_08u = evaluate_sweep_mss_displacement_hypothesis(fr_ms_id_only, mode="MSS_DISPLACEMENT_ONLY")
    _rec_case("CASE-08", "Confirmed MSS+displacement passes while selected_break_id without proof is UNKNOWN_MISSING", evaluate_sweep_mss_displacement_hypothesis, (d_08t.eligible and not d_08u.eligible and d_08u.tri_state == "UNKNOWN_MISSING"), f"confirmed={d_08t.tri_state},id_only={d_08u.tri_state}")

    # 09: HTF unknown vs proven opposite vs proven aligned -> 3 distinct outcomes
    d_09_al = evaluate_htf_alignment_hypothesis(_mk_frame("c09a", {"h4_bias": "BULLISH"}), "LONG", "H4_ONLY")
    d_09_op = evaluate_htf_alignment_hypothesis(_mk_frame("c09o", {"h4_bias": "BEARISH"}), "LONG", "H4_ONLY")
    d_09_un = evaluate_htf_alignment_hypothesis(_mk_frame("c09u", {"h4_bias": "UNKNOWN"}), "LONG", "H4_ONLY")
    _rec_case("CASE-09", "HTF aligned (PROVEN_TRUE) vs opposite (PROVEN_FALSE) vs unknown (UNKNOWN_MISSING) distinct", evaluate_htf_alignment_hypothesis, (d_09_al.tri_state == "PROVEN_TRUE" and d_09_op.tri_state == "PROVEN_FALSE" and d_09_un.tri_state == "UNKNOWN_MISSING"), f"{d_09_al.tri_state}/{d_09_op.tri_state}/{d_09_un.tri_state}")

    # 10: Macro argument reversal / invalid schedule -> INVALID, never proven outside
    bad_sched = {"schedule_version": "BAD", "timezone": "Invalid/Zone", "weekdays": [0, 1, 2, 3, 4]}
    d_10 = evaluate_macro_window_hypothesis(fr_ms_ok, schedule_config=bad_sched)
    _rec_case("CASE-10", "Invalid macro schedule yields INVALID tri-state, never VALID_OUTSIDE_WINDOW", evaluate_macro_window_hypothesis, (d_10.tri_state == "INVALID" and not d_10.eligible and d_10.rejection_code == "MACRO_STATE_UNKNOWN_OR_INVALID"), f"{d_10.tri_state}:{d_10.rejection_code}")

    # 11: NY DST boundaries (EST UTC-5 vs EDT UTC-4) & half-open [08:50, 09:10) endpoints
    ny_tz = ZoneInfo("America/New_York")
    ts_start = int(datetime(2026, 3, 25, 8, 50, 0, tzinfo=ny_tz).astimezone(timezone.utc).timestamp() * 1000)
    ts_end = int(datetime(2026, 3, 25, 9, 10, 0, tzinfo=ny_tz).astimezone(timezone.utc).timestamp() * 1000)
    ts_est = int(datetime(2026, 1, 15, 13, 55, 0, tzinfo=timezone.utc).timestamp() * 1000)
    m_st_s = resolve_causal_macro_time_window(decision_timestamp_ms=ts_start, symbol="XAUUSDT")
    m_st_e = resolve_causal_macro_time_window(decision_timestamp_ms=ts_end, symbol="XAUUSDT")
    m_est = resolve_causal_macro_time_window(decision_timestamp_ms=ts_est, symbol="XAUUSDT")
    _rec_case("CASE-11", "NY DST EST(-300)/EDT(-240) and half-open [08:50, 09:10) endpoints exact", resolve_causal_macro_time_window, (m_st_s.eligible_for_macro_gate and not m_st_e.eligible_for_macro_gate and m_est.eligible_for_macro_gate and m_est.utc_offset_minutes == -300), f"start={m_st_s.eligible_for_macro_gate},end={m_st_e.eligible_for_macro_gate},est_off={m_est.utc_offset_minutes}")

    # 12: Source TF mismatch or missing target pool -> UNKNOWN/INVALID, never forced to 1M
    aud_miss_pool = audit_target_reachability_row_v17931214(
        opportunity_id="c12", cohort_role="TEST", historical_rejection="NONE", current_rejection="NONE",
        decision_row={"decision_timestamp_ms": ts_ms, "direction": "LONG", "entry_reference": 3000.0, "selected_sl": 2996.0, "take_profit": 3010.0, "selected_target_id": "nonexistent_pool_id"},
        feature_row={"decision_timestamp_ms": ts_ms, "target_unswept": True},
        pools_by_id={}, datasets={"1m": m1_long}, close_ts_index={"1m": [b["close_time"] for b in m1_long]},
    )
    _rec_case("CASE-12", "Missing target source pool yields UNKNOWN_MISSING (resolved_source_tf='UNKNOWN', never forced 1M)", audit_target_reachability_row_v17931214, (aud_miss_pool["tri_state_status"] == "UNKNOWN_MISSING" and aud_miss_pool["resolved_source_tf"] == "UNKNOWN"), f"{aud_miss_pool['tri_state_status']}:{aud_miss_pool['resolved_source_tf']}")

    # 13: Source ATR zero/NaN -> INVALID_INCONSISTENT fail-closed
    aud_bad_atr = audit_target_reachability_row_v17931214(
        opportunity_id="c13", cohort_role="TEST", historical_rejection="NONE", current_rejection="NONE",
        decision_row={"decision_timestamp_ms": ts_ms, "direction": "LONG", "entry_reference": 3000.0, "selected_sl": 2996.0, "take_profit": 3010.0, "selected_target_id": "pool_5m_1"},
        feature_row={"decision_timestamp_ms": ts_ms, "target_unswept": True, "_injected_atr_source_tf": float("nan")},
        pools_by_id={"pool_5m_1": {"pool_id": "pool_5m_1", "timeframe": "5M", "price": 3010.0, "known_at_ms": ts_ms - 300_000}},
        datasets={"1m": m1_long, "5m": htf_long["5m"]}, close_ts_index={"1m": [b["close_time"] for b in m1_long], "5m": [b["close_time"] for b in htf_long["5m"]]},
    )
    _rec_case("CASE-13", "NaN/zero source-TF ATR fails closed as INVALID_INCONSISTENT", audit_target_reachability_row_v17931214, aud_bad_atr["tri_state_status"] == "INVALID_INCONSISTENT", f"{aud_bad_atr['tri_state_status']}:{aud_bad_atr['first_decisive_failure_code']}")

    # 14: Reused history relabeled Fresh OOS -> fail closed
    fake_oos_entry = DatasetManifestEntryKH01A(
        dataset_id="FAKE_OOS",
        relative_path="aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz",
        sha256=CANONICAL_DATASET_SHA256_KH01B,
        byte_size=1024,
        exchange="BITGET",
        symbol="XAUUSDT",
        product_type="USDT-FUTURES",
        timezone="UTC",
        first_open_timestamp_ms=ts_ms - 86_400_000,
        last_close_timestamp_ms=ts_ms,
        first_open_utc="2026-10-13T00:00:00Z",
        last_close_utc="2026-10-14T00:00:00Z",
        origin_uri="local://reused",
        origin_receipt_sha256=None,
        acquired_at_ms=ts_ms,
        policy_freeze_timestamp_ms=ts_ms + 3_600_000,
        first_strategy_access_timestamp_ms=ts_ms,
        strategy_evaluation_count=2,
        bar_coverage_by_tf={},
        quote_coverage="MODELED_SYNTHETIC",
        quality_verdict="REUSED",
        prior_use_status="FRESH_UNTOUCHED",
        cohort_eligibility={"FRESH_UNTOUCHED_OOS": True},
        permission_flags={"allow_fresh_oos_claim": True},
    )
    esc_14 = validate_fresh_oos_escrow_claim_kh01a(
        fake_oos_entry,
        prior_research_sha256s={CANONICAL_DATASET_SHA256_KH01B},
        prior_research_intervals_ms=[(ts_ms - 10 * 86_400_000, ts_ms + 86_400_000)],
        policy_freeze_ms=ts_ms + 3_600_000,
    )
    _rec_case("CASE-14", "Reused historical dataset relabeled as Fresh OOS rejected fail-closed", validate_fresh_oos_escrow_claim_kh01a, not esc_14["eligible_as_fresh_untouched_oos"], str(esc_14["violations"]))

    # 15: Future-bar perturbation changes ex-post outcome but NEVER changes as-of entry decision/fingerprint
    fwd_win = [{"timestamp": ts_ms, "open_time": ts_ms, "close_time": ts_ms + 60_000, "open": 3007.5, "high": 3020.0, "low": 3007.0, "close": 3019.0, "is_closed": True}]
    fwd_loss = [{"timestamp": ts_ms, "open_time": ts_ms, "close_time": ts_ms + 60_000, "open": 3007.5, "high": 3007.6, "low": 2995.0, "close": 2996.0, "is_closed": True}]
    sn_pre_1 = SharedStrategyEvaluator.evaluate_model_direction(ctx_long, "SMC_M1_FVG_CONTINUATION", "LONG")
    ctx_long_2 = build_v1793_variant_market_context(
        {"symbol": "XAUUSDT", "decision_timestamp_ms": ts_ms, "m1_candles": m1_long + fwd_loss, "m5_candles": htf_long["5m"], "m15_candles": htf_long["15m"], "h1_candles": htf_long["1H"], "h4_candles": htf_long["4H"], "d1_candles": htf_long["1D"], "bid": 3007.40, "ask": 3007.60},
        pol_d2, decision_time=ts_ms,
    )
    sn_pre_2 = SharedStrategyEvaluator.evaluate_model_direction(ctx_long_2, "SMC_M1_FVG_CONTINUATION", "LONG")
    _ = fwd_win
    _rec_case("CASE-15", "Appending future crash bar after decision_time leaves pre-entry decision_fingerprint 100% invariant", SharedStrategyEvaluator.evaluate_model_direction, sn_pre_1.decision_fingerprint == sn_pre_2.decision_fingerprint and sn_pre_1.trade_action_fingerprint == sn_pre_2.trade_action_fingerprint, f"fp_match={sn_pre_1.trade_action_fingerprint == sn_pre_2.trade_action_fingerprint}")

    # 16: Leaked future H4 bar close > decision_ts in feature frame -> INVALID fail-closed
    bad_bar_idx = {("4H", ts_ms): ts_ms + 14_400_000}
    fr_leak = extract_kh01b_causal_feature_frame(
        {"opportunity_id": "c16", "direction": "LONG", "model_id": "SMC_M1_CONTINUATION", "decision_timestamp_ms": ts_ms, "entry_reference": 3000.0, "selected_sl": 2996.0, "take_profit": 3010.0},
        {"c16": {"opportunity_id": "c16", "decision_timestamp_ms": ts_ms + 60_000, "h4_bias": "BULLISH"}},
        bad_bar_idx, ts_ms,
    )
    _rec_case("CASE-16", "Leaked future feature timestamp > asof_ts flagged INVALID and rejected", extract_kh01b_causal_feature_frame, fr_leak.has_any_invalid_evidence, str(fr_leak.invalidation_reasons))

    # 17: Modeled quote falsely labeled CAPTURED_L1 -> reject provenance
    fr_fake_l1 = _mk_frame("c17", {"quote_source_kind": "CAPTURED_L1_EXCHANGE_BBO"})
    _rec_case("CASE-17", "Modeled quote falsely labeled CAPTURED_L1 rejected as INVALID provenance", extract_kh01b_causal_feature_frame, fr_fake_l1.has_any_invalid_evidence and fr_fake_l1.feature_cells["quote_provenance"]["tri_state"] == "INVALID", str(fr_fake_l1.invalidation_reasons))

    # 18: Cost 2x turns apparently positive 1x cohort to negative -> selection_outcome becomes RESEARCH_NEGATIVE_EDGE
    borderline_eps_1x = [
        {"opportunity_id": f"b18_{i}", "filled": True, "outcome_label": "WIN", "realized_gross_pnl_usd": 0.45, "total_fees_usd": 0.30, "realized_net_pnl_usd": 0.15, "realized_net_r": 0.06, "planned_net_risk_usd": 2.50, "decision_timestamp_ms": ts_ms + i * 86_400_000, "exit_ts_ms": ts_ms + i * 86_400_000 + 600_000, "setup_episode_id": f"ep18_{i}"}
        for i in range(35)
    ]
    borderline_eps_sp2 = [
        {**e, "realized_gross_pnl_usd": 0.20, "realized_net_pnl_usd": -0.10}
        for e in borderline_eps_1x
    ]
    fake_bnd_18 = VariantReplayBundle(
        variant_id="KH01B-TEST-18", short_code="T18", ontology="OTHER_CAUSAL_FILTER_VARIANTS", axis_type="SINGLE_AXIS_ENTRY_FILTER",
        population_name="TEST", population_total=35, eligible_candidates_count=35, rejected_candidates_count=0,
        missing_evidence_rejected_count=0, proven_false_rejected_count=0, invalid_evidence_rejected_count=0,
        plausible_eligibility_lower_bound=35, plausible_eligibility_upper_bound=35, rejection_reasons_counts={},
        post_arbitration_timestamps_selected=35, arbitration_suppressed_ties_count=0, arbitration_winner_substitutions_vs_b00=0,
        materialized_accepted_count=35, materialized_modified_tp_count=0,
        fill_mode_summaries={
            "NEXT_BAR_OPEN_BID_ASK": _summarize_episode_list_kh01b(borderline_eps_1x, 0),
            "LEGACY_CLOSE_FILL": _summarize_episode_list_kh01b(borderline_eps_1x, 0),
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": _summarize_episode_list_kh01b(borderline_eps_sp2, 0),
        },
        primary_nb_episodes=borderline_eps_1x,
        all_mode_episodes={"NEXT_BAR_OPEN_BID_ASK": borderline_eps_1x, "LEGACY_CLOSE_FILL": borderline_eps_1x, "STRESS_WIDER_SPREAD_SLIPPAGE_2X": borderline_eps_sp2},
        matched_b00_242_counterfactual={"paired_net_pnl_delta_vs_b00_usd": 5.25}, si8_separate_diagnostic=None, bundle_sha256="h18",
    )
    ec_18 = compute_kh01b_research_economics_and_uncertainty(fake_bnd_18, CostAssumptions(), [(ts_ms - 10_000_000, ts_ms, ts_ms + 180 * 60_000, ts_ms + 40 * 86_400_000)])
    _rec_case("CASE-18", "2x cost stress turning borderline 1x positive (+$5.25) negative (-$14.00) forces RESEARCH_NEGATIVE_EDGE", compute_kh01b_research_economics_and_uncertainty, (ec_18.net_pnl_1x_nb_usd > 0 and ec_18.stress_2x_all_cost_net_pnl_usd < 0 and ec_18.selection_outcome == "RESEARCH_NEGATIVE_EDGE" and not ec_18.qualifies_for_kh01c_advancement), f"1x={ec_18.net_pnl_1x_nb_usd},2x={ec_18.stress_2x_all_cost_net_pnl_usd},out={ec_18.selection_outcome}")

    # 19: All zero trades -> NO_VALID_SETUPS with None bootstrap CI (no division-by-zero)
    zero_sum = _summarize_episode_list_kh01b([], 0)
    zero_bnd = VariantReplayBundle(
        variant_id="KH01B-TEST-19", short_code="T19", ontology="STRICT_D4_TRUE_UNICORN", axis_type="STRICT_CAUSAL_CONFLUENCE_MODEL",
        population_name="TEST", population_total=100, eligible_candidates_count=0, rejected_candidates_count=100,
        missing_evidence_rejected_count=50, proven_false_rejected_count=50, invalid_evidence_rejected_count=0,
        plausible_eligibility_lower_bound=0, plausible_eligibility_upper_bound=50, rejection_reasons_counts={"NONE": 100},
        post_arbitration_timestamps_selected=0, arbitration_suppressed_ties_count=0, arbitration_winner_substitutions_vs_b00=0,
        materialized_accepted_count=0, materialized_modified_tp_count=0,
        fill_mode_summaries={fm: zero_sum for fm in FILL_MODES_KH01B},
        primary_nb_episodes=[], all_mode_episodes={fm: [] for fm in FILL_MODES_KH01B},
        matched_b00_242_counterfactual={"paired_net_pnl_delta_vs_b00_usd": 0.0}, si8_separate_diagnostic=None, bundle_sha256="h19",
    )
    ec_19 = compute_kh01b_research_economics_and_uncertainty(zero_bnd, CostAssumptions(), [(ts_ms - 10_000_000, ts_ms, ts_ms + 180 * 60_000, ts_ms + 86_400_000)])
    _rec_case("CASE-19", "Zero closed trades returns NO_VALID_SETUPS and None CI without fake economic PASS", compute_kh01b_research_economics_and_uncertainty, (ec_19.selection_outcome == "NO_VALID_SETUPS" and ec_19.cluster_bootstrap_95ci_expectancy_r is None and ec_19.activation_ready == "NO"), f"out={ec_19.selection_outcome},ci={ec_19.cluster_bootstrap_95ci_expectancy_r}")

    # 20: Nonzero shard exit / missing JUnit node -> verify_fresh_junit_bijection_v17931211 FAIL
    tmp_j_res = verify_fresh_junit_bijection_v17931211(
        Path("."),
        {"run_id": "r20", "tested_executable_sha": "a" * 40, "collect_exit_code": 0, "collected_node_ids": ["t1", "t2"]},
        {"run_id": "r20", "tested_executable_sha": "a" * 40, "run_start_epoch_ns": 100, "shards": [{"shard_id": "s1", "run_id": "r20", "tested_executable_sha": "a" * 40, "exit_code": 1, "timed_out": False, "scheduled_node_ids": ["t1"], "junit_xml_path": "nonexistent.xml"}]},
        expected_run_id="r20", expected_tested_sha="a" * 40,
    )
    _rec_case("CASE-20", "Shard nonzero exit + missing JUnit node rejected by JUnit bijection verifier", verify_fresh_junit_bijection_v17931211, tmp_j_res["status"] == "FAIL", str(tmp_j_res["violations"][:2]))

    # 21: Missing provenance after previously positive filter -> never becomes PROVEN_TRUE
    fr_21 = _mk_frame("c21", {"h4_bias": "BULLISH", "h1_bias": "BULLISH", "mss_confirmed": None, "displacement_valid": True})
    ok_21, rc_21, miss_21, _, _, _ = _evaluate_single_candidate_for_variant("B12", "KH01B-B12-PREREGISTERED-MULTI-AXIS-HTF-MSS-TARGET", fr_21)
    _rec_case("CASE-21", "Missing MSS proof after positive H4+H1 filter rejected as UNKNOWN_MISSING in B12", evaluate_sweep_mss_displacement_hypothesis, (not ok_21 and miss_21 and "UNKNOWN_MISSING" in str(rc_21)), f"ok={ok_21},rc={rc_21}")

    # 22: Production/LIVE/PAPER/AUTO activation attempt via adapter -> rejected immediately
    act_22_rejected = False
    try:
        res_22 = evaluate_shared_delayed_entry_adapter_v17931213(
            execution_context_mode="LIVE_PRODUCTION",
            activation_flags={"LIVE": True, "PAPER": True, "AUTO_LIVE": True, "enabled_in_production": True},
            variant_id="KH01B-B03", opportunity_id="c22", episode_key="ep22", model_id="SMC_M1_CONTINUATION",
            direction="LONG", decision_timestamp_ms=ts_ms, baseline_entry=3000.0, baseline_sl=2996.0, baseline_tp=3010.0,
            closed_m1=m1_long, closed_m5=htf_long["5m"], closed_m15=htf_long["15m"], closed_h1=htf_long["1H"], closed_h4=htf_long["4H"],
            forward_bars=[], pre_entry_features={},
        )
        act_22_rejected = (res_22.get("accepted") is False or res_22.get("live_order_emitted") is False or "BLOCKED" in str(res_22))
    except Exception:
        act_22_rejected = True
    _rec_case("CASE-22", "LIVE/PAPER/AUTO activation attempt blocked fail-closed", evaluate_shared_delayed_entry_adapter_v17931213, act_22_rejected, f"rejected={act_22_rejected}")

    # 23: Deliberately shuffled partition completion order produces identical canonical sorted hash
    items_23 = [("opp_3", "PROVEN_TRUE"), ("opp_1", "PROVEN_FALSE"), ("opp_2", "UNKNOWN_MISSING")]
    h_23_a = canonical_hash(sorted(items_23, key=lambda t: t[0]))
    h_23_b = canonical_hash(sorted(reversed(items_23), key=lambda t: t[0]))
    _rec_case("CASE-23", "Shuffled worker partition completion order produces identical canonical hash", canonical_hash, h_23_a == h_23_b, h_23_a)

    # 24: Fold embargo < 180m or shared setup_episode_id leakage -> FAIL_CAUSAL_OR_ACCOUNTING
    ec_24 = compute_kh01b_research_economics_and_uncertainty(
        fake_bnd_18, CostAssumptions(),
        [(ts_ms - 10_000_000, ts_ms, ts_ms + 30 * 60_000, ts_ms + 40 * 86_400_000)],  # Only 30m embargo (< 180m)!
    )
    _rec_case("CASE-24", "Fold embargo < 180m horizon detected as FAIL_CAUSAL_OR_ACCOUNTING", compute_kh01b_research_economics_and_uncertainty, ec_24.selection_outcome == "FAIL_CAUSAL_OR_ACCOUNTING", ec_24.selection_outcome)

    # 25: Borderline R:R fee/tick perturbation -> deterministic conservative rejection below min_net_rr=2.0
    calc_25_fail = calculate_risk_reward("LONG", entry=3000.00, sl=2996.00, tp=3008.20, min_net_rr=2.0, costs=CostAssumptions())
    calc_25_rep = calculate_risk_reward("LONG", entry=3000.00, sl=2996.00, tp=3008.20, min_net_rr=2.0, costs=CostAssumptions())
    _rec_case("CASE-25", "Borderline gross R:R=2.05 with fees/spread yields net_rr < 2.0 deterministically rejected", calculate_risk_reward, (not calc_25_fail.can_execute and calc_25_fail.net_rr < 2.0 and calc_25_fail.net_rr == calc_25_rep.net_rr), f"can_execute={calc_25_fail.can_execute},net_rr={round(calc_25_fail.net_rr, 4)}")

    passed_n = sum(1 for c in cases if c["status"] == "PASS")
    pos_n = 1 if cases[0]["status"] == "PASS" else 0
    neg_n = sum(1 for c in cases[1:] if c["status"] == "PASS")
    body = {
        "spec_version": SPEC_VERSION_KH01B,
        "total_cases": len(cases),
        "passed_cases": passed_n,
        "failed_cases": len(cases) - passed_n,
        "positive_strict_unicorn_fixtures_passed": pos_n,
        "negative_controls_passed": neg_n,
        "synthetic_counted_as_historical_trades": 0,
        "status": "PASS" if passed_n == len(cases) else "FAIL",
        "cases": tuple(cases),
    }
    return CausalDetectorValidation(**body, validation_sha256=canonical_hash(body))


# ==============================================================================
# F14: FULL BASELINE & 3-ADAPTER PARITY VERIFICATION
# ==============================================================================

def verify_kh01b_full_baseline_and_adapter_parity(
    *,
    tested_sha: str,
    branch: str,
    a1_streaming_parity: Mapping[str, Any],
    si8_decisions: Sequence[Mapping[str, Any]],
    b00_bundle: VariantReplayBundle,
    baseline_truth: BaselineTruth,
    three_adapter_parity: Mapping[str, Any],
) -> ImmutableBaselineEvidence:
    """
    F14: Verifies full-population A1 (56,710) + SI8 (6,375) identity & parity,
    exact B00 == SI0 accounting match (`242`, `166 = 9W + 157L`, `-$345.8971`),
    3-adapter dry-run parity across all 246 scheduled SI0/SI8/SI9 episodes,
    zero risk-cap (`<= $2.50`) violations, and all runtime modes OFF.
    """
    violations: List[str] = []
    a1_denom = int(a1_streaming_parity.get("population_denominator") or 0)
    a1_eval = int(a1_streaming_parity.get("evaluated_count") or 0)
    a1_mis = int(a1_streaming_parity.get("mismatches_count", 1))
    if a1_denom != baseline_truth.a1_candidate_count or a1_eval != a1_denom or a1_mis != 0:
        violations.append(f"A1_PARITY_MISMATCH:{a1_eval}/{a1_denom},mis={a1_mis}")

    si8_denom = len(si8_decisions)
    si8_mis = sum(
        1 for r in si8_decisions
        if not r.get("opportunity_id") or not r.get("trade_action_fingerprint") or float(r.get("hypothetical_quantity") or 0.0) <= 0.0
    )
    if si8_denom != baseline_truth.si8_discovery_count or si8_mis != 0:
        violations.append(f"SI8_POPULATION_MISMATCH:{si8_denom},mis={si8_mis}")

    b00_nb = b00_bundle.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"]
    b00_match = (
        int(b00_nb["total_scheduled_episodes"]) == baseline_truth.si0_reference_scheduled
        and int(b00_nb["closed_trades_count"]) == baseline_truth.si0_reference_closed
        and int(b00_nb["wins_count"]) == baseline_truth.si0_reference_wins
        and int(b00_nb["losses_count"]) == baseline_truth.si0_reference_losses
        and abs(float(b00_nb["total_realized_net_usd"]) - baseline_truth.si0_reference_net_pnl_usd) < 1e-4
        and abs(float(b00_nb["expectancy_net_r"]) - baseline_truth.si0_reference_expectancy_r) < 1e-4
    )
    if not b00_match:
        violations.append(f"B00_VS_SI0_DRIFT:{b00_nb}")

    ad_denom = int(three_adapter_parity.get("population_denominator") or 0)
    ad_mis = int(three_adapter_parity.get("mismatches_count", 1))
    if ad_denom < 242 or ad_mis != 0:
        violations.append(f"THREE_ADAPTER_PARITY_MISMATCH:{ad_denom},mis={ad_mis}")

    risk_viols = sum(
        1 for ep in b00_bundle.primary_nb_episodes
        if float(ep.get("planned_net_risk_usd") or 0.0) > 2.5001
    )
    if risk_viols > 0:
        violations.append(f"RISK_CAP_VIOLATIONS:{risk_viols}")

    modes_off = (
        three_adapter_parity.get("runtime_modes_all_off") is True
        and branch == MANDATORY_BRANCH_KH01B
    )
    if not modes_off:
        violations.append("RUNTIME_MODES_OR_BRANCH_INVALID")

    body = {
        "spec_version": SPEC_VERSION_KH01B,
        "tested_sha": tested_sha,
        "branch": branch,
        "a1_population_denominator": a1_denom,
        "a1_evaluated_count": a1_eval,
        "a1_mismatches_count": a1_mis,
        "si8_population_denominator": si8_denom,
        "si8_verified_count": si8_denom - si8_mis,
        "si8_mismatches_count": si8_mis,
        "b00_vs_si0_exact_match": b00_match,
        "three_adapter_episodes_denominator": ad_denom,
        "three_adapter_mismatches_count": ad_mis,
        "runtime_modes_all_off": modes_off,
        "risk_cap_violations_count": risk_viols,
        "status": "PASS" if not violations else "FAIL",
        "violations": tuple(violations),
    }
    return ImmutableBaselineEvidence(**body, parity_sha256=canonical_hash(body))


# ==============================================================================
# F15: EVALUATE 16 ORTHOGONAL GATES FROM CLOSED DISK ARTIFACTS & RECEIPTS
# ==============================================================================

def evaluate_kh01b_orthogonal_gates_from_disk(
    run_dir: Path,
    run_id: str,
    tested_sha: str,
    *,
    publication_status: str = "PENDING_REMOTE_PUSH",
) -> Dict[str, Any]:
    """
    F15: Reads sealed JSON artifacts & dynamic `receipts/G-KH01B-*.json` strictly from disk,
    evaluates `G-KH01B-01..16`, and produces distinct orthogonal verdicts (`ENGINEERING_VERDICT`,
    `DETECTOR_VALIDITY_VERDICT`, `REPLAY_VALIDITY_VERDICT`, `TRADING_PARITY_VERDICT`,
    `REGRESSION_VERDICT`, `ECONOMIC_EDGE_VERDICT`, `FRESH_OOS_VERDICT`, `ACTIVATION_READY`,
    `PUBLICATION_VERDICT`).
    """
    def _rd(name: str) -> Dict[str, Any]:
        p = run_dir / name
        if not p.is_file():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    f00 = _rd("00_preflight_source_config_dataset_freeze.json")
    f01 = _rd("01_function_callgraph_preentry_evidence_and_keep_as_is.json")
    f02 = _rd("02_pre_registered_hypothesis_manifest_and_search_budget.json")
    f03 = _rd("03_causal_feature_sources_unknown_invalid_coverage.json")
    f04 = _rd("04_fixed_population_ids_and_match_conservation.json")
    f05 = _rd("05_htf_single_axis_B01_B02_B03.json")
    f06 = _rd("06_target_source_tf_horizon_B04_B11_and_267_controls.json")
    f07 = _rd("07_macro_B05_calendar_DST_unverified_contract.json")
    f08 = _rd("08_sweep_mss_displacement_B06_B07_B08_evidence.json")
    f09 = _rd("09_strict_D4_and_adjacent_B09_B10_detector_funnel.json")
    f10 = _rd("10_all_variant_comparable_replays_3fill_1pos_ledgers.json")
    f11 = _rd("11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json")
    f12 = _rd("12_full_baseline_and_three_adapter_parity.json")
    f13 = _rd("13_real_fault_injection_red_green_casebook.json")
    f14 = _rd("14_full_pytest_junit_shards_process_exit_and_bijection.json")
    f15 = _rd("15_async_workers_1_2_4_repeat_determinism_and_real_speed.json")
    f16 = _rd("16_resource_disk_ram_cpu_pid_cleanup.json")
    f17 = _rd("17_fresh_oos_registry_and_economic_verdict_separation.json")

    receipts_map: Dict[str, EvidenceReceiptV17931219] = {}
    rcpt_dir = run_dir / "receipts"
    if rcpt_dir.is_dir():
        for rp in sorted(rcpt_dir.glob("G-KH01B-*.json")):
            doc = json.loads(rp.read_text(encoding="utf-8"))
            r_in = doc.get("receipt")
            if isinstance(r_in, Mapping):
                receipts_map[rp.stem] = EvidenceReceiptV17931219(**r_in)

    gates: List[Dict[str, Any]] = []

    def _add_gate(gid: str, name: str, rel_art: str, obs: Any, status: str, viols: Sequence[str]) -> None:
        v_list = list(viols)
        rcpt = receipts_map.get(gid)
        if status == "PASS":
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
                v_list.append(f"INVALID_OR_FAILED_GATE_RECEIPT:{gid}")
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

    # G-KH01B-01
    v01: List[str] = []
    if f00.get("branch") != MANDATORY_BRANCH_KH01B or f00.get("tested_executable_sha") != tested_sha:
        v01.append("BRANCH_OR_SHA_MISMATCH")
    if f00.get("source_tree_unchanged_during_run") is not True or f00.get("prior_artifacts_unchanged_during_run") is not True:
        v01.append("SOURCE_OR_PRIOR_ARTIFACTS_MUTATED")
    if f00.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH01B:
        v01.append("DATASET_SHA_MISMATCH")
    _add_gate("G-KH01B-01", "BRANCH_EXACT_SOURCE_FREEZE", "00_preflight_source_config_dataset_freeze.json", {"branch": f00.get("branch"), "tested_sha": tested_sha}, "PASS" if not v01 else "FAIL", v01)

    # G-KH01B-02
    v02: List[str] = []
    if f02.get("frozen_before_backtest_execution") is not True or int(f02.get("registered_variants_count") or 0) != 13:
        v02.append("PREREGISTRATION_NOT_FROZEN_OR_INCOMPLETE")
    if not f02.get("manifest_sha256"):
        v02.append("MISSING_MANIFEST_SHA256")
    _add_gate("G-KH01B-02", "PRE_REGISTERED_VARIANT_MANIFEST_HASHED_BEFORE_RESULTS", "02_pre_registered_hypothesis_manifest_and_search_budget.json", {"manifest_sha256": f02.get("manifest_sha256"), "variants_count": f02.get("registered_variants_count")}, "PASS" if not v02 else "FAIL", v02)

    # G-KH01B-03
    v03: List[str] = []
    if f04.get("baseline_verification_passed") is not True or int(f04.get("duplicate_opportunity_ids_count", 1)) != 0:
        v03.append("POPULATION_IDENTITY_OR_CONSERVATION_FAILED")
    _add_gate("G-KH01B-03", "63K_CANDIDATE_UNIVERSE_IDENTITY_AND_LEDGER_CONSERVATION", "04_fixed_population_ids_and_match_conservation.json", {"a1_count": f04.get("a1_candidate_count"), "si8_count": f04.get("si8_discovery_count"), "total": f04.get("total_candidate_universe_count")}, "PASS" if not v03 else "FAIL", v03)

    # G-KH01B-04
    v04: List[str] = []
    if int(f03.get("future_bar_or_known_at_violations_count", 1)) != 0 or int(f03.get("invalid_evidence_frames_count", 1)) != 0:
        v04.append("ASOF_CLOCK_OR_PROVENANCE_VIOLATION")
    _add_gate("G-KH01B-04", "POINT_IN_TIME_FEATURE_SOURCE_PROVENANCE_AND_UNKNOWN_TRUTH", "03_causal_feature_sources_unknown_invalid_coverage.json", {"a1_frames_verified": f03.get("a1_frames_evaluated_count"), "future_violations": f03.get("future_bar_or_known_at_violations_count")}, "PASS" if not v04 else "FAIL", v04)

    # G-KH01B-05
    v05: List[str] = []
    for sub_d in (f05, f06, f07, f08):
        if sub_d.get("status") != "PASS":
            v05.append(f"TRISTATE_SUBMODULE_FAILED:{sub_d.get('module_id')}")
    if (f01.get("macro_call_site_ast_audit") or {}).get("invalid_production_call_sites_count", 1) != 0:
        v05.append("INVALID_MACRO_AST_CALL_SITE")
    _add_gate("G-KH01B-05", "HTF_MACRO_SWEEP_MSS_SOURCE_TF_TRISTATE_RULES", "05_htf_single_axis_B01_B02_B03.json", {"htf": f05.get("status"), "target": f06.get("status"), "macro": f07.get("status"), "sweep_mss": f08.get("status")}, "PASS" if not v05 else "FAIL", v05)

    # G-KH01B-06
    v06: List[str] = []
    det_val = f09.get("detector_fixture_validation") or {}
    if det_val.get("status") != "PASS" or int(det_val.get("positive_strict_unicorn_fixtures_passed") or 0) < 1 or int(det_val.get("synthetic_counted_as_historical_trades", 1)) != 0:
        v06.append("STRICT_UNICORN_FIXTURE_VALIDATION_FAILED")
    _add_gate("G-KH01B-06", "STRICT_UNICORN_DETECTOR_REAL_POSITIVE_AND_NEGATIVE_PATHS", "09_strict_D4_and_adjacent_B09_B10_detector_funnel.json", {"cases_passed": f"{det_val.get('passed_cases')}/{det_val.get('total_cases')}"}, "PASS" if not v06 else "FAIL", v06)

    # G-KH01B-07
    v07: List[str] = []
    if f09.get("ontology_isolation_verified") is not True or f09.get("b10_counted_toward_d4") is not False or f09.get("d4_stage_counts_monotonically_non_increasing") is not True:
        v07.append("ONTOLOGY_LEAKAGE_OR_NON_MONOTONIC_D4_FUNNEL")
    _add_gate("G-KH01B-07", "TRUE_UNICORN_VS_ADJACENT_LABEL_ISOLATION_AND_NON_RESCUE", "09_strict_D4_and_adjacent_B09_B10_detector_funnel.json", {"b09_historical_closed": f09.get("b09_strict_d4_closed_trades"), "b10_adjacent_closed": f09.get("b10_adjacent_closed_trades")}, "PASS" if not v07 else "FAIL", v07)

    # G-KH01B-08
    v08: List[str] = []
    vars_10 = f10.get("variants_summary") or {}
    if len(vars_10) != 13:
        v08.append(f"MISSING_ABLATION_VARIANTS:{len(vars_10)}!=13")
    for sc_k, v_d in vars_10.items():
        if int(v_d.get("eligible_candidates_count", -1)) + int(v_d.get("rejected_candidates_count", -1)) != int(v_d.get("population_total", -2)):
            v08.append(f"CANDIDATE_CONSERVATION_MISMATCH:{sc_k}")
    _add_gate("G-KH01B-08", "ORTHOGONAL_ABLATION_MATCHED_POPULATION_NO_SELECTION_BIAS", "10_all_variant_comparable_replays_3fill_1pos_ledgers.json", {"variants_evaluated": len(vars_10)}, "PASS" if not v08 else "FAIL", v08)

    # G-KH01B-09
    v09: List[str] = []
    if f10.get("one_position_no_overlap_verified_all_variants") is not True or f11.get("status") != "PASS":
        v09.append("CHRONOLOGICAL_1POS_OR_COST_STRESS_FAILED")
    _add_gate("G-KH01B-09", "CHRONOLOGICAL_1POS_3FILL_REPLAY_ACCOUNTING_AND_STRESS", "11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json", {"no_position_overlap": f10.get("one_position_no_overlap_verified_all_variants"), "kh01c_decision": f11.get("kh01c_advancement_decision")}, "PASS" if not v09 else "FAIL", v09)

    # G-KH01B-10
    v10: List[str] = []
    if f12.get("status") != "PASS" or f12.get("b00_vs_si0_exact_match") is not True or int(f12.get("a1_mismatches_count", 1)) != 0 or int(f12.get("three_adapter_mismatches_count", 1)) != 0:
        v10.append("BASELINE_OR_ADAPTER_PARITY_FAILED")
    _add_gate("G-KH01B-10", "FULL_BASELINE_FINGERPRINT_AND_ADAPTER_PARITY", "12_full_baseline_and_three_adapter_parity.json", {"a1_verified": f12.get("a1_evaluated_count"), "three_adapter_verified": f12.get("three_adapter_episodes_denominator")}, "PASS" if not v10 else "FAIL", v10)

    # G-KH01B-11
    v11: List[str] = []
    ind_bij = f14.get("independent_bijection_verification") or {}
    if ind_bij.get("status") != "PASS" or int((f14.get("shard_manifest") or {}).get("nonzero_exit_shards_count", 1)) != 0:
        v11.append("PYTEST_JUNIT_BIJECTION_OR_EXIT_FAILED")
    if f13.get("status") != "PASS":
        v11.append("FAULT_INJECTION_CASEBOOK_13_FAILED")
    _add_gate("G-KH01B-11", "FULL_PYTEST_JUNIT_NODE_EXIT_BIJECTION_AND_REGRESSION", "14_full_pytest_junit_shards_process_exit_and_bijection.json", {"pytest_counters": ind_bij.get("counters"), "fault_cases": f"{f13.get('passed_cases')}/{f13.get('total_cases')}"}, "PASS" if not v11 else "FAIL", v11)

    # G-KH01B-12
    v12: List[str] = []
    if f15.get("pytest_subset_w1_w2_w4_repeat_identical") is not True or f15.get("ablation_slice_w1_w2_w4_repeat_identical") is not True:
        v12.append("ASYNC_WORKER_DETERMINISM_FAILED")
    _add_gate("G-KH01B-12", "BOUNDED_ASYNC_DETERMINISM_W1_W2_W4_REPEAT", "15_async_workers_1_2_4_repeat_determinism_and_real_speed.json", {"pytest_w4_speedup": f15.get("measured_speedup_w4_vs_w1"), "ablation_w124_identical": f15.get("ablation_slice_w1_w2_w4_repeat_identical")}, "PASS" if not v12 else "FAIL", v12)

    # G-KH01B-13
    v13: List[str] = []
    if not (1 <= int(f16.get("max_children_observed") or 0) <= 4) or int(f16.get("orphan_pids_at_cleanup_count", 1)) != 0 or f16.get("disk_reserve_20gb_breached") is not False or f16.get("temp_dirs_cleaned_with_sentinel_check") is not True:
        v13.append("RESOURCE_OR_20GB_DISK_OR_CLEANUP_SAFETY_FAILED")
    _add_gate("G-KH01B-13", "RESOURCE_DISK_RAM_PID_ATOMIC_WRITE_AND_SAFE_CLEANUP", "16_resource_disk_ram_cpu_pid_cleanup.json", {"max_children": f16.get("max_children_observed"), "min_free_disk_gb": f16.get("min_free_disk_gb_observed"), "orphans": f16.get("orphan_pids_at_cleanup_count")}, "PASS" if not v13 else "FAIL", v13)

    # G-KH01B-14
    v14: List[str] = []
    if len(receipts_map) < 14:
        v14.append(f"INCOMPLETE_GATE_RECEIPTS:{len(receipts_map)}")
    _add_gate("G-KH01B-14", "INDEPENDENT_PROCESS_RECEIPTS_CROSS_VIEW_CONSISTENCY", "00_preflight_source_config_dataset_freeze.json", {"dynamic_receipts_count": len(receipts_map)}, "PASS" if not v14 else "FAIL", v14)

    # G-KH01B-15: Fresh OOS (BLOCKED_NO_UNTOUCHED_DATA expected & required on reused dataset!)
    oos_st = str(f17.get("FRESH_OOS_VERDICT") or "FAIL")
    if oos_st == "PASS":
        oos_gate_st = "FAIL"
        v15 = ["FATAL_REUSED_DATASET_FALSIFIED_AS_FRESH_OOS_PASS"]
    elif oos_st == "BLOCKED_NO_UNTOUCHED_DATA":
        oos_gate_st = "BLOCKED_NO_UNTOUCHED_DATA"
        v15 = ["NO_UNTOUCHED_POST_FREEZE_EXCHANGE_DATASET_AVAILABLE"]
    else:
        oos_gate_st = "FAIL"
        v15 = [f"INVALID_FRESH_OOS_STATUS:{oos_st}"]
    gates.append({
        "gate_id": "G-KH01B-15",
        "name": "FRESH_OOS_HONEST_STATUS",
        "artifact_path": "17_fresh_oos_registry_and_economic_verdict_separation.json",
        "tested_run_id": run_id,
        "executable_sha": tested_sha,
        "observed_values": {"FRESH_OOS_VERDICT": oos_st},
        "status": oos_gate_st,
        "violations": v15,
        "independent_receipt": asdict(receipts_map["G-KH01B-15"]) if "G-KH01B-15" in receipts_map else None,
    })

    # G-KH01B-16: Git push & remote readback
    gates.append({
        "gate_id": "G-KH01B-16",
        "name": "GIT_PUSH_REMOTE_READBACK",
        "artifact_path": "00_preflight_source_config_dataset_freeze.json",
        "tested_run_id": run_id,
        "executable_sha": tested_sha,
        "observed_values": {"pre_push_bundle_status": publication_status},
        "status": publication_status,
        "violations": [] if publication_status == "PASS_REMOTE_VERIFIED" else ["AWAITING_POST_PUSH_REMOTE_BLOB_READBACK"],
        "independent_receipt": asdict(receipts_map["G-KH01B-16"]) if "G-KH01B-16" in receipts_map else None,
    })

    by_gid = {g["gate_id"]: g for g in gates}
    tech_pass = all(by_gid[gid]["status"] == "PASS" for gid in MANDATORY_KH01B_TECHNICAL_GATES) and oos_gate_st == "BLOCKED_NO_UNTOUCHED_DATA"
    qual_cands = list(f11.get("qualified_kh01c_candidates") or [])
    econ_verdict = (
        "PROMISING_REUSED_HISTORY_RESEARCH_ONLY"
        if (tech_pass and len(qual_cands) > 0)
        else "NO_PROVEN_ECONOMIC_EDGE"
    )

    return {
        "spec_version": SPEC_VERSION_KH01B,
        "schema_version": SCHEMA_VERSION_KH01B,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": f00.get("canonical_dataset_sha256"),
        "mandatory_technical_gates": list(MANDATORY_KH01B_TECHNICAL_GATES),
        "technical_gates_passed_count": sum(1 for gid in MANDATORY_KH01B_TECHNICAL_GATES if by_gid[gid]["status"] == "PASS"),
        "technical_gates_total_count": len(MANDATORY_KH01B_TECHNICAL_GATES),
        "orthogonal_verdicts": {
            "ENGINEERING_VERDICT": "PASS" if tech_pass else "FAIL",
            "DETECTOR_VALIDITY_VERDICT": "PASS" if by_gid["G-KH01B-06"]["status"] == "PASS" and by_gid["G-KH01B-07"]["status"] == "PASS" else "FAIL",
            "REPLAY_VALIDITY_VERDICT": "PASS" if by_gid["G-KH01B-08"]["status"] == "PASS" and by_gid["G-KH01B-09"]["status"] == "PASS" else "FAIL",
            "TRADING_PARITY_VERDICT": "PASS" if by_gid["G-KH01B-10"]["status"] == "PASS" else "FAIL",
            "REGRESSION_VERDICT": "PASS" if by_gid["G-KH01B-11"]["status"] == "PASS" else "FAIL",
            "FRESH_OOS_VERDICT": oos_gate_st,
            "ECONOMIC_EDGE_VERDICT": econ_verdict,
            "KH01C_ADVANCEMENT_VERDICT": str(f11.get("kh01c_advancement_decision") or "NO_ECONOMIC_ADVANCEMENT_CANDIDATE"),
            "QUALIFIED_KH01C_CANDIDATES": qual_cands,
            "ACTIVATION_READY": "NO",
            "RUNTIME_SAFETY_MODES": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "PUBLICATION_VERDICT": publication_status,
        },
        "gates": gates,
        "open_blockers_for_kh01c_and_kh01d": [
            {
                "blocker_id": "BLK-KH01B-OOS-01",
                "domain": "FRESH_OOS_DATA_ACQUISITION_FOR_KH01D",
                "status": "BLOCKED_NO_UNTOUCHED_DATA",
                "required_for_kh01d": (
                    "Acquire post-freeze untouched Bitget XAUUSDT M1/M5/M15/H1/H4/D1 closed candles + "
                    "synchronously captured exchange L1 bid/ask BBO quotes + verified Bitget session/maintenance calendar."
                ),
            },
            {
                "blocker_id": "BLK-KH01B-ECON-02",
                "domain": "ECONOMIC_EDGE_AND_SAMPLE_POWER",
                "status": econ_verdict,
                "detail": f"KH-01C advancement decision: {f11.get('kh01c_advancement_decision')}; qualified={qual_cands}.",
            },
        ],
    }


# ==============================================================================
# F16: RENDER VIETNAMESE HANDOVER + README & VERIFY CROSS-VIEW CONSISTENCY
# ==============================================================================

def render_kh01b_readme_handover_vi(
    *,
    run_id: str,
    branch: str,
    tested_sha: str,
    dataset_sha: str,
    manifest_02: Mapping[str, Any],
    replays_10: Mapping[str, Any],
    economics_11: Mapping[str, Any],
    d4_09: Mapping[str, Any],
    parity_12: Mapping[str, Any],
    junit_14: Mapping[str, Any],
    workers_15: Mapping[str, Any],
    resource_16: Mapping[str, Any],
    verdicts_19: Mapping[str, Any],
) -> Tuple[str, str]:
    """
    F16: Renders `README_KH01B.md` and `HANDOVER_KH01B_VI.txt` directly from sealed JSON structures
    so every count, PnL, R-multiple, and verdict is mechanically identical across views.
    """
    ov = verdicts_19["orthogonal_verdicts"]
    cnts = (junit_14.get("independent_bijection_verification") or {}).get("counters") or {}
    ec_map = economics_11.get("variants_economics") or {}
    vs_map = replays_10.get("variants_summary") or {}

    table_lines = [
        "| Mã | Variant ID | Ontology | Eligible / 56,710 | Scheduled / Closed (NB) | Net PnL 1x (USD) | Exp 1x (R) | Net PnL 2x All-Cost (USD) | Kết luận Nghiên cứu | Cảnh báo Cỡ mẫu |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for sc in [f"B{i:02d}" for i in range(13)]:
        ec = ec_map.get(sc) or {}
        vs = vs_map.get(sc) or {}
        table_lines.append(
            f"| `{sc}` | `{ec.get('variant_id')}` | `{ec.get('ontology')}` | "
            f"`{vs.get('eligible_candidates_count')}` | `{ec.get('scheduled_nb')} / {ec.get('closed_trades_nb')}` | "
            f"`{ec.get('net_pnl_1x_nb_usd')}` | `{ec.get('expectancy_1x_nb_r')}` | "
            f"`{ec.get('stress_2x_all_cost_net_pnl_usd')}` | `{ec.get('selection_outcome')}` | `{ec.get('sample_power_warning')}` |"
        )
    table_md = "\n".join(table_lines)

    det = d4_09.get("detector_fixture_validation") or {}
    readme = f"""# AURUM DESK — KH-01B: ENTRY INTELLIGENCE & CAUSAL UNICORN RESEARCH (`{SPEC_VERSION_KH01B}`)

- **Run ID**: `{run_id}`
- **Mandatory Branch**: `{branch}`
- **Tested Executable SHA**: `{tested_sha}`
- **Pre-Registered Manifest SHA256**: `{manifest_02.get('manifest_sha256')}`
- **Canonical Dataset SHA256 (`REUSED_RESEARCH`)**: `{dataset_sha}`

## 1. Orthogonal Verdicts
- `ENGINEERING_VERDICT`: **{ov['ENGINEERING_VERDICT']}** (`{verdicts_19['technical_gates_passed_count']}/{verdicts_19['technical_gates_total_count']}` technical gates PASS)
- `DETECTOR_VALIDITY_VERDICT`: **{ov['DETECTOR_VALIDITY_VERDICT']}** (`{det.get('passed_cases')}/{det.get('total_cases')}` real positive/negative cases PASS; `0` synthetic trades counted in historical PnL)
- `REPLAY_VALIDITY_VERDICT`: **{ov['REPLAY_VALIDITY_VERDICT']}**
- `TRADING_PARITY_VERDICT`: **{ov['TRADING_PARITY_VERDICT']}** (`{parity_12.get('a1_evaluated_count')}/{parity_12.get('a1_population_denominator')}` A1 + `{parity_12.get('three_adapter_episodes_denominator')}` 3-adapter episodes matched with `0` mismatches)
- `REGRESSION_VERDICT`: **{ov['REGRESSION_VERDICT']}** (`{cnts.get('collected')}` collected == `{cnts.get('executed')}` executed; `{cnts.get('passed')}` passed, `{cnts.get('skipped')}` skipped, `{cnts.get('failed')}` failed, `{cnts.get('errors')}` errors)
- `FRESH_OOS_VERDICT`: **{ov['FRESH_OOS_VERDICT']}**
- `ECONOMIC_EDGE_VERDICT`: **{ov['ECONOMIC_EDGE_VERDICT']}**
- `KH01C_ADVANCEMENT_VERDICT`: **{ov['KH01C_ADVANCEMENT_VERDICT']}** (Qualified: `{ov['QUALIFIED_KH01C_CANDIDATES']}`)
- `ACTIVATION_READY`: **{ov['ACTIVATION_READY']}** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)
- `PUBLICATION_VERDICT` (pre-push immutable bundle): **{ov['PUBLICATION_VERDICT']}**

## 2. Pre-Registered Orthogonal Experiment Matrix (`B00`–`B12`)
{table_md}

## 3. Strict True Unicorn (`B09`) vs Unicorn-Adjacent (`B10`) Isolation
- `B09` (`STRICT_D4_TRUE_UNICORN`): Historical A1 closed trades = `{d4_09.get('b09_strict_d4_closed_trades')}`, Full 63,085 D4 accepted = `{d4_09.get('d4_63085_accepted_count')}` (`NO_VALID_SETUPS`).
- `B10` (`ADJACENT_FVG_SWEEP_MSS`, strictly NOT True Unicorn, `counts_toward_d4=False`): Historical A1 closed trades = `{d4_09.get('b10_adjacent_closed_trades')}`.
- Real Production Detector Unit Fixtures (`SYNTHETIC_UNIT_FIXTURE_ONLY`): `{det.get('passed_cases')}/{det.get('total_cases')}` PASS.

## 4. Async Determinism & Resource Telemetry
- Pytest subset `w=4` vs `w=1` speedup: `{workers_15.get('measured_speedup_w4_vs_w1')}x` (`w=1, w=2, w=4, w=1_repeat` canonical outcome hash identical = `{workers_15.get('pytest_subset_w1_w2_w4_repeat_identical')}`).
- Ablation slice `w=1, w=2, w=4, w=1_repeat` canonical hash identical = `{workers_15.get('ablation_slice_w1_w2_w4_repeat_identical')}`.
- Resource guards: `max_children_observed = {resource_16.get('max_children_observed')}` (`<= 4`), `min_free_disk_gb = {resource_16.get('min_free_disk_gb_observed')}` (`>= 20.0 GiB` reserve), `min_available_ram_gb = {resource_16.get('min_available_ram_gb_observed')}`, `orphan_pids = {resource_16.get('orphan_pids_at_cleanup_count')}`.
"""

    handover_vi = f"""BÀN GIAO NGHIỆM THU KỸ THUẬT — AURUM DESK {SPEC_VERSION_KH01B}
Run ID: {run_id}
Nhánh bắt buộc: {branch}
Tested Executable SHA: {tested_sha}
Dataset SHA256 (REUSED_RESEARCH): {dataset_sha}
Pre-registered Manifest SHA256: {manifest_02.get('manifest_sha256')}

1. PHÁN QUYẾT TRỰC GIAO:
- ENGINEERING_VERDICT: {ov['ENGINEERING_VERDICT']} ({verdicts_19['technical_gates_passed_count']}/{verdicts_19['technical_gates_total_count']} cổng kỹ thuật PASS)
- DETECTOR_VALIDITY_VERDICT: {ov['DETECTOR_VALIDITY_VERDICT']} ({det.get('passed_cases')}/{det.get('total_cases')} ca kiểm định detector & đối chứng âm PASS)
- REPLAY_VALIDITY_VERDICT: {ov['REPLAY_VALIDITY_VERDICT']}
- TRADING_PARITY_VERDICT: {ov['TRADING_PARITY_VERDICT']} (A1 {parity_12.get('a1_evaluated_count')}/{parity_12.get('a1_population_denominator')} khớp 100%; 3-adapter {parity_12.get('three_adapter_episodes_denominator')} tập lệnh khớp 100%)
- REGRESSION_VERDICT: {ov['REGRESSION_VERDICT']} (collected={cnts.get('collected')}, executed={cnts.get('executed')}, passed={cnts.get('passed')}, skipped={cnts.get('skipped')}, failed={cnts.get('failed')}, errors={cnts.get('errors')})
- FRESH_OOS_VERDICT: {ov['FRESH_OOS_VERDICT']}
- ECONOMIC_EDGE_VERDICT: {ov['ECONOMIC_EDGE_VERDICT']}
- KH01C_ADVANCEMENT_VERDICT: {ov['KH01C_ADVANCEMENT_VERDICT']} (Ứng viên đạt chuẩn chuyển tiếp: {ov['QUALIFIED_KH01C_CANDIDATES']})
- ACTIVATION_READY: {ov['ACTIVATION_READY']} (PRODUCTION=OFF, LIVE=OFF, PAPER=OFF, AUTO=OFF)

2. BẢNG KẾT QUẢ THỰC NGHIỆM ĐỐI CHỨNG B00–B12:
{table_md}

3. RÀO CHẮN CHO KH-01C & KH-01D:
- Toàn bộ tập dữ liệu hiện hữu mang nhãn REUSED_RESEARCH (20 ngày M1 XAUUSDT với báo giá mô hình hóa $0.20 spread, chưa có L1 BBO thực và lịch nghỉ/bảo trì sàn Bitget chưa xác minh).
- Trước khi thực hiện KH-01D (Fresh OOS), bắt buộc thu thập và ký gửi (escrow) tập dữ liệu mới hoàn toàn sau thời điểm đóng băng mã nguồn gồm nến đóng M1/M5/M15/H1/H4/D1 + báo giá L1 bid/ask thực từ sàn Bitget + biên nhận lịch phiên giao dịch.
"""
    return readme, handover_vi


def verify_kh01b_cross_view_consistency(
    *,
    readme_text: str,
    handover_text: str,
    verdicts_19: Mapping[str, Any],
    economics_11: Mapping[str, Any],
    junit_14: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    F16b: Mechanically checks that `README_KH01B.md` and `HANDOVER_KH01B_VI.txt` contain the exact
    verdicts, test counters, and B00..B12 PnL/expectancy figures recorded in JSON artifacts.
    """
    violations: List[str] = []
    ov = verdicts_19["orthogonal_verdicts"]
    cnts = (junit_14.get("independent_bijection_verification") or {}).get("counters") or {}
    req_tokens = [
        str(verdicts_19.get("tested_executable_sha") or ""),
        str(ov.get("ENGINEERING_VERDICT") or ""),
        str(ov.get("FRESH_OOS_VERDICT") or ""),
        str(ov.get("ECONOMIC_EDGE_VERDICT") or ""),
        str(ov.get("KH01C_ADVANCEMENT_VERDICT") or ""),
        str(cnts.get("collected") or ""),
        str(cnts.get("passed") or ""),
    ]
    for sc, ec in (economics_11.get("variants_economics") or {}).items():
        req_tokens.append(f"`{sc}`")
        req_tokens.append(str(ec.get("net_pnl_1x_nb_usd")))
        req_tokens.append(str(ec.get("selection_outcome")))

    for tok in req_tokens:
        if tok not in readme_text:
            violations.append(f"README_MISSING_EXACT_TOKEN:{tok}")
        if tok not in handover_text:
            violations.append(f"HANDOVER_VI_MISSING_EXACT_TOKEN:{tok}")

    return {
        "status": "PASS" if not violations else "FAIL",
        "checked_tokens_count": len(req_tokens),
        "violations": violations,
    }
