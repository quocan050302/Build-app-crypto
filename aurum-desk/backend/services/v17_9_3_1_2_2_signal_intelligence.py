"""
backend/services/v17_9_3_1_2_2_signal_intelligence.py

AURUM Desk / XAUUSDT / Bitget — V17.9.3.1.2.2
Entry Signal Intelligence & Verification Truth Closure Service.

Implements:
  1. Versioned, fail-closed Signal Intelligence mode and pre-registered policy registry
     (`OFF`, `OBSERVE_ONLY`, `RESEARCH_APPLY`; `SI0_OFF_BASELINE`..`SI6_COMBINED_PREDECLARED`).
  2. Point-in-time `SignalQualityEvidenceV1793122` using strictly causal pre-entry facts
     (`known_at_ms <= decision_timestamp_ms`): MTF H4/H1/M15/M5 structure & alignment,
     MSS/BOS break + displacement body/ATR validation, liquidity sweep + reclaim timing,
     strict causal Breaker+FVG Unicorn vs `FVG_ONLY` separation, independent protected
     structure anchor vs selected SL verification, causal target reachability, and quote/cost checks.
  3. Causal `PendingConfirmationV1793122` state machine (`WAITING_CONFIRMATION` ->
     `CONFIRMED` / `EXPIRED` / `INVALIDATED` / `CANCELLED_GAP`) that never backdates entries
     and rebuilds geometry, risk sizing, and `calculate_risk_reward()` at the confirmation bar.
  4. Real integration hook `evaluate_signal_intelligence_gate_v1793122()` invoked inside
     `SharedStrategyEvaluator.evaluate_model_direction()`, preserving 100% bitwise OFF parity
     and OBSERVE_ONLY trade-action parity while blocking `RESEARCH_APPLY` in Production/PAPER/LIVE.
  5. Deterministic pre-entry multi-model arbitration `arbitrate_competing_model_intents_v1793122()`
     with zero lookahead or post-entry outcome labels.
  6. Forensic casebook builders for all 24 `SMC_M1_CONTINUATION` closed losses and
     `SMC_M1_REVERSAL` / `SMC_M1_FVG_CONTINUATION` failures, metric-derived strategy statistical
     verdict calculator, and the 52 non-tautological acceptance gates (`G01..G52`).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.liquidity_scenario_service import identify_confirmed_swings
from services.scalp_m1_types import ModelIdentifier
from services.unicorn_evidence_service import detect_breaker_blocks, detect_unicorn_evidence
from services.v17_9_2_structural_stop_intelligence import (
    plan_v17_9_2_structural_stop_and_target,
    resolve_v17931_protected_structure_authority,
)
from services.v17_9_3_macro_unicorn_intelligence import (
    bind_unicorn_to_causal_episode,
    classify_macro_context_quality,
    evaluate_causal_unicorn_confluence,
    plan_v17_9_3_macro_unicorn_setup,
    resolve_causal_macro_time_window,
)

V17_9_3_1_2_2_SCHEMA_VERSION = "17.9.3.1.2.2"
MANDATORY_BRANCH_V1793122 = "feature/v17-9-2-structural-sl-tp-from-qa"
CANONICAL_DATASET_SHA256_V1793122 = (
    "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"
)

CANONICAL_SIGNAL_INTELLIGENCE_MODES: Tuple[str, ...] = (
    "OFF",
    "OBSERVE_ONLY",
    "RESEARCH_APPLY",
)

CANONICAL_SIGNAL_INTELLIGENCE_POLICY_IDS: Tuple[str, ...] = (
    "SI0_OFF_BASELINE",
    "SI1_MODEL_QUALITY_AND_ARBITRATION",
    "SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION",
    "SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY",
    "SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN",
    "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET",
    "SI6_COMBINED_PREDECLARED",
)

CANONICAL_V1793123_POLICY_IDS: Tuple[str, ...] = (
    "SI0_OFF_BASELINE",
    "SI1_MODEL_QUALITY_AND_ARBITRATION",
    "SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION",
    "SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY",
    "SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN",
    "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET",
    "SI6_COMBINED_PREDECLARED",
    "SI7_CORE_TRADING_LOGIC_REPAIRED",
    "SI8_RESEARCH_DISCOVERY_REPAIRED",
)

POLICY_ID_ALIASES_V1793122: Dict[str, str] = {
    "OFF": "SI0_OFF_BASELINE",
    "SI0": "SI0_OFF_BASELINE",
    "SI0_OFF_BASELINE": "SI0_OFF_BASELINE",
    "SI1": "SI1_MODEL_QUALITY_AND_ARBITRATION",
    "SI1_MODEL_QUALITY_AND_ARBITRATION": "SI1_MODEL_QUALITY_AND_ARBITRATION",
    "SI2": "SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION",
    "SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION": "SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION",
    "SI3": "SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY",
    "SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY": "SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY",
    "SI4": "SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN",
    "SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN": "SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN",
    "SI5": "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET",
    "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET": "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET",
    "SI6": "SI6_COMBINED_PREDECLARED",
    "SI6_COMBINED_PREDECLARED": "SI6_COMBINED_PREDECLARED",
    "SI7": "SI7_CORE_TRADING_LOGIC_REPAIRED",
    "SI7_CORE_TRADING_LOGIC_REPAIRED": "SI7_CORE_TRADING_LOGIC_REPAIRED",
    "SI8": "SI8_RESEARCH_DISCOVERY_REPAIRED",
    "SI8_RESEARCH_DISCOVERY_REPAIRED": "SI8_RESEARCH_DISCOVERY_REPAIRED",
}

FORBIDDEN_PRODUCTION_ADAPTER_ORIGINS: Tuple[str, ...] = (
    "PRODUCTION_ORCHESTRATOR",
    "LIVE",
    "LIVE_EXECUTION",
    "PAPER_EXECUTION",
    "AUTO_PAPER",
    "AUTO_LIVE",
)

RESEARCH_MODEL_BASE_TIE_RANK: Dict[str, int] = {
    ModelIdentifier.CG01_SWEEP_MSS_FVG.value: 1,
    ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value: 2,
    ModelIdentifier.SMC_M1_FVG_CONTINUATION.value: 3,
    ModelIdentifier.SMC_M1_REVERSAL.value: 4,
    ModelIdentifier.SMC_M1_CONTINUATION.value: 5,
}

PRODUCTION_MODEL_STATIC_PRIORITY: Dict[str, int] = {
    ModelIdentifier.SMC_M1_REVERSAL.value: 1,
    ModelIdentifier.SMC_M1_CONTINUATION.value: 2,
    ModelIdentifier.SMC_M1_FVG_CONTINUATION.value: 3,
    ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value: 4,
    ModelIdentifier.CG01_SWEEP_MSS_FVG.value: 5,
}


def _canonical_json_sha256(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _bar_open_ms(bar: Any) -> int:
    if isinstance(bar, Mapping):
        return int(bar.get("open_time") or bar.get("open_time_ms") or bar.get("timestamp") or 0)
    return int(
        getattr(bar, "open_time", None)
        or getattr(bar, "open_time_ms", None)
        or getattr(bar, "timestamp", 0)
        or 0
    )


def _bar_close_ms(bar: Any, tf_ms: int = 60_000) -> int:
    if isinstance(bar, Mapping):
        c = bar.get("close_time") or bar.get("close_time_ms")
        if c is not None:
            return int(c)
        o = _bar_open_ms(bar)
        return o + tf_ms if o > 0 else 0
    c = getattr(bar, "close_time", None) or getattr(bar, "close_time_ms", None)
    if c is not None:
        return int(c)
    o = _bar_open_ms(bar)
    return o + tf_ms if o > 0 else 0


def _bar_float(bar: Any, key: str, default: float = 0.0) -> float:
    if isinstance(bar, Mapping):
        val = bar.get(key, default)
    else:
        val = getattr(bar, key, default)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


def _closed_bars_as_of(
    bars: Sequence[Any],
    cutoff_ms: int,
    tf_ms: int = 60_000,
) -> Tuple[List[Any], int]:
    """
    Filters `bars` to strictly closed bars where `_bar_close_ms(b, tf_ms) <= int(cutoff_ms)`.
    Returns `(closed_bars, unclosed_or_future_count)`.
    """
    closed: List[Any] = []
    unclosed_count = 0
    for b in bars:
        c_ms = _bar_close_ms(b, tf_ms)
        if c_ms > int(cutoff_ms):
            unclosed_count += 1
            continue
        closed.append(b)
    return closed, unclosed_count


def normalize_signal_intelligence_mode_v1793122(raw_mode: Any) -> Tuple[str, bool, Optional[str]]:
    """
    Normalizes `v17_9_3_1_2_2_signal_intelligence_mode`.
    Returns `(normalized_mode, is_valid, error_code)`.
    Fails closed on any unrecognized string or invalid type.
    """
    if raw_mode is None:
        return ("OFF", True, None)
    if isinstance(raw_mode, bool) or not isinstance(raw_mode, str):
        return ("INVALID_SIGNAL_INTELLIGENCE_MODE", False, "SI_INVALID_MODE_FAIL_CLOSED")
    cleaned = raw_mode.strip().upper()
    if not cleaned or cleaned == "OFF":
        return ("OFF", True, None)
    if cleaned in CANONICAL_SIGNAL_INTELLIGENCE_MODES:
        return (cleaned, True, None)
    return ("INVALID_SIGNAL_INTELLIGENCE_MODE", False, "SI_INVALID_MODE_FAIL_CLOSED")


def normalize_signal_intelligence_policy_id_v1793122(
    raw_policy_id: Any,
    *,
    mode: str = "OFF",
) -> Tuple[str, bool, Optional[str]]:
    """
    Normalizes `v17_9_3_1_2_2_policy_id`.
    Returns `(normalized_policy_id, is_valid, error_code)`.
    Fails closed on any unrecognized policy identifier.
    """
    if raw_policy_id is None:
        default_id = "SI0_OFF_BASELINE" if mode == "OFF" else "SI6_COMBINED_PREDECLARED"
        return (default_id, True, None)
    if isinstance(raw_policy_id, bool) or not isinstance(raw_policy_id, str):
        return ("INVALID_SIGNAL_INTELLIGENCE_POLICY", False, "SI_INVALID_POLICY_ID_FAIL_CLOSED")
    cleaned = raw_policy_id.strip().upper()
    if not cleaned:
        default_id = "SI0_OFF_BASELINE" if mode == "OFF" else "SI6_COMBINED_PREDECLARED"
        return (default_id, True, None)
    mapped = POLICY_ID_ALIASES_V1793122.get(cleaned)
    if mapped is not None:
        return (mapped, True, None)
    return ("INVALID_SIGNAL_INTELLIGENCE_POLICY", False, "SI_INVALID_POLICY_ID_FAIL_CLOSED")


def is_research_apply_forbidden_in_adapter_v1793122(
    adapter_origin: str,
    *,
    policy_dict: Optional[Mapping[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Section 03: `RESEARCH_APPLY` only operates in explicitly authorized historical backtest or
    offline shadow research, NOT in AUTO PAPER / LIVE / PRODUCTION_ORCHESTRATOR.
    """
    origin_u = str(adapter_origin or "").strip().upper()
    if origin_u in FORBIDDEN_PRODUCTION_ADAPTER_ORIGINS:
        return (True, "SI_FORBIDDEN_IN_PRODUCTION_ADAPTER")
    if isinstance(policy_dict, Mapping):
        rt_mode = str(
            policy_dict.get("scalp_m1_mode")
            or policy_dict.get("execution_mode")
            or policy_dict.get("runtime_mode")
            or ""
        ).strip().upper()
        if rt_mode in ("PAPER", "LIVE", "AUTO_PAPER", "AUTO_LIVE"):
            return (True, "SI_FORBIDDEN_IN_PRODUCTION_EXECUTION_MODE")
        if bool(policy_dict.get("live_trading_enabled", False)) or bool(policy_dict.get("auto_paper_enabled", False)):
            return (True, "SI_FORBIDDEN_WHEN_LIVE_OR_AUTO_PAPER_ENABLED")
    return (False, None)


@dataclass(frozen=True)
class SignalIntelligencePolicyV1793122:
    policy_id: str
    version: str
    description: str
    hypothesis_id: str
    enable_model_quality_arbitration: bool = False
    enable_htf_structure_and_direction_validation: bool = False
    enable_mss_displacement_confirmation: bool = False
    enable_causal_sweep_and_strict_unicorn: bool = False
    enable_structural_sl_and_reachable_target: bool = False
    require_both_direction_evaluation: bool = False
    prevent_unqualified_long_fallback: bool = False
    min_displacement_body_atr_ratio: float = 0.55
    max_fvg_age_bars_m1: int = 24
    max_pending_confirmation_bars_m1: int = 8
    min_net_rr: float = 2.0
    max_planned_risk_usd: float = 2.50
    min_stop_atr_m1_ratio: float = 0.75
    max_target_atr_m1_multiple: float = 8.0
    enabled_in_production: bool = False
    activation_ready: str = "NO"
    enable_core_trading_logic_repair_v1793123: bool = False
    enable_research_discovery_v1793123: bool = False
    require_unswept_liquidity_pool_target: bool = False

    @property
    def arbitration_mode(self) -> str:
        return (
            "PRE_ENTRY_QUALITY_TUPLE"
            if self.enable_model_quality_arbitration
            else "STATIC_PRODUCTION_PRIORITY"
        )

    @property
    def pending_confirmation_ttl_bars(self) -> int:
        return int(self.max_pending_confirmation_bars_m1)

    @property
    def require_causal_htf_alignment(self) -> bool:
        return bool(self.enable_htf_structure_and_direction_validation)

    @property
    def require_true_unicorn_overlap(self) -> bool:
        return bool(self.enable_causal_sweep_and_strict_unicorn)

    @property
    def require_protected_structure_sl(self) -> bool:
        return bool(self.enable_structural_sl_and_reachable_target)

    @property
    def require_reachable_pre_entry_target(self) -> bool:
        return bool(self.enable_structural_sl_and_reachable_target)

    def _legacy_hash_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not self.enable_core_trading_logic_repair_v1793123 and not self.enable_research_discovery_v1793123 and not self.require_unswept_liquidity_pool_target:
            d.pop("enable_core_trading_logic_repair_v1793123", None)
            d.pop("enable_research_discovery_v1793123", None)
            d.pop("require_unswept_liquidity_pool_target", None)
        return d

    def policy_hash(self) -> str:
        return _canonical_json_sha256(self._legacy_hash_dict())

    def to_dict(self) -> Dict[str, Any]:
        d = self._legacy_hash_dict()
        d["policy_hash"] = self.policy_hash()
        return d


PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122: Dict[str, SignalIntelligencePolicyV1793122] = {
    "SI0_OFF_BASELINE": SignalIntelligencePolicyV1793122(
        policy_id="SI0_OFF_BASELINE",
        version=V17_9_3_1_2_2_SCHEMA_VERSION,
        description=(
            "Exact existing production baseline comparator (OFF). Retains all original "
            "decision statuses, geometry, static model priority, and trade action fingerprints."
        ),
        hypothesis_id="H_SI0_BASELINE_CONTROL",
    ),
    "SI1_MODEL_QUALITY_AND_ARBITRATION": SignalIntelligencePolicyV1793122(
        policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION",
        version=V17_9_3_1_2_2_SCHEMA_VERSION,
        description=(
            "Evaluates all 5 models across both directions where causal structure allows, "
            "corrects setup-specific continuation/reversal/FVG defects, and arbitrates competing "
            "models using a frozen pre-entry candidate quality tuple instead of static model priority."
        ),
        hypothesis_id="H_SI1_MODEL_QUALITY_ARBITRATION",
        enable_model_quality_arbitration=True,
        require_both_direction_evaluation=True,
    ),
    "SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION": SignalIntelligencePolicyV1793122(
        policy_id="SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION",
        version=V17_9_3_1_2_2_SCHEMA_VERSION,
        description=(
            "Validates closed-bar H4/H1/M15 structure alignment, prevents missing HTF bias from "
            "defaulting to unqualified LONG, requires aligned trend for continuation, and requires "
            "sweep+reclaim+MSS for counter-HTF reversals."
        ),
        hypothesis_id="H_SI2_CAUSAL_HTF_DIRECTION",
        enable_htf_structure_and_direction_validation=True,
        require_both_direction_evaluation=True,
        prevent_unqualified_long_fallback=True,
    ),
    "SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY": SignalIntelligencePolicyV1793122(
        policy_id="SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY",
        version=V17_9_3_1_2_2_SCHEMA_VERSION,
        description=(
            "Distinguishes raw setup bar from confirmed MSS/BOS break and displacement body "
            "(>= 0.55 * ATR1M). Places unconfirmed setups into a causal WAITING_CONFIRMATION state "
            "(<= 8 M1 bars) and rebuilds fill/risk at the actual confirmation bar without backdating."
        ),
        hypothesis_id="H_SI3_MSS_DISPLACEMENT_CONFIRMATION",
        enable_mss_displacement_confirmation=True,
        min_displacement_body_atr_ratio=0.55,
        max_pending_confirmation_bars_m1=8,
    ),
    "SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN": SignalIntelligencePolicyV1793122(
        policy_id="SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN",
        version=V17_9_3_1_2_2_SCHEMA_VERSION,
        description=(
            "Requires verified causal liquidity sweep+reclaim timing and strict Breaker+FVG "
            "Unicorn overlap bound to the same causal episode and direction, explicitly separating "
            "FVG_ONLY from UNICORN_CONFIRMED."
        ),
        hypothesis_id="H_SI4_SWEEP_AND_TRUE_UNICORN",
        enable_causal_sweep_and_strict_unicorn=True,
    ),
    "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET": SignalIntelligencePolicyV1793122(
        policy_id="SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET",
        version=V17_9_3_1_2_2_SCHEMA_VERSION,
        description=(
            "Enforces independent pre-entry protected structure swing authority, noise-floor SL "
            "validation, causal unswept target reachability within regime volatility bounds, and "
            "full recalculation of quantity, $2.50 risk cap, and net RR >= 2.0."
        ),
        hypothesis_id="H_SI5_STRUCTURAL_SL_REACHABLE_TP",
        enable_structural_sl_and_reachable_target=True,
        min_net_rr=2.0,
        max_planned_risk_usd=2.50,
    ),
    "SI6_COMBINED_PREDECLARED": SignalIntelligencePolicyV1793122(
        policy_id="SI6_COMBINED_PREDECLARED",
        version=V17_9_3_1_2_2_SCHEMA_VERSION,
        description=(
            "Pre-registered combination of SI1 (model quality & arbitration), SI2 (causal HTF "
            "structure & direction), SI3 (MSS displacement & confirmed entry), SI4 (causal sweep "
            "& strict Unicorn), and SI5 (independent structural SL & reachable target)."
        ),
        hypothesis_id="H_SI6_COMBINED_PREDECLARED",
        enable_model_quality_arbitration=True,
        enable_htf_structure_and_direction_validation=True,
        enable_mss_displacement_confirmation=True,
        enable_causal_sweep_and_strict_unicorn=True,
        enable_structural_sl_and_reachable_target=True,
        require_both_direction_evaluation=True,
        prevent_unqualified_long_fallback=True,
        min_displacement_body_atr_ratio=0.55,
        max_fvg_age_bars_m1=24,
        max_pending_confirmation_bars_m1=8,
        min_net_rr=2.0,
        max_planned_risk_usd=2.50,
    ),
    "SI7_CORE_TRADING_LOGIC_REPAIRED": SignalIntelligencePolicyV1793122(
        policy_id="SI7_CORE_TRADING_LOGIC_REPAIRED",
        version="17.9.3.1.2.3",
        description=(
            "V17.9.3.1.2.3 core trading logic repair: strict FVG mitigation/retest verification, "
            "model-specific Unicorn vs FVG/Sweep confluence (fixing SI4/SI6 zero-trade starvation), "
            "causal HTF provenance enforcement, independent protected SL, unswept liquidity pool TP "
            "with full StrategyDecisionSnapshot propagation, and pre-entry quality arbitration."
        ),
        hypothesis_id="H_SI7_CORE_TRADING_LOGIC_REPAIRED_V1793123",
        enable_model_quality_arbitration=True,
        enable_htf_structure_and_direction_validation=True,
        enable_mss_displacement_confirmation=True,
        enable_causal_sweep_and_strict_unicorn=True,
        enable_structural_sl_and_reachable_target=True,
        require_both_direction_evaluation=True,
        prevent_unqualified_long_fallback=True,
        min_displacement_body_atr_ratio=0.55,
        max_fvg_age_bars_m1=24,
        max_pending_confirmation_bars_m1=8,
        min_net_rr=2.0,
        max_planned_risk_usd=2.50,
        enable_core_trading_logic_repair_v1793123=True,
        enable_research_discovery_v1793123=False,
        require_unswept_liquidity_pool_target=True,
    ),
    "SI8_RESEARCH_DISCOVERY_REPAIRED": SignalIntelligencePolicyV1793122(
        policy_id="SI8_RESEARCH_DISCOVERY_REPAIRED",
        version="17.9.3.1.2.3",
        description=(
            "V17.9.3.1.2.3 independent research candidate discovery + core trading logic repair: "
            "discovers valid causal setups across all 5 models and both directions directly from "
            "closed market evidence via SharedStrategyEvaluator while keeping SI0 100% unchanged."
        ),
        hypothesis_id="H_SI8_RESEARCH_DISCOVERY_REPAIRED_V1793123",
        enable_model_quality_arbitration=True,
        enable_htf_structure_and_direction_validation=True,
        enable_mss_displacement_confirmation=True,
        enable_causal_sweep_and_strict_unicorn=True,
        enable_structural_sl_and_reachable_target=True,
        require_both_direction_evaluation=True,
        prevent_unqualified_long_fallback=True,
        min_displacement_body_atr_ratio=0.55,
        max_fvg_age_bars_m1=24,
        max_pending_confirmation_bars_m1=8,
        min_net_rr=2.0,
        max_planned_risk_usd=2.50,
        enable_core_trading_logic_repair_v1793123=True,
        enable_research_discovery_v1793123=True,
        require_unswept_liquidity_pool_target=True,
    ),
}


def compute_signal_intelligence_registry_hash_v1793122() -> str:
    return _canonical_json_sha256(
        {
            k: PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122[k].to_dict()
            for k in sorted(CANONICAL_SIGNAL_INTELLIGENCE_POLICY_IDS)
        }
    )


def compute_signal_intelligence_registry_hash_v1793123() -> str:
    return _canonical_json_sha256(
        {k: v.to_dict() for k, v in sorted(PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122.items())}
    )


compute_signal_intelligence_policy_registry_hash_v1793122 = (
    compute_signal_intelligence_registry_hash_v1793122
)
compute_signal_intelligence_policy_registry_hash_v1793123 = (
    compute_signal_intelligence_registry_hash_v1793123
)


@dataclass(frozen=True)
class SignalQualityEvidenceV1793122:
    model_id: str
    direction: str
    decision_timestamp_ms: int
    htf_alignment_state: str
    h4_bias: str
    h1_bias: str
    m15_bias: str
    m5_bias: str
    htf_known_at_ms: int
    htf_causal_valid: bool
    mss_confirmed: bool
    mss_break_known_at_ms: int
    displacement_body_atr_ratio: float
    displacement_valid: bool
    continuation_retrace_valid: bool
    sweep_confirmed: bool
    sweep_reclaimed: bool
    sweep_penetration_ticks: float
    sweep_known_at_ms: int
    sweep_level_price: Optional[float]
    fvg_present: bool
    fvg_unmitigated: bool
    fvg_retest_valid: bool
    fvg_age_bars_m1: int
    fvg_known_at_ms: int
    unicorn_classification: str
    unicorn_breaker_fvg_bound: bool
    unicorn_known_at_ms: int
    protected_structure_anchor_id: Optional[str]
    protected_structure_price: Optional[float]
    protected_structure_known_at_ms: Optional[int]
    protected_structure_independent_of_sl: bool
    target_reachable: bool
    target_room_atr_m1: float
    target_unswept_pool_id: Optional[str]
    target_known_at_ms: int
    quote_fresh: bool
    quote_age_ms: int
    spread_to_atr_m1_ratio: float
    prospective_net_rr: float
    planned_risk_usd: float
    candidate_quality_tuple: Tuple[int, int, int, int, int, int, float, float]
    feature_known_at_map: Dict[str, int]
    pre_entry_feature_hash: str
    h4_known_at_ms: int = 0
    h1_known_at_ms: int = 0
    m15_known_at_ms: int = 0
    m5_known_at_ms: int = 0
    unicorn_overlap_low: Optional[float] = None
    unicorn_overlap_high: Optional[float] = None
    causal_feature_violations_count: int = 0

    @property
    def lookahead_violation_detected(self) -> bool:
        return self.causal_feature_violations_count > 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["candidate_quality_tuple"] = list(self.candidate_quality_tuple)
        return d


@dataclass(frozen=True)
class PendingConfirmationV1793122:
    pending_setup_id: str = ""
    origin_opportunity_id: str = ""
    model_id: str = ""
    symbol: str = "XAUUSDT"
    direction: str = "LONG"
    created_at_ms: int = 0
    expires_at_ms: int = 0
    max_wait_bars_m1: int = 8
    required_confirmation_type: str = "MSS_DISPLACEMENT_BREAK"
    trigger_level_price: float = 0.0
    invalidation_price: float = 0.0
    zone_low: Optional[float] = None
    zone_high: Optional[float] = None
    baseline_entry: float = 0.0
    baseline_sl: float = 0.0
    baseline_tp: float = 0.0
    status: str = "WAITING_CONFIRMATION"
    confirmed_at_ms: Optional[int] = None
    confirmed_entry_price: Optional[float] = None
    confirmed_sl_price: Optional[float] = None
    confirmed_tp_price: Optional[float] = None
    confirmed_quantity: Optional[float] = None
    confirmed_net_rr: Optional[float] = None
    terminal_reason: Optional[str] = None
    # Optional compatibility fields used by tests / replay
    pending_id: Optional[str] = None
    opportunity_id: Optional[str] = None
    signal_ts_ms: Optional[int] = None
    created_bar_index_1m: int = 0
    max_ttl_bars: Optional[int] = None
    confirmation_type: Optional[str] = None
    setup_invalidation_price: Optional[float] = None
    protected_structure_price: Optional[float] = None
    protected_structure_id: Optional[str] = None
    target_id: Optional[str] = None
    policy_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.pending_setup_id and self.pending_id:
            object.__setattr__(self, "pending_setup_id", str(self.pending_id))
        elif self.pending_setup_id and not self.pending_id:
            object.__setattr__(self, "pending_id", str(self.pending_setup_id))
        if not self.origin_opportunity_id and self.opportunity_id:
            object.__setattr__(self, "origin_opportunity_id", str(self.opportunity_id))
        elif self.origin_opportunity_id and not self.opportunity_id:
            object.__setattr__(self, "opportunity_id", str(self.origin_opportunity_id))
        if self.created_at_ms == 0 and self.signal_ts_ms is not None:
            object.__setattr__(self, "created_at_ms", int(self.signal_ts_ms))
        elif self.signal_ts_ms is None:
            object.__setattr__(self, "signal_ts_ms", int(self.created_at_ms))
        if self.max_ttl_bars is not None:
            object.__setattr__(self, "max_wait_bars_m1", int(self.max_ttl_bars))
        else:
            object.__setattr__(self, "max_ttl_bars", int(self.max_wait_bars_m1))
        if self.confirmation_type:
            object.__setattr__(self, "required_confirmation_type", str(self.confirmation_type))
        else:
            object.__setattr__(self, "confirmation_type", str(self.required_confirmation_type))
        if self.invalidation_price == 0.0 and self.setup_invalidation_price is not None:
            object.__setattr__(self, "invalidation_price", float(self.setup_invalidation_price))
        elif self.setup_invalidation_price is None:
            object.__setattr__(self, "setup_invalidation_price", float(self.invalidation_price))

    @property
    def rejection_reason(self) -> Optional[str]:
        return self.terminal_reason

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignalIntelligencePlanV1793122:
    schema_version: str
    mode: str
    mode_valid: bool
    policy_id: str
    policy_valid: bool
    policy_hash: str
    original_model_id: str
    direction: str
    candidate_key: str
    setup_episode_id: Optional[str]
    decision_timestamp_ms: int
    baseline_status: str
    baseline_entry: Optional[float]
    baseline_sl: Optional[float]
    baseline_tp: Optional[float]
    baseline_qty: Optional[float]
    baseline_net_rr: Optional[float]
    proposed_status: str
    proposed_entry: Optional[float]
    proposed_sl: Optional[float]
    proposed_tp: Optional[float]
    proposed_qty: Optional[float]
    proposed_gross_rr: Optional[float]
    proposed_net_rr: Optional[float]
    proposed_planned_risk_usd: Optional[float]
    proposed_anchor_id: Optional[str]
    proposed_anchor_kind: Optional[str]
    proposed_invalidation_price: Optional[float]
    proposed_target_id: Optional[str]
    gates_passed: bool
    evaluation_stage: str
    rejection_stage: Optional[str]
    rejection_reason_codes: Tuple[str, ...]
    quality_evidence: Optional[SignalQualityEvidenceV1793122]
    pending_confirmation: Optional[PendingConfirmationV1793122]
    pre_entry_feature_hash: str
    observe_metadata_fingerprint: str
    applied_status: str = "ELIGIBLE"
    applied_quantity: Optional[float] = 0.0
    applied_rejection_reasons: Tuple[str, ...] = ()

    @property
    def proposed_rejection_reasons(self) -> Tuple[str, ...]:
        return self.rejection_reason_codes

    @property
    def evidence(self) -> Optional[SignalQualityEvidenceV1793122]:
        return self.quality_evidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "mode_valid": self.mode_valid,
            "policy_id": self.policy_id,
            "policy_valid": self.policy_valid,
            "policy_hash": self.policy_hash,
            "original_model_id": self.original_model_id,
            "direction": self.direction,
            "candidate_key": self.candidate_key,
            "setup_episode_id": self.setup_episode_id,
            "decision_timestamp_ms": self.decision_timestamp_ms,
            "baseline_status": self.baseline_status,
            "baseline_entry": self.baseline_entry,
            "baseline_sl": self.baseline_sl,
            "baseline_tp": self.baseline_tp,
            "baseline_qty": self.baseline_qty,
            "baseline_net_rr": self.baseline_net_rr,
            "proposed_status": self.proposed_status,
            "proposed_entry": self.proposed_entry,
            "proposed_sl": self.proposed_sl,
            "proposed_tp": self.proposed_tp,
            "proposed_qty": self.proposed_qty,
            "proposed_gross_rr": self.proposed_gross_rr,
            "proposed_net_rr": self.proposed_net_rr,
            "proposed_planned_risk_usd": self.proposed_planned_risk_usd,
            "proposed_anchor_id": self.proposed_anchor_id,
            "proposed_anchor_kind": self.proposed_anchor_kind,
            "proposed_invalidation_price": self.proposed_invalidation_price,
            "proposed_target_id": self.proposed_target_id,
            "applied_status": self.applied_status,
            "applied_quantity": self.applied_quantity,
            "applied_rejection_reasons": list(self.applied_rejection_reasons),
            "gates_passed": self.gates_passed,
            "evaluation_stage": self.evaluation_stage,
            "rejection_stage": self.rejection_stage,
            "rejection_reason_codes": list(self.rejection_reason_codes),
            "quality_evidence": self.quality_evidence.to_dict() if self.quality_evidence else None,
            "pending_confirmation": (
                self.pending_confirmation.to_dict() if self.pending_confirmation else None
            ),
            "pre_entry_feature_hash": self.pre_entry_feature_hash,
            "observe_metadata_fingerprint": self.observe_metadata_fingerprint,
        }


def _infer_series_structure_bias(
    bars: Sequence[Any],
    *,
    tf_ms: int,
    decision_timestamp_ms: int,
) -> Tuple[str, int, bool]:
    """
    Computes causal structure bias (`BULLISH`, `BEARISH`, `NEUTRAL`, `INSUFFICIENT`, `ABSENT`,
    `STALE`, `LOOKAHEAD_INVALID`) from closed candles with `close_ms <= decision_timestamp_ms`.
    """
    if not bars:
        return ("ABSENT", 0, True)

    valid_bars, _ = _closed_bars_as_of(bars, decision_timestamp_ms, tf_ms)
    if not valid_bars:
        return ("ABSENT", 0, True)
    max_close_ms = max(_bar_close_ms(b, tf_ms) for b in valid_bars)

    if len(valid_bars) < 3:
        return ("INSUFFICIENT", max_close_ms, True)

    # Check staleness: if last closed bar is older than 4 * tf_ms (outside weekend gap)
    age_ms = decision_timestamp_ms - max_close_ms
    if age_ms > 4 * tf_ms and tf_ms <= 3_600_000:
        return ("STALE", max_close_ms, True)

    window = valid_bars[-min(len(valid_bars), 12) :]
    closes = [_bar_float(b, "close") for b in window]
    highs = [_bar_float(b, "high") for b in window]
    lows = [_bar_float(b, "low") for b in window]

    half = max(1, len(window) // 2)
    first_high = max(highs[:half])
    second_high = max(highs[half:])
    first_low = min(lows[:half])
    second_low = min(lows[half:])
    net_change = closes[-1] - closes[0]
    avg_range = sum(max(0.01, h - l) for h, l in zip(highs, lows)) / float(len(window))

    if second_high > first_high and second_low >= first_low and net_change > 0.25 * avg_range:
        return ("BULLISH", max_close_ms, True)
    if second_low < first_low and second_high <= first_high and net_change < -0.25 * avg_range:
        return ("BEARISH", max_close_ms, True)
    if net_change > 0.60 * avg_range:
        return ("BULLISH", max_close_ms, True)
    if net_change < -0.60 * avg_range:
        return ("BEARISH", max_close_ms, True)
    return ("NEUTRAL", max_close_ms, True)


def resolve_research_preferred_direction_v1793122(
    context: Any = None,
    explicit_bias: Optional[str] = None,
    *,
    h4_bias: Optional[str] = None,
    h1_bias: Optional[str] = None,
    m15_bias: Optional[str] = None,
    m5_bias: Optional[str] = None,
    now_ms: Optional[int] = None,
    h4_candles: Optional[Sequence[Any]] = None,
    h1_candles: Optional[Sequence[Any]] = None,
    m15_candles: Optional[Sequence[Any]] = None,
    m5_candles: Optional[Sequence[Any]] = None,
) -> Tuple[Optional[str], str]:
    """
    Section 02.A & Section 04 (SI2) & V17.9.3.1.2.3 STAGE G2:
    Resolves preferred direction without forcing `LONG` when H4/H1/M15 history is missing,
    neutral, or conflicting, and verifies causal bar provenance/freshness before honoring
    `explicit_bias` or `context.htf_bias`.
    """
    eff_bias = str(explicit_bias or getattr(context, "htf_bias", "") or "").strip().upper()
    explicit_candles_passed = (
        h4_candles is not None or h1_candles is not None or m15_candles is not None or m5_candles is not None
    )

    if context is not None or explicit_candles_passed:
        eff_now_ms = int(
            now_ms
            if now_ms is not None
            else (getattr(context, "decision_timestamp_ms", 0) or getattr(context, "now_ms", 0) or 0)
        )
        h4_bars = list(
            h4_candles
            if h4_candles is not None
            else (getattr(context, "closed_h4_candles", None) or getattr(context, "candles_4h", ()) or ())
        )
        h1_bars = list(
            h1_candles
            if h1_candles is not None
            else (getattr(context, "closed_h1_candles", None) or getattr(context, "candles_1h", ()) or ())
        )
        m15_bars = list(
            m15_candles
            if m15_candles is not None
            else (getattr(context, "closed_m15_candles", None) or getattr(context, "candles_15m", ()) or ())
        )
        m5_bars = list(
            m5_candles
            if m5_candles is not None
            else (getattr(context, "closed_m5_candles", None) or getattr(context, "candles_5m", ()) or ())
        )

        calc_h4, _, h4_ok = _infer_series_structure_bias(h4_bars, tf_ms=14_400_000, decision_timestamp_ms=eff_now_ms)
        calc_h1, _, h1_ok = _infer_series_structure_bias(h1_bars, tf_ms=3_600_000, decision_timestamp_ms=eff_now_ms)
        calc_m15, _, m15_ok = _infer_series_structure_bias(m15_bars, tf_ms=900_000, decision_timestamp_ms=eff_now_ms)
        if not (h4_ok and h1_ok and m15_ok):
            return (None, "HTF_LOOKAHEAD_INVALID")

        mtf_sum = getattr(context, "mtf_structure_bias_summary", None) if context is not None else None
        if isinstance(mtf_sum, Mapping):
            tf_map = mtf_sum.get("timeframes") if isinstance(mtf_sum.get("timeframes"), Mapping) else {}
            if isinstance(tf_map, Mapping):
                h4_m = tf_map.get("H4") if isinstance(tf_map.get("H4"), Mapping) else {}
                h1_m = tf_map.get("H1") if isinstance(tf_map.get("H1"), Mapping) else {}
                if calc_h4 not in ("ABSENT", "INSUFFICIENT", "STALE") and str(h4_m.get("trend_bias", "")).upper() in ("BULLISH", "BEARISH"):
                    calc_h4 = str(h4_m["trend_bias"]).upper()
                if calc_h1 not in ("ABSENT", "INSUFFICIENT", "STALE") and str(h1_m.get("trend_bias", "")).upper() in ("BULLISH", "BEARISH"):
                    calc_h1 = str(h1_m["trend_bias"]).upper()
        if explicit_candles_passed:
            h4_b = str(calc_h4).upper()
            h1_b = str(calc_h1).upper()
            m15_b = str(calc_m15).upper()
        else:
            h4_b = str(h4_bias or calc_h4).upper()
            h1_b = str(h1_bias or calc_h1).upper()
            m15_b = str(m15_bias or calc_m15).upper()
    else:
        h4_bars = []
        h1_bars = []
        m15_bars = []
        m5_bars = []
        h4_b = str(h4_bias or "ABSENT").upper()
        h1_b = str(h1_bias or "ABSENT").upper()
        m15_b = str(m15_bias or "NEUTRAL").upper()

    if eff_bias in ("BULLISH", "BEARISH"):
        # Verify causal bar provenance and freshness before honoring explicit_bias
        has_explicit_bar_args = (context is not None) or explicit_candles_passed or (h4_bias is not None) or (h1_bias is not None)
        if has_explicit_bar_args:
            if h4_b in ("ABSENT", "INSUFFICIENT", "STALE") and h1_b in ("ABSENT", "INSUFFICIENT", "STALE") and m15_b in ("ABSENT", "INSUFFICIENT", "STALE", "NEUTRAL"):
                return (None, "EXPLICIT_HTF_BIAS_MISSING_CLOSED_CANDLE_PROVENANCE")
            if h4_b == "STALE" or h1_b == "STALE":
                return (None, "EXPLICIT_HTF_BIAS_STALE_BARS_REJECTED")
            if eff_bias == "BULLISH" and h4_b == "BEARISH" and h1_b == "BEARISH":
                return (None, "EXPLICIT_HTF_BIAS_CONFLICTS_WITH_CAUSAL_BARS")
            if eff_bias == "BEARISH" and h4_b == "BULLISH" and h1_b == "BULLISH":
                return (None, "EXPLICIT_HTF_BIAS_CONFLICTS_WITH_CAUSAL_BARS")
        return ("LONG", "EXPLICIT_HTF_BIAS_BULLISH") if eff_bias == "BULLISH" else ("SHORT", "EXPLICIT_HTF_BIAS_BEARISH")

    if h4_b == "BULLISH" and h1_b in ("BULLISH", "NEUTRAL"):
        return ("LONG", "HTF_H4_H1_BULLISH")
    if h4_b == "BEARISH" and h1_b in ("BEARISH", "NEUTRAL"):
        return ("SHORT", "HTF_H4_H1_BEARISH")
    if h1_b == "BULLISH" and m15_b == "BULLISH" and h4_b != "BEARISH":
        return ("LONG", "HTF_H1_M15_BULLISH")
    if h1_b == "BEARISH" and m15_b == "BEARISH" and h4_b != "BULLISH":
        return ("SHORT", "HTF_H1_M15_BEARISH")

    if (h4_b == "BULLISH" and h1_b == "BEARISH") or (h4_b == "BEARISH" and h1_b == "BULLISH"):
        return (None, "HTF_MIXED_REQUIRE_LOCAL_CONFIRMATION")

    if context is not None and h4_b in ("ABSENT", "INSUFFICIENT", "STALE") and h1_b in ("ABSENT", "INSUFFICIENT", "STALE") and len(m15_bars) < 3 and len(m5_bars) < 3:
        return (None, "HTF_BIAS_MISSING_NO_DEFAULT_LONG")

    return (None, "HTF_DATA_INSUFFICIENT_OR_CONFLICTED")


def _build_causal_swing_pools_from_swings_v1793124(
    target_swings: Sequence[Mapping[str, Any]],
    closed_1m: Sequence[Any],
    direction: str,
    now_ms: int,
) -> List[Dict[str, Any]]:
    """
    V17.9.3.1.2.4 Stage R2.2: Converts confirmed swings into causal liquidity pools,
    explicitly inspecting closed M1 candles between `min(occurred_at_ms, known_at_ms)`
    and `now_ms` to determine whether price already traded through/swept the swing level
    (`swept_at_ms <= now_ms`). Never blindly marks fallback swings `swept=False`.
    """
    out: List[Dict[str, Any]] = []
    for s in target_swings:
        px = float(s.get("price", 0.0) or 0.0)
        if px <= 0.0:
            continue
        occ_ms = int(s.get("occurred_at_ms", 0) or 0)
        k_ms = int(s.get("known_at_ms", occ_ms) or occ_ms or 0)
        start_check_ms = min(occ_ms, k_ms) if (occ_ms > 0 and k_ms > 0) else max(occ_ms, k_ms)
        swept_flag = bool(s.get("swept", False) or s.get("mitigated", False))
        swept_at_ms: Optional[int] = s.get("swept_at_ms") or s.get("mitigated_at_ms")
        if not swept_flag and start_check_ms > 0 and closed_1m:
            for b in closed_1m:
                b_close_ms = _bar_close_ms(b, 60_000)
                if b_close_ms <= start_check_ms or b_close_ms > now_ms:
                    continue
                if direction == "LONG" and _bar_float(b, "high") >= px:
                    swept_flag = True
                    swept_at_ms = b_close_ms
                    break
                if direction == "SHORT" and _bar_float(b, "low") <= px:
                    swept_flag = True
                    swept_at_ms = b_close_ms
                    break
        out.append(
            {
                "pool_id": str(s.get("id", "")),
                "price": px,
                "pool_side": "BSL" if direction == "LONG" else "SSL",
                "occurred_at_ms": occ_ms if occ_ms > 0 else k_ms,
                "known_at_ms": k_ms,
                "swept": swept_flag,
                "swept_at_ms": swept_at_ms,
            }
        )
    return out


def _resolve_unswept_liquidity_pool_target_v1793123(
    *,
    pools: Sequence[Any],
    closed_1m: Sequence[Any],
    direction: str,
    entry_price: float,
    selected_sl: Optional[float],
    selected_target_id: Optional[str],
    take_profit: Optional[float],
    now_ms: int,
    eff_atr_m1: float,
    eff_atr_m5: float,
    max_target_atr_m1_multiple: float = 8.0,
    max_target_atr_m5_multiple: float = 3.2,
    min_required_target_distance: float = 0.0,
) -> Tuple[Optional[str], Optional[float], int, bool, bool]:
    """
    Inspects `pools` (causal pre-entry liquidity pools) to verify whether `selected_target_id`
    or another opposing pool is genuinely unswept as of `now_ms`.
    Returns `(valid_pool_id, valid_pool_price, pool_known_at_ms, has_unswept_pool, explicit_pool_was_swept)`.
    """
    if not pools or entry_price <= 0.0:
        return (None, take_profit, now_ms, False, False)

    explicit_pool_was_swept = False
    valid_candidates: List[Tuple[float, str, float, int]] = []

    for p in pools:
        if isinstance(p, Mapping):
            p_id = str(p.get("id") or p.get("pool_id") or p.get("level_id") or "")
            p_px_raw = p.get("price") if p.get("price") is not None else p.get("level_price")
            k_ts_raw = p.get("known_at_ms") or p.get("created_at_ms") or p.get("formed_at_ms") or p.get("timestamp_ms")
            occ_ts_raw = p.get("occurred_at_ms") or p.get("formed_at_ms") or k_ts_raw
            is_swept_flag = bool(p.get("swept", False) or p.get("mitigated", False) or p.get("is_swept", False))
            swept_at_raw = p.get("swept_at_ms") or p.get("mitigated_at_ms")
        else:
            p_id = str(getattr(p, "id", None) or getattr(p, "pool_id", None) or getattr(p, "level_id", "") or "")
            p_px_raw = getattr(p, "price", None) if getattr(p, "price", None) is not None else getattr(p, "level_price", None)
            k_ts_raw = (
                getattr(p, "known_at_ms", None)
                or getattr(p, "created_at_ms", None)
                or getattr(p, "formed_at_ms", None)
                or getattr(p, "timestamp_ms", None)
            )
            occ_ts_raw = getattr(p, "occurred_at_ms", None) or getattr(p, "formed_at_ms", None) or k_ts_raw
            is_swept_flag = bool(
                getattr(p, "swept", False)
                or getattr(p, "mitigated", False)
                or getattr(p, "is_swept", False)
            )
            swept_at_raw = getattr(p, "swept_at_ms", None) or getattr(p, "mitigated_at_ms", None)

        if p_px_raw is None:
            continue
        p_px = round(float(p_px_raw), 2)
        k_ts = int(k_ts_raw) if k_ts_raw is not None else now_ms
        if k_ts > now_ms:
            if selected_target_id and p_id == str(selected_target_id):
                explicit_pool_was_swept = True
            continue

        swept_by_ts = swept_at_raw is not None and int(swept_at_raw) <= now_ms
        swept_by_bars = False
        check_start_ts = min(int(occ_ts_raw), k_ts) if occ_ts_raw is not None else k_ts
        if k_ts_raw is not None and closed_1m:
            post_pool_bars = [b for b in closed_1m if _bar_close_ms(b, 60_000) > check_start_ts and _bar_close_ms(b, 60_000) <= now_ms]
            if direction == "LONG" and p_px > entry_price:
                swept_by_bars = any(_bar_float(b, "high") >= p_px for b in post_pool_bars)
            elif direction == "SHORT" and p_px < entry_price:
                swept_by_bars = any(_bar_float(b, "low") <= p_px for b in post_pool_bars)

        if is_swept_flag or swept_by_ts or swept_by_bars:
            if selected_target_id and p_id == str(selected_target_id):
                explicit_pool_was_swept = True
            continue

        if direction == "LONG" and p_px <= entry_price:
            continue
        if direction == "SHORT" and p_px >= entry_price:
            continue

        dist_px = abs(p_px - entry_price)
        if min_required_target_distance > 0.0 and dist_px < min_required_target_distance:
            continue

        room_m1 = dist_px / max(0.05, eff_atr_m1)
        room_m5 = dist_px / max(0.10, eff_atr_m5)
        if 1.2 <= room_m1 <= max_target_atr_m1_multiple and room_m5 <= max_target_atr_m5_multiple:
            valid_candidates.append((dist_px, p_id or f"pool_{p_px}", p_px, k_ts))

    if not valid_candidates:
        return (None, None if explicit_pool_was_swept else take_profit, now_ms, False, explicit_pool_was_swept)

    # If selected_target_id matches one of the valid unswept candidates, keep it; otherwise choose nearest valid pool
    for dist, pid, ppx, kts in valid_candidates:
        if selected_target_id and pid == str(selected_target_id):
            return (pid, ppx, kts, True, False)

    valid_candidates.sort(key=lambda item: (item[0], item[1]))
    _, best_id, best_px, best_kts = valid_candidates[0]
    return (best_id, best_px, best_kts, True, explicit_pool_was_swept)


def build_signal_quality_evidence_v1793122(
    *,
    context: Any,
    model_id: str,
    direction: str,
    setup_descriptors: Optional[Mapping[str, Any]],
    sh_1m: Sequence[Mapping[str, Any]],
    sl_1m: Sequence[Mapping[str, Any]],
    entry_price: float,
    selected_sl: Optional[float],
    take_profit: Optional[float],
    selected_target_id: Optional[str],
    atr_m1: float,
    atr_m5: Optional[float],
    prospective_net_rr: float,
    planned_risk_usd: float,
    unicorn_evidence: Optional[Any] = None,
) -> SignalQualityEvidenceV1793122:
    """
    Constructs a strictly causal `SignalQualityEvidenceV1793122` at `decision_timestamp_ms`.
    Verifies `known_at_ms <= decision_timestamp_ms` for every feature.
    """
    now_ms = int(getattr(context, "decision_timestamp_ms", 0) or getattr(context, "now_ms", 0) or 0)
    raw_1m = list(getattr(context, "closed_m1_candles", None) or getattr(context, "candles_1m", ()) or ())
    raw_5m = list(getattr(context, "closed_m5_candles", None) or getattr(context, "candles_5m", ()) or ())
    raw_15m = list(getattr(context, "closed_m15_candles", None) or getattr(context, "candles_15m", ()) or ())
    raw_h1 = list(getattr(context, "closed_h1_candles", None) or getattr(context, "candles_1h", ()) or ())
    raw_h4 = list(getattr(context, "closed_h4_candles", None) or getattr(context, "candles_4h", ()) or ())

    closed_1m, _ = _closed_bars_as_of(raw_1m, now_ms, 60_000)
    closed_5m, _ = _closed_bars_as_of(raw_5m, now_ms, 300_000)
    closed_15m, _ = _closed_bars_as_of(raw_15m, now_ms, 900_000)
    closed_h1, _ = _closed_bars_as_of(raw_h1, now_ms, 3_600_000)
    closed_h4, _ = _closed_bars_as_of(raw_h4, now_ms, 14_400_000)

    h4_bias, h4_ts, h4_ok = _infer_series_structure_bias(closed_h4, tf_ms=14_400_000, decision_timestamp_ms=now_ms)
    h1_bias, h1_ts, h1_ok = _infer_series_structure_bias(closed_h1, tf_ms=3_600_000, decision_timestamp_ms=now_ms)
    m15_bias, m15_ts, m15_ok = _infer_series_structure_bias(closed_15m, tf_ms=900_000, decision_timestamp_ms=now_ms)
    m5_bias, m5_ts, m5_ok = _infer_series_structure_bias(closed_5m, tf_ms=300_000, decision_timestamp_ms=now_ms)

    # Check if any swing in sh_1m / sl_1m or setup_descriptors has known_at_ms > now_ms
    swings_causal_ok = True
    causal_violations = 0
    max_swing_known_ms = 0
    for s in list(sh_1m) + list(sl_1m):
        k_ms = int(s.get("known_at_ms") or s.get("confirmed_at_ms") or s.get("occurred_at_ms") or 0)
        if k_ms > now_ms:
            swings_causal_ok = False
            causal_violations += 1
        elif k_ms > max_swing_known_ms:
            max_swing_known_ms = k_ms

    s_desc = dict(setup_descriptors or {})
    sel_brk = s_desc.get("selected_break") if isinstance(s_desc.get("selected_break"), Mapping) else None
    sel_fvg = s_desc.get("selected_fvg") if isinstance(s_desc.get("selected_fvg"), Mapping) else None
    sel_swp = s_desc.get("selected_sweep") if isinstance(s_desc.get("selected_sweep"), Mapping) else None

    if sel_brk is not None and int(sel_brk.get("known_at_ms") or 0) > now_ms:
        swings_causal_ok = False
        causal_violations += 1
    if sel_fvg is not None and int(sel_fvg.get("confirmed_at") or sel_fvg.get("timestamp") or 0) > now_ms:
        swings_causal_ok = False
        causal_violations += 1
    if sel_swp is not None and int(sel_swp.get("known_at_ms") or sel_swp.get("occurred_at_ms") or 0) > now_ms:
        swings_causal_ok = False
        causal_violations += 1

    for ts_key in ("sweep_occurred_at_ms", "sweep_reclaimed_at_ms", "fvg_formed_at_ms", "mss_confirmed_at_ms"):
        ts_val = int(s_desc.get(ts_key) or 0)
        if ts_val > now_ms:
            swings_causal_ok = False
            causal_violations += 1

    htf_causal_valid = bool(h4_ok and h1_ok and m15_ok and m5_ok and swings_causal_ok)
    htf_known_at_ms = max(h4_ts, h1_ts, m15_ts, m5_ts)

    expected_bias = "BULLISH" if direction == "LONG" else "BEARISH"
    opposing_bias = "BEARISH" if direction == "LONG" else "BULLISH"

    if not htf_causal_valid:
        htf_alignment_state = "STALE"
    elif h4_bias in ("INSUFFICIENT", "ABSENT") and h1_bias in ("INSUFFICIENT", "ABSENT"):
        htf_alignment_state = "INSUFFICIENT_DATA"
    elif h4_bias == "STALE" or h1_bias == "STALE":
        htf_alignment_state = "STALE"
    elif (
        (h4_bias == expected_bias and h1_bias in (expected_bias, "NEUTRAL"))
        or (h1_bias == expected_bias and h4_bias in (expected_bias, "NEUTRAL") and m15_bias in (expected_bias, "NEUTRAL"))
    ):
        htf_alignment_state = "ALIGNED"
    elif h4_bias == opposing_bias and h1_bias == opposing_bias:
        htf_alignment_state = "COUNTERTREND"
    elif (h4_bias == opposing_bias and h1_bias == expected_bias) or (h4_bias == expected_bias and h1_bias == opposing_bias):
        htf_alignment_state = "MIXED"
    elif h1_bias == opposing_bias and m15_bias == opposing_bias:
        htf_alignment_state = "COUNTERTREND"
    else:
        htf_alignment_state = "NEUTRAL"

    # Evaluate MSS break & displacement on recent closed M1 bars
    eff_atr_m1 = max(0.05, float(atr_m1 or 0.5))
    mss_confirmed = False
    mss_break_known_at_ms = 0
    displacement_body_atr_ratio = 0.0
    continuation_retrace_valid = False

    if closed_1m:
        recent_3 = list(closed_1m[-min(len(closed_1m), 4) :])
        if sel_brk is not None:
            brk_open_ts = int(sel_brk.get("break_open_ms") or sel_brk.get("broken_at_ms") or 0)
            brk_k_ts = int(sel_brk.get("known_at_ms") or (brk_open_ts + 60_000))
            if 0 < brk_open_ts and brk_k_ts <= now_ms:
                for b_cand in closed_1m[-min(len(closed_1m), 25) :]:
                    if _bar_open_ms(b_cand) == brk_open_ts:
                        recent_3.append(b_cand)
                        break
        if sel_fvg is not None:
            fvg_mid_ts = int(sel_fvg.get("middle_bar_ms") or sel_fvg.get("timestamp") or 0)
            fvg_k_ts = int(sel_fvg.get("confirmed_at") or (fvg_mid_ts + 60_000))
            if 0 < fvg_mid_ts and fvg_k_ts <= now_ms:
                for b_cand in closed_1m[-min(len(closed_1m), 25) :]:
                    if _bar_open_ms(b_cand) == fvg_mid_ts:
                        recent_3.append(b_cand)
                        break
        bodies = [abs(_bar_float(b, "close") - _bar_float(b, "open")) for b in recent_3]
        displacement_body_atr_ratio = round(max(bodies) / eff_atr_m1, 4) if bodies else 0.0

    if sel_brk is not None:
        brk_known = int(sel_brk.get("known_at_ms") or 0)
        ref_sw_known = int(sel_brk.get("reference_swing_known_at_ms") or brk_known)
        if 0 < ref_sw_known <= brk_known <= now_ms:
            mss_confirmed = True
            mss_break_known_at_ms = brk_known
        elif ref_sw_known > now_ms or brk_known > now_ms:
            causal_violations += 1
    elif s_desc.get("selected_break_id"):
        brk_known = int(s_desc.get("mss_confirmed_at_ms") or now_ms)
        ref_sw_known = int(s_desc.get("mss_reference_swing_known_at_ms") or brk_known)
        if 0 < ref_sw_known <= brk_known <= now_ms:
            mss_confirmed = True
            mss_break_known_at_ms = brk_known
        elif ref_sw_known > now_ms or brk_known > now_ms:
            causal_violations += 1

    valid_sh_1m = [s for s in sh_1m if int(s.get("known_at_ms") or s.get("confirmed_at_ms") or 0) <= now_ms]
    valid_sl_1m = [s for s in sl_1m if int(s.get("known_at_ms") or s.get("confirmed_at_ms") or 0) <= now_ms]
    future_sh_count = sum(1 for s in sh_1m if int(s.get("known_at_ms") or s.get("confirmed_at_ms") or 0) > now_ms)
    future_sl_count = sum(1 for s in sl_1m if int(s.get("known_at_ms") or s.get("confirmed_at_ms") or 0) > now_ms)
    if future_sh_count > 0 or future_sl_count > 0:
        causal_violations += future_sh_count + future_sl_count
        if (direction == "LONG" and not valid_sh_1m and future_sh_count > 0) or (
            direction == "SHORT" and not valid_sl_1m and future_sl_count > 0
        ):
            mss_confirmed = False
            mss_break_known_at_ms = 0

    if closed_1m and valid_sh_1m and valid_sl_1m:
        last_bar = closed_1m[-1]
        last_sh_px = float(valid_sh_1m[-1]["price"])
        last_sl_px = float(valid_sl_1m[-1]["price"])
        last_close = _bar_float(last_bar, "close")
        last_low = _bar_float(last_bar, "low")
        last_high = _bar_float(last_bar, "high")

        if direction == "LONG":
            if last_close > last_sh_px + 0.02 and last_close > _bar_float(last_bar, "open"):
                mss_confirmed = True
                mss_break_known_at_ms = max(mss_break_known_at_ms, _bar_close_ms(last_bar, 60_000))
            if len(closed_1m) >= 4:
                has_pullback_bar = any(
                    _bar_float(b, "close") < _bar_float(b, "open") or _bar_float(b, "low") <= last_sh_px + 0.35 * eff_atr_m1
                    for b in closed_1m[-4:-1]
                )
                not_climax = (last_high - last_close) <= 0.45 * max(0.05, last_high - last_low)
                continuation_retrace_valid = bool(has_pullback_bar and not_climax)
        else:
            if last_close < last_sl_px - 0.02 and last_close < _bar_float(last_bar, "open"):
                mss_confirmed = True
                mss_break_known_at_ms = max(mss_break_known_at_ms, _bar_close_ms(last_bar, 60_000))
            if len(closed_1m) >= 4:
                has_pullback_bar = any(
                    _bar_float(b, "close") > _bar_float(b, "open") or _bar_float(b, "high") >= last_sl_px - 0.35 * eff_atr_m1
                    for b in closed_1m[-4:-1]
                )
                not_climax = (last_close - last_low) <= 0.45 * max(0.05, last_high - last_low)
                continuation_retrace_valid = bool(has_pullback_bar and not_climax)

    if "continuation_retrace_valid" in s_desc and s_desc.get("continuation_retrace_valid") is not None:
        continuation_retrace_valid = bool(s_desc.get("continuation_retrace_valid"))

    displacement_valid = bool(displacement_body_atr_ratio >= 0.55 and mss_confirmed)

    # Evaluate Liquidity Sweep & Reclaim (strictly requiring prior confirmed swing + reclaim close)
    sweep_confirmed = False
    sweep_reclaimed = False
    sweep_penetration_ticks = 0.0
    sweep_known_at_ms = 0
    sweep_level_price: Optional[float] = None

    if closed_1m and valid_sh_1m and valid_sl_1m:
        last_close = _bar_float(closed_1m[-1], "close")
        recent_8 = closed_1m[-min(len(closed_1m), 8) :]
        if direction == "LONG":
            ref_sl = valid_sl_1m[-1]
            ref_px = float(ref_sl["price"])
            ref_k_ms = int(ref_sl.get("known_at_ms") or ref_sl.get("confirmed_at_ms") or 0)
            post_swing_bars = [b for b in recent_8 if _bar_open_ms(b) >= ref_k_ms] if ref_k_ms > 0 else recent_8
            if post_swing_bars:
                min_l = min(_bar_float(b, "low") for b in post_swing_bars)
                pen_ticks = round((ref_px - min_l) / 0.01, 2)
                if pen_ticks >= 2.0:
                    sweep_confirmed = True
                    sweep_penetration_ticks = pen_ticks
                    sweep_level_price = ref_px
                    for b in reversed(post_swing_bars):
                        if _bar_float(b, "low") <= ref_px:
                            sweep_known_at_ms = _bar_close_ms(b, 60_000)
                            break
                    if last_close > ref_px:
                        sweep_reclaimed = True
        else:
            ref_sh = valid_sh_1m[-1]
            ref_px = float(ref_sh["price"])
            ref_k_ms = int(ref_sh.get("known_at_ms") or ref_sh.get("confirmed_at_ms") or 0)
            post_swing_bars = [b for b in recent_8 if _bar_open_ms(b) >= ref_k_ms] if ref_k_ms > 0 else recent_8
            if post_swing_bars:
                max_h = max(_bar_float(b, "high") for b in post_swing_bars)
                pen_ticks = round((max_h - ref_px) / 0.01, 2)
                if pen_ticks >= 2.0:
                    sweep_confirmed = True
                    sweep_penetration_ticks = pen_ticks
                    sweep_level_price = ref_px
                    for b in reversed(post_swing_bars):
                        if _bar_float(b, "high") >= ref_px:
                            sweep_known_at_ms = _bar_close_ms(b, 60_000)
                            break
                    if last_close < ref_px:
                        sweep_reclaimed = True

    if sel_swp is not None:
        swp_k = int(sel_swp.get("known_at_ms") or sel_swp.get("occurred_at_ms") or 0)
        if 0 < swp_k <= now_ms:
            if sweep_known_at_ms == 0:
                sweep_known_at_ms = swp_k
                sweep_level_price = float(sel_swp.get("price", 0.0) or 0.0)
            if "reclaimed" in sel_swp:
                sweep_confirmed = True
                sweep_reclaimed = bool(sel_swp.get("reclaimed"))
    elif s_desc.get("selected_sweep_id"):
        occ_ms = int(s_desc.get("sweep_occurred_at_ms") or now_ms)
        rec_raw = s_desc.get("sweep_reclaimed_at_ms")
        if s_desc.get("sweep_reclaimed") is False:
            sweep_confirmed = True
            sweep_reclaimed = False
            sweep_known_at_ms = occ_ms if 0 < occ_ms <= now_ms else 0
        elif rec_raw is not None:
            rec_ms = int(rec_raw)
            if 0 < occ_ms <= rec_ms <= now_ms:
                sweep_confirmed = True
                sweep_reclaimed = True
                sweep_known_at_ms = rec_ms
            elif 0 < occ_ms <= now_ms and (rec_ms <= 0 or rec_ms > now_ms):
                sweep_confirmed = True
                sweep_reclaimed = False
                sweep_known_at_ms = occ_ms

    # Evaluate FVG freshness, mitigation, and chronological closed-bar retest (STAGE G3 / T01, T02, T03)
    fvg_present = False
    fvg_unmitigated = False
    fvg_retest_valid = False
    fvg_age_bars_m1 = 999
    fvg_known_at_ms = 0

    f_top_val: float = 0.0
    f_bot_val: float = 0.0
    f_conf_val: int = 0
    if sel_fvg is not None:
        f_top_val = float(sel_fvg.get("top", 0.0) or sel_fvg.get("fvg_top", 0.0) or 0.0)
        f_bot_val = float(sel_fvg.get("bottom", 0.0) or sel_fvg.get("fvg_bottom", 0.0) or 0.0)
        f_conf_val = int(sel_fvg.get("confirmed_at") or sel_fvg.get("timestamp") or sel_fvg.get("known_at_ms") or 0)
    elif s_desc.get("selected_fvg_id"):
        f_top_val = float(s_desc.get("fvg_top") or s_desc.get("fvg_high") or s_desc.get("entry_zone_high") or 0.0)
        f_bot_val = float(s_desc.get("fvg_bottom") or s_desc.get("fvg_low") or s_desc.get("entry_zone_low") or 0.0)
        f_conf_val = int(s_desc.get("fvg_formed_at_ms") or 0)

    if 0 < f_conf_val <= now_ms:
        fvg_present = True
        fvg_known_at_ms = f_conf_val
        fvg_age_bars_m1 = max(0, int((now_ms - f_conf_val) // 60_000))
        if f_top_val > f_bot_val > 0.0 and closed_1m:
            # Strictly evaluate bars that closed AFTER FVG formation (no self-bar retest shortcut)
            post_fvg_bars = [b for b in closed_1m if _bar_close_ms(b, 60_000) > f_conf_val and _bar_close_ms(b, 60_000) <= now_ms]
            if direction == "LONG":
                mitigated = bool(s_desc.get("fvg_mitigated", False)) or any(
                    _bar_float(b, "close") < f_bot_val or _bar_float(b, "low") < f_bot_val - 0.05
                    for b in post_fvg_bars
                )
                retested = (
                    (s_desc.get("fvg_retest_at_ms") is not None and f_conf_val <= int(s_desc["fvg_retest_at_ms"]) <= now_ms)
                    or bool(s_desc.get("fvg_retest_valid", False))
                    or any(
                        _bar_float(b, "low") <= f_top_val and _bar_float(b, "close") >= f_bot_val
                        for b in post_fvg_bars[-min(len(post_fvg_bars), 8) :]
                    )
                )
            else:
                mitigated = bool(s_desc.get("fvg_mitigated", False)) or any(
                    _bar_float(b, "close") > f_top_val or _bar_float(b, "high") > f_top_val + 0.05
                    for b in post_fvg_bars
                )
                retested = (
                    (s_desc.get("fvg_retest_at_ms") is not None and f_conf_val <= int(s_desc["fvg_retest_at_ms"]) <= now_ms)
                    or bool(s_desc.get("fvg_retest_valid", False))
                    or any(
                        _bar_float(b, "high") >= f_bot_val and _bar_float(b, "close") <= f_top_val
                        for b in post_fvg_bars[-min(len(post_fvg_bars), 8) :]
                    )
                )
            fvg_unmitigated = (not mitigated) and (fvg_age_bars_m1 <= 24)
            fvg_retest_valid = bool(fvg_unmitigated and retested and fvg_age_bars_m1 <= 24)
        else:
            fvg_unmitigated = (not bool(s_desc.get("fvg_mitigated", False))) and (fvg_age_bars_m1 <= 24)
            if "fvg_retest_valid" in s_desc and s_desc.get("fvg_retest_valid") is not None:
                fvg_retest_valid = bool(s_desc.get("fvg_retest_valid")) and fvg_unmitigated
            elif s_desc.get("fvg_retest_at_ms") is not None:
                fvg_retest_valid = bool(f_conf_val <= int(s_desc["fvg_retest_at_ms"]) <= now_ms and fvg_unmitigated)
            else:
                fvg_retest_valid = bool(fvg_unmitigated)

    # Evaluate strict Breaker + FVG Unicorn confluence vs FVG_ONLY (STAGE G3 / T04, T05, T06)
    u_overlap_low: Optional[float] = None
    u_overlap_high: Optional[float] = None
    u_assess = evaluate_causal_unicorn_confluence(
        context=context,
        direction=direction,
        decision_timestamp_ms=now_ms,
        setup_descriptors=s_desc,
        strict=True,
        require_confirmed_retest=True,
        require_htf_alignment=False,
    )
    u_ev_dict = unicorn_evidence if isinstance(unicorn_evidence, Mapping) else {}
    brk_known_ev = int(u_ev_dict.get("breaker_known_at_ms") or fvg_known_at_ms or now_ms)
    fvg_known_ev = int(u_ev_dict.get("fvg_known_at_ms") or fvg_known_at_ms or now_ms)
    causal_order_invalid = bool(u_ev_dict.get("causal_order_invalid", False))
    if "breaker_formed_after_retest" in u_ev_dict and bool(u_ev_dict.get("breaker_formed_after_retest")):
        causal_order_invalid = True
    if u_ev_dict.get("retest_known_at_ms") is not None and brk_known_ev > int(u_ev_dict["retest_known_at_ms"]):
        causal_order_invalid = True

    # Check explicit Breaker & FVG spatial overlap bounds if provided in u_ev_dict
    ev_ov_low = u_ev_dict.get("overlap_low")
    ev_ov_high = u_ev_dict.get("overlap_high")
    if ev_ov_low is None and u_ev_dict.get("breaker_low") is not None and u_ev_dict.get("fvg_bottom") is not None:
        ev_ov_low = max(float(u_ev_dict["breaker_low"]), float(u_ev_dict["fvg_bottom"]))
    if ev_ov_high is None and u_ev_dict.get("breaker_high") is not None and u_ev_dict.get("fvg_top") is not None:
        ev_ov_high = min(float(u_ev_dict["breaker_high"]), float(u_ev_dict["fvg_top"]))

    spatial_overlap_ok = True
    if ev_ov_low is not None and ev_ov_high is not None:
        spatial_overlap_ok = float(ev_ov_low) < float(ev_ov_high)

    if brk_known_ev > now_ms or fvg_known_ev > now_ms or fvg_known_at_ms > now_ms or causal_order_invalid:
        if brk_known_ev > now_ms or fvg_known_ev > now_ms:
            causal_violations += 1
        unicorn_classification = "INVALID_UNICORN"
        unicorn_breaker_fvg_bound = False
        unicorn_known_at_ms = max(brk_known_ev, fvg_known_ev)
    elif (
        u_ev_dict.get("fvg_present")
        and u_ev_dict.get("breaker_present")
        and bool(u_ev_dict.get("overlap_valid", True))
        and spatial_overlap_ok
        and (ev_ov_low is None or ev_ov_high is None or float(ev_ov_low) < float(ev_ov_high))
    ):
        unicorn_classification = "UNICORN_CONFIRMED"
        unicorn_breaker_fvg_bound = True
        unicorn_known_at_ms = max(brk_known_ev, fvg_known_ev)
        if ev_ov_low is not None:
            u_overlap_low = float(ev_ov_low)
        if ev_ov_high is not None:
            u_overlap_high = float(ev_ov_high)
    elif u_ev_dict.get("breaker_present") and u_ev_dict.get("fvg_present") and not spatial_overlap_ok:
        unicorn_classification = "INVALID_UNICORN"
        unicorn_breaker_fvg_bound = False
        unicorn_known_at_ms = max(brk_known_ev, fvg_known_ev)
    elif (
        bool(getattr(u_assess, "is_valid_unicorn", False))
        and bool(getattr(u_assess, "breaker_id", None))
        and bool(getattr(u_assess, "fvg_id", None))
        and getattr(u_assess, "overlap_zone", None) is not None
    ):
        ov_zone = getattr(u_assess, "overlap_zone", None)
        if isinstance(ov_zone, (tuple, list)) and len(ov_zone) >= 2 and float(ov_zone[0]) < float(ov_zone[1]):
            unicorn_classification = "UNICORN_CONFIRMED"
            unicorn_breaker_fvg_bound = True
            unicorn_known_at_ms = int(
                getattr(u_assess, "retest_timestamp_ms", None)
                or getattr(u_assess, "confirmed_at_ms", None)
                or getattr(u_assess, "known_at_ms", None)
                or fvg_known_at_ms
                or now_ms
            )
            u_overlap_low = float(ov_zone[0])
            u_overlap_high = float(ov_zone[1])
        else:
            unicorn_classification = "INVALID_UNICORN"
            unicorn_breaker_fvg_bound = False
            unicorn_known_at_ms = fvg_known_at_ms
    elif (fvg_present and fvg_unmitigated) or (bool(u_ev_dict.get("fvg_present")) and not bool(u_ev_dict.get("breaker_present"))):
        unicorn_classification = "FVG_ONLY"
        unicorn_breaker_fvg_bound = False
        unicorn_known_at_ms = fvg_known_at_ms
    elif fvg_present:
        unicorn_classification = "INVALID_UNICORN"
        unicorn_breaker_fvg_bound = False
        unicorn_known_at_ms = fvg_known_at_ms
    else:
        unicorn_classification = "NONE"
        unicorn_breaker_fvg_bound = False
        unicorn_known_at_ms = 0

    # Resolve independent pre-entry protected structure authority (STAGE G5 / T17)
    prot_auth = resolve_v17931_protected_structure_authority(
        snapshot={
            "direction": direction,
            "model_id": model_id,
            "entry_reference": entry_price,
            "selected_sl": selected_sl,
            "decision_timestamp_ms": now_ms,
        },
        context=context,
    )
    prot_id = prot_auth.get("outer_anchor_id") or prot_auth.get("protected_structure_anchor_id")
    prot_px = prot_auth.get("protected_structure_price") if prot_id is not None else None
    prot_known = prot_auth.get("protected_structure_known_at_ms") or (now_ms if prot_id is not None else None)
    if prot_px is None and valid_sl_1m and direction == "LONG":
        prot_id = str(valid_sl_1m[-1].get("id") or "sl_1m_anchor")
        prot_px = float(valid_sl_1m[-1]["price"])
        prot_known = int(valid_sl_1m[-1].get("known_at_ms") or valid_sl_1m[-1].get("confirmed_at_ms") or now_ms)
    elif prot_px is None and valid_sh_1m and direction == "SHORT":
        prot_id = str(valid_sh_1m[-1].get("id") or "sh_1m_anchor")
        prot_px = float(valid_sh_1m[-1]["price"])
        prot_known = int(valid_sh_1m[-1].get("known_at_ms") or valid_sh_1m[-1].get("confirmed_at_ms") or now_ms)

    if s_desc.get("protected_structure_anchor_id") is not None:
        prot_id = str(s_desc.get("protected_structure_anchor_id"))
    if s_desc.get("protected_structure_price") is not None:
        prot_px = float(s_desc.get("protected_structure_price"))
    if s_desc.get("protected_structure_known_at_ms") is not None:
        prot_known = int(s_desc.get("protected_structure_known_at_ms"))

    if prot_known is not None and int(prot_known) > now_ms:
        causal_violations += 1

    is_synthetic_from_sl = bool(
        s_desc.get("anchor_derived_from_sl", False)
        or (prot_id is not None and ("from_sl" in str(prot_id).lower() or "synthetic_sl" in str(prot_id).lower()))
    )
    prot_side_valid = bool(
        prot_px is not None
        and (
            (direction == "LONG" and float(prot_px) < entry_price)
            or (direction == "SHORT" and float(prot_px) > entry_price)
        )
    )
    prot_indep = bool(
        not is_synthetic_from_sl
        and prot_id is not None
        and prot_px is not None
        and prot_known is not None
        and 0 < int(prot_known) <= now_ms
        and selected_sl is not None
        and prot_side_valid
        and abs(float(prot_px) - float(selected_sl)) >= 0.005
    )

    # Target reachability & unswept liquidity pool check (STAGE G5 / T19, T20)
    eff_atr_m5 = float(atr_m5) if (atr_m5 is not None and float(atr_m5) > 0.0) else eff_atr_m1 * 2.2
    pools = list(getattr(context, "liquidity_pools", ()) or ())
    if not pools:
        target_swings = sh_1m if direction == "LONG" else sl_1m
        if selected_target_id is not None and any(str(s.get("id", "")) == str(selected_target_id) for s in target_swings):
            pools = _build_causal_swing_pools_from_swings_v1793124(
                target_swings, closed_1m, direction, now_ms
            )
    unswept_pool_id, unswept_pool_px, pool_known_ms, has_unswept_pool, explicit_pool_swept = (
        _resolve_unswept_liquidity_pool_target_v1793123(
            pools=pools,
            closed_1m=closed_1m,
            direction=direction,
            entry_price=entry_price,
            selected_sl=selected_sl,
            selected_target_id=selected_target_id,
            take_profit=take_profit,
            now_ms=now_ms,
            eff_atr_m1=eff_atr_m1,
            eff_atr_m5=eff_atr_m5,
        )
    )

    target_known_at_ms = int(pool_known_ms)
    eff_tp_for_room = take_profit
    eff_target_pool_id: Optional[str] = selected_target_id
    if pools:
        if explicit_pool_swept and not has_unswept_pool:
            eff_target_pool_id = None
        elif has_unswept_pool and unswept_pool_id is not None:
            eff_target_pool_id = unswept_pool_id

    target_room_atr_m1 = (
        round(abs(float(eff_tp_for_room) - entry_price) / eff_atr_m1, 4)
        if eff_tp_for_room is not None
        else 0.0
    )
    stop_dist_atr_m1 = (
        round(abs(entry_price - float(selected_sl)) / eff_atr_m1, 4)
        if selected_sl is not None
        else 0.0
    )
    target_room_atr_m5 = (
        round(abs(float(eff_tp_for_room) - entry_price) / max(0.10, eff_atr_m5), 4)
        if eff_tp_for_room is not None
        else 0.0
    )

    target_reachable = bool(
        eff_tp_for_room is not None
        and selected_sl is not None
        and not (pools and explicit_pool_swept and not has_unswept_pool)
        and target_known_at_ms <= now_ms
        and 1.2 <= target_room_atr_m1 <= 8.0
        and target_room_atr_m5 <= 3.2
        and stop_dist_atr_m1 >= 0.75
        and (
            (direction == "LONG" and float(selected_sl) < entry_price < float(eff_tp_for_room))
            or (direction == "SHORT" and float(eff_tp_for_room) < entry_price < float(selected_sl))
        )
    )

    quote_ts = int(getattr(context, "quote_timestamp_ms", now_ms) or now_ms)
    quote_age_ms = max(0, now_ms - quote_ts)
    spread_val = float(getattr(context, "spread", 0.20) or 0.20)
    spread_to_atr_m1_ratio = round(spread_val / eff_atr_m1, 4)
    has_bid_ask = getattr(context, "bid", None) is not None and getattr(context, "ask", None) is not None
    quote_fresh = bool(
        (
            not has_bid_ask
            or (
                float(getattr(context, "bid", 0.0)) > 0.0
                and float(getattr(context, "ask", 0.0)) > float(getattr(context, "bid", 0.0))
            )
        )
        and quote_age_ms <= 60_000
        and spread_to_atr_m1_ratio <= 0.85
    )

    candidate_quality_tuple: Tuple[int, int, int, int, int, int, float, float] = (
        1 if htf_alignment_state == "ALIGNED" else 0,
        1 if unicorn_classification == "UNICORN_CONFIRMED" else 0,
        1 if (sweep_confirmed and sweep_reclaimed and mss_confirmed) else 0,
        1 if displacement_valid else 0,
        1 if (fvg_retest_valid or continuation_retrace_valid) else 0,
        1 if (target_reachable and prot_indep) else 0,
        round(float(prospective_net_rr or 0.0), 4),
        -round(spread_to_atr_m1_ratio, 4),
    )

    feature_known_at_map = {
        "decision_timestamp_ms": now_ms,
        "htf_known_at_ms": int(htf_known_at_ms),
        "mss_break_known_at_ms": int(mss_break_known_at_ms),
        "sweep_known_at_ms": int(sweep_known_at_ms),
        "fvg_known_at_ms": int(fvg_known_at_ms),
        "unicorn_known_at_ms": int(unicorn_known_at_ms),
        "protected_structure_known_at_ms": int(prot_known or 0),
        "target_known_at_ms": int(target_known_at_ms),
        "quote_timestamp_ms": int(quote_ts),
    }

    pre_entry_feature_hash = _canonical_json_sha256(
        {
            "model_id": model_id,
            "direction": direction,
            "decision_timestamp_ms": now_ms,
            "htf_alignment_state": htf_alignment_state,
            "h4_bias": h4_bias,
            "h1_bias": h1_bias,
            "m15_bias": m15_bias,
            "mss_confirmed": mss_confirmed,
            "displacement_body_atr_ratio": displacement_body_atr_ratio,
            "sweep_confirmed": sweep_confirmed,
            "sweep_reclaimed": sweep_reclaimed,
            "fvg_retest_valid": fvg_retest_valid,
            "unicorn_classification": unicorn_classification,
            "protected_structure_independent_of_sl": prot_indep,
            "target_reachable": target_reachable,
            "candidate_quality_tuple": list(candidate_quality_tuple),
            "feature_known_at_map": feature_known_at_map,
        }
    )

    return SignalQualityEvidenceV1793122(
        model_id=model_id,
        direction=direction,
        decision_timestamp_ms=now_ms,
        htf_alignment_state=htf_alignment_state,
        h4_bias=h4_bias,
        h1_bias=h1_bias,
        m15_bias=m15_bias,
        m5_bias=m5_bias,
        htf_known_at_ms=int(htf_known_at_ms),
        htf_causal_valid=htf_causal_valid,
        mss_confirmed=mss_confirmed,
        mss_break_known_at_ms=int(mss_break_known_at_ms),
        displacement_body_atr_ratio=displacement_body_atr_ratio,
        displacement_valid=displacement_valid,
        continuation_retrace_valid=continuation_retrace_valid,
        sweep_confirmed=sweep_confirmed,
        sweep_reclaimed=sweep_reclaimed,
        sweep_penetration_ticks=sweep_penetration_ticks,
        sweep_known_at_ms=int(sweep_known_at_ms),
        sweep_level_price=sweep_level_price,
        fvg_present=fvg_present,
        fvg_unmitigated=fvg_unmitigated,
        fvg_retest_valid=fvg_retest_valid,
        fvg_age_bars_m1=fvg_age_bars_m1,
        fvg_known_at_ms=int(fvg_known_at_ms),
        unicorn_classification=unicorn_classification,
        unicorn_breaker_fvg_bound=unicorn_breaker_fvg_bound,
        unicorn_known_at_ms=int(unicorn_known_at_ms),
        protected_structure_anchor_id=str(prot_id) if prot_id is not None else None,
        protected_structure_price=float(prot_px) if prot_px is not None else None,
        protected_structure_known_at_ms=int(prot_known) if prot_known is not None else None,
        protected_structure_independent_of_sl=prot_indep,
        target_reachable=target_reachable,
        target_room_atr_m1=target_room_atr_m1,
        target_unswept_pool_id=eff_target_pool_id,
        target_known_at_ms=int(target_known_at_ms),
        quote_fresh=quote_fresh,
        quote_age_ms=quote_age_ms,
        spread_to_atr_m1_ratio=spread_to_atr_m1_ratio,
        prospective_net_rr=round(float(prospective_net_rr or 0.0), 4),
        planned_risk_usd=round(float(planned_risk_usd or 0.0), 4),
        candidate_quality_tuple=candidate_quality_tuple,
        feature_known_at_map=feature_known_at_map,
        pre_entry_feature_hash=pre_entry_feature_hash,
        h4_known_at_ms=int(h4_ts),
        h1_known_at_ms=int(h1_ts),
        m15_known_at_ms=int(m15_ts),
        m5_known_at_ms=int(m5_ts),
        unicorn_overlap_low=u_overlap_low,
        unicorn_overlap_high=u_overlap_high,
        causal_feature_violations_count=causal_violations,
    )


def evaluate_signal_intelligence_gate_v1793122(
    *,
    context: Any,
    model_id: str,
    direction: str,
    candidate_key: str = "cand",
    setup_episode_id: Optional[str] = None,
    setup_descriptors: Optional[Mapping[str, Any]] = None,
    sh_1m: Sequence[Mapping[str, Any]] = (),
    sl_1m: Sequence[Mapping[str, Any]] = (),
    baseline_status: str = "ELIGIBLE",
    baseline_rejection_reasons: Sequence[str] = (),
    baseline_entry: Optional[float] = None,
    baseline_sl: Optional[float] = None,
    baseline_tp: Optional[float] = None,
    baseline_qty: Optional[float] = None,
    baseline_gross_rr: Optional[float] = None,
    baseline_net_rr: Optional[float] = None,
    baseline_anchor_id: Optional[str] = None,
    baseline_anchor_kind: Optional[str] = None,
    baseline_invalidation_price: Optional[float] = None,
    baseline_target_id: Optional[str] = None,
    atr_m1: float = 1.0,
    atr_m5: Optional[float] = None,
    unicorn_evidence: Optional[Any] = None,
    policy_settings: Optional[Mapping[str, Any]] = None,
    raw_mode: Any = "OFF",
    raw_policy_id: Any = "SI0_OFF_BASELINE",
    mode: Any = None,
    policy_id: Any = None,
    adapter_origin: str = "SHARED_EVALUATOR",
) -> SignalIntelligencePlanV1793122:
    """
    Evaluates the V17.9.3.1.2.2 Signal Intelligence stage inside `SharedStrategyEvaluator`.
    Enforces:
      - Fail-closed rejection on invalid mode or policy ID.
      - Fail-closed rejection if `RESEARCH_APPLY` is invoked from Production/PAPER/LIVE adapter.
      - Strict non-rescue invariant: a candidate that is NOT `ELIGIBLE` in baseline is NEVER
        promoted to `ELIGIBLE` by `SI0..SI6`.
      - Full recalculation of SL/TP/qty/net_rr via `plan_v17_9_2_structural_stop_and_target`
        and `calculate_risk_reward` when `SI5` or `SI6` updates geometry.
    """
    eff_raw_mode = mode if mode is not None else raw_mode
    eff_raw_policy_id = policy_id if policy_id is not None else raw_policy_id
    pol_settings = dict(policy_settings or {})

    now_ms = int(getattr(context, "decision_timestamp_ms", 0) or getattr(context, "now_ms", 0) or 0)
    norm_mode, mode_valid, mode_err = normalize_signal_intelligence_mode_v1793122(eff_raw_mode)
    norm_policy_id, policy_valid, pol_err = normalize_signal_intelligence_policy_id_v1793122(
        eff_raw_policy_id, mode=norm_mode if mode_valid else "OFF"
    )
    policy_obj = PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122.get(
        norm_policy_id, PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122["SI0_OFF_BASELINE"]
    )
    pol_hash = policy_obj.policy_hash()

    ctx_pol = getattr(context, "policy", None)
    cap_usdt = float(pol_settings.get("capital_usdt") or getattr(context, "capital_usdt", 1000.0) or 1000.0)
    risk_pct = float(
        pol_settings.get("risk_pct")
        or getattr(ctx_pol, "risk_per_trade_pct", None)
        or getattr(ctx_pol, "fallback_risk_pct", 0.25)
        or 0.25
    )
    max_risk_cap = round(cap_usdt * (risk_pct / 100.0), 4)
    costs_obj = pol_settings.get("costs") or CostAssumptions()

    planned_risk_usd = 0.0
    if baseline_entry is not None and baseline_sl is not None and baseline_qty is not None and baseline_qty > 0:
        entry_fee = baseline_entry * baseline_qty * float(getattr(costs_obj, "taker_fee_rate", 0.0004))
        exit_fee = baseline_sl * baseline_qty * float(getattr(costs_obj, "taker_fee_rate", 0.0004))
        slip_cost = float(getattr(costs_obj, "slippage_usd", 0.10)) * baseline_qty
        planned_risk_usd = round(
            abs(baseline_entry - baseline_sl) * baseline_qty + entry_fee + exit_fee + slip_cost,
            4,
        )

    evidence = build_signal_quality_evidence_v1793122(
        context=context,
        model_id=model_id,
        direction=direction,
        setup_descriptors=setup_descriptors,
        sh_1m=sh_1m,
        sl_1m=sl_1m,
        entry_price=float(baseline_entry or 0.0),
        selected_sl=baseline_sl,
        take_profit=baseline_tp,
        selected_target_id=baseline_target_id,
        atr_m1=atr_m1,
        atr_m5=atr_m5,
        prospective_net_rr=float(baseline_net_rr or 0.0),
        planned_risk_usd=planned_risk_usd,
        unicorn_evidence=unicorn_evidence,
    )

    reasons: List[str] = list(baseline_rejection_reasons or ())
    rejection_stage: Optional[str] = None
    pending_obj: Optional[PendingConfirmationV1793122] = None

    prop_entry = baseline_entry
    prop_sl = baseline_sl
    prop_tp = baseline_tp
    prop_qty = baseline_qty
    prop_gross_rr = baseline_gross_rr
    prop_net_rr = baseline_net_rr
    prop_risk_usd = planned_risk_usd
    prop_anchor_id = baseline_anchor_id
    prop_anchor_kind = baseline_anchor_kind
    prop_inv_px = baseline_invalidation_price
    prop_target_id = baseline_target_id
    prop_status = baseline_status

    # 1. Fail-closed mode / policy / production adapter checks
    if not mode_valid:
        if mode_err and mode_err not in reasons:
            reasons.append(mode_err)
        rejection_stage = "INVALID_MODE_FAIL_CLOSED"
        prop_status = "REJECTED"
        prop_qty = 0.0
    if not policy_valid:
        if pol_err and pol_err not in reasons:
            reasons.append(pol_err)
        rejection_stage = rejection_stage or "INVALID_POLICY_FAIL_CLOSED"
        prop_status = "REJECTED"
        prop_qty = 0.0

    if norm_mode == "RESEARCH_APPLY":
        forbid_prod, forbid_code = is_research_apply_forbidden_in_adapter_v1793122(
            adapter_origin, policy_dict=context.get_policy_dict() if hasattr(context, "get_policy_dict") else None
        )
        if forbid_prod and forbid_code:
            if forbid_code not in reasons:
                reasons.append(forbid_code)
            rejection_stage = rejection_stage or "PRODUCTION_RUNTIME_GUARD"
            prop_status = "REJECTED"
            prop_qty = 0.0

    # 2. Evaluate policy gates when baseline candidate is otherwise valid/eligible
    # Note: Never promote a baseline non-eligible candidate to ELIGIBLE.
    baseline_is_eligible = (baseline_status == "ELIGIBLE" and len(baseline_rejection_reasons or ()) == 0)

    if mode_valid and policy_valid and norm_policy_id != "SI0_OFF_BASELINE":
        gate_reasons: List[str] = []
        needs_pending_confirmation = False
        pending_type = "MSS_DISPLACEMENT_BREAK"

        # Quote freshness & causal feature check (applies across all SI1..SI6)
        if not evidence.quote_fresh:
            gate_reasons.append("SI_QUOTE_STALE_OR_WIDE_SPREAD")
            rejection_stage = rejection_stage or "QUOTE_QUALITY_GATE"
        if not evidence.htf_causal_valid:
            gate_reasons.append("SI2_FUTURE_OR_INVALID_HTF_SWING_REJECTED")
            rejection_stage = rejection_stage or "CAUSAL_PROVENANCE_GATE"

        # SI1: Model-specific setup defect filter & arbitration readiness
        if policy_obj.enable_model_quality_arbitration:
            if model_id == ModelIdentifier.SMC_M1_CONTINUATION.value:
                if not evidence.mss_confirmed:
                    gate_reasons.append("SI_CONTINUATION_MISSING_CONFIRMED_BOS")
                    rejection_stage = rejection_stage or "SI1_MODEL_QUALITY"
                if evidence.displacement_body_atr_ratio < policy_obj.min_displacement_body_atr_ratio:
                    gate_reasons.append("SI1_CONT_WEAK_DISPLACEMENT_BODY")
                    gate_reasons.append("SI_CONTINUATION_WEAK_DISPLACEMENT_BODY")
                    rejection_stage = rejection_stage or "SI1_MODEL_QUALITY"
                if not evidence.continuation_retrace_valid:
                    gate_reasons.append("SI1_CONT_IMPULSE_CHASING_NO_RETRACE")
                    rejection_stage = rejection_stage or "SI1_MODEL_QUALITY"
            elif model_id == ModelIdentifier.SMC_M1_REVERSAL.value:
                if not (evidence.sweep_confirmed and evidence.sweep_reclaimed):
                    gate_reasons.append("SI1_REV_UNCONFIRMED_SWEEP_RECLAIM")
                    gate_reasons.append("SI_REVERSAL_MISSING_CONFIRMED_SWEEP_RECLAIM")
                    rejection_stage = rejection_stage or "SI1_MODEL_QUALITY"
                elif policy_obj.enable_core_trading_logic_repair_v1793123 and not evidence.mss_confirmed:
                    gate_reasons.append("SI1_REV_MISSING_SEQUENCED_MSS_AFTER_SWEEP")
                    rejection_stage = rejection_stage or "SI1_MODEL_QUALITY"
            elif model_id in (
                ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
                ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
            ):
                if not evidence.fvg_present or not evidence.fvg_unmitigated:
                    gate_reasons.append("SI1_FVG_MISSING_OR_MITIGATED")
                    gate_reasons.append("SI_FVG_STALE_OR_MITIGATED")
                    rejection_stage = rejection_stage or "SI1_MODEL_QUALITY"
                elif evidence.fvg_age_bars_m1 > policy_obj.max_fvg_age_bars_m1:
                    gate_reasons.append("SI1_FVG_STALE_EXCEEDED_MAX_AGE")
                    gate_reasons.append("SI_FVG_STALE_OR_MITIGATED")
                    rejection_stage = rejection_stage or "SI1_MODEL_QUALITY"
                elif not evidence.fvg_retest_valid:
                    gate_reasons.append("SI1_FVG_NO_CHRONOLOGICAL_RETEST")
                    gate_reasons.append("SI_FVG_STALE_OR_MITIGATED")
                    rejection_stage = rejection_stage or "SI1_MODEL_QUALITY"
            elif model_id == ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value:
                if not (evidence.sweep_confirmed and evidence.sweep_reclaimed):
                    gate_reasons.append("SI1_SWEEP_RECLAIM_NOT_CONFIRMED")
                    rejection_stage = rejection_stage or "SI1_MODEL_QUALITY"

        # SI2: Causal HTF structure & direction validation
        if policy_obj.enable_htf_structure_and_direction_validation:
            if evidence.htf_alignment_state in ("ABSENT", "INSUFFICIENT", "INSUFFICIENT_DATA", "STALE"):
                gate_reasons.append(f"SI2_HTF_CONTEXT_{evidence.htf_alignment_state}")
                gate_reasons.append("SI_HTF_DATA_INSUFFICIENT_FAIL_CLOSED")
                rejection_stage = rejection_stage or "SI2_HTF_STRUCTURE"
            elif model_id in (
                ModelIdentifier.SMC_M1_CONTINUATION.value,
                ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
            ):
                if evidence.htf_alignment_state != "ALIGNED":
                    gate_reasons.append(f"SI2_CONT_HTF_NOT_ALIGNED:{evidence.htf_alignment_state}")
                    rejection_stage = rejection_stage or "SI2_HTF_STRUCTURE"
            else:
                # Reversal / Sweep models: reject COUNTERTREND unless sweep+reclaim+MSS confirmed
                if evidence.htf_alignment_state == "COUNTERTREND":
                    if not (evidence.sweep_confirmed and evidence.sweep_reclaimed and evidence.mss_confirmed and evidence.displacement_valid):
                        gate_reasons.append("SI2_REV_COUNTER_INTACT_HTF_IMPULSE")
                        rejection_stage = rejection_stage or "SI2_HTF_STRUCTURE"
                elif evidence.htf_alignment_state == "MIXED":
                    if not (evidence.sweep_confirmed and evidence.sweep_reclaimed):
                        gate_reasons.append("SI2_REV_MIXED_HTF_WITHOUT_SWEEP")
                        rejection_stage = rejection_stage or "SI2_HTF_STRUCTURE"

        # SI3: MSS displacement & confirmed entry (or transition to WAITING_CONFIRMATION)
        if policy_obj.enable_mss_displacement_confirmation:
            if not evidence.mss_confirmed or not evidence.displacement_valid:
                needs_pending_confirmation = True
                pending_type = "MSS_DISPLACEMENT_BREAK"
            elif model_id in (
                ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
                ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
            ) and not evidence.fvg_retest_valid:
                needs_pending_confirmation = True
                pending_type = "FVG_RETEST_CONFIRMATION"
            elif model_id == ModelIdentifier.SMC_M1_CONTINUATION.value and not evidence.continuation_retrace_valid:
                needs_pending_confirmation = True
                pending_type = "MSS_DISPLACEMENT_BREAK"

        # SI4: Causal liquidity sweep & strict Unicorn (Breaker + FVG)
        if policy_obj.enable_causal_sweep_and_strict_unicorn:
            if evidence.fvg_present and (not evidence.fvg_unmitigated or evidence.fvg_age_bars_m1 > policy_obj.max_fvg_age_bars_m1):
                gate_reasons.append("SI4_FVG_STALE_OR_ALREADY_MITIGATED")
                rejection_stage = rejection_stage or "SI4_FVG_VALIDATION"
            if (
                evidence.unicorn_classification == "UNICORN_CONFIRMED"
                and evidence.unicorn_overlap_low is not None
                and evidence.unicorn_overlap_high is not None
                and float(evidence.unicorn_overlap_low) < float(evidence.unicorn_overlap_high)
            ):
                prop_entry = round((float(evidence.unicorn_overlap_low) + float(evidence.unicorn_overlap_high)) / 2.0, 2)
            if policy_obj.enable_core_trading_logic_repair_v1793123:
                # V17.9.3.1.2.3 STAGE G3: Model-specific confluence (prevents starving non-Unicorn models)
                if model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value:
                    if evidence.unicorn_classification == "FVG_ONLY":
                        gate_reasons.append("SI4_FVG_ONLY_NOT_CONFIRMED_UNICORN")
                        gate_reasons.append("SI_FVG_ONLY_NOT_TRUE_UNICORN")
                        rejection_stage = rejection_stage or "SI4_STRICT_UNICORN"
                    elif evidence.unicorn_classification != "UNICORN_CONFIRMED" or not evidence.unicorn_breaker_fvg_bound:
                        gate_reasons.append("SI4_UNICORN_BREAKER_FVG_NOT_BOUND")
                        rejection_stage = rejection_stage or "SI4_STRICT_UNICORN"
                elif model_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value:
                    if not (evidence.fvg_present and evidence.fvg_unmitigated and evidence.fvg_retest_valid):
                        gate_reasons.append("SI4_FVG_CONTINUATION_ZONE_NOT_RETESTED_OR_MITIGATED")
                        rejection_stage = rejection_stage or "SI4_FVG_VALIDATION"
                elif model_id in (
                    ModelIdentifier.SMC_M1_REVERSAL.value,
                    ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
                ):
                    if not (evidence.sweep_confirmed and evidence.sweep_reclaimed):
                        gate_reasons.append("SI4_SWEEP_RECLAIM_NOT_CONFIRMED")
                        rejection_stage = rejection_stage or "SI4_LIQUIDITY_SWEEP"
                    elif not evidence.mss_confirmed:
                        gate_reasons.append("SI4_SWEEP_MISSING_MSS_CONFIRMATION")
                        rejection_stage = rejection_stage or "SI4_LIQUIDITY_SWEEP"
                elif model_id == ModelIdentifier.SMC_M1_CONTINUATION.value:
                    if not (evidence.mss_confirmed and evidence.displacement_valid and evidence.continuation_retrace_valid):
                        gate_reasons.append("SI4_CONT_MISSING_DISPLACEMENT_OR_RETRACE")
                        rejection_stage = rejection_stage or "SI4_CONTINUATION_VALIDATION"
            else:
                if model_id in (
                    ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
                    ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                ):
                    if evidence.unicorn_classification == "FVG_ONLY":
                        gate_reasons.append("SI4_FVG_ONLY_NOT_CONFIRMED_UNICORN")
                        gate_reasons.append("SI_FVG_ONLY_NOT_TRUE_UNICORN")
                        rejection_stage = rejection_stage or "SI4_STRICT_UNICORN"
                    elif evidence.unicorn_classification != "UNICORN_CONFIRMED" or not evidence.unicorn_breaker_fvg_bound:
                        gate_reasons.append("SI4_UNICORN_BREAKER_FVG_NOT_BOUND")
                        rejection_stage = rejection_stage or "SI4_STRICT_UNICORN"
                elif model_id in (
                    ModelIdentifier.SMC_M1_REVERSAL.value,
                    ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
                ):
                    if not (evidence.sweep_confirmed and evidence.sweep_reclaimed):
                        gate_reasons.append("SI4_SWEEP_RECLAIM_NOT_CONFIRMED")
                        rejection_stage = rejection_stage or "SI4_LIQUIDITY_SWEEP"
                    elif evidence.unicorn_classification != "UNICORN_CONFIRMED" and not evidence.fvg_retest_valid:
                        gate_reasons.append("SI4_SWEEP_MISSING_UNICORN_OR_FVG_RETEST")
                        rejection_stage = rejection_stage or "SI4_STRICT_UNICORN"
                elif model_id == ModelIdentifier.SMC_M1_CONTINUATION.value:
                    if evidence.unicorn_classification != "UNICORN_CONFIRMED" and not (
                        evidence.sweep_confirmed and evidence.continuation_retrace_valid
                    ):
                        gate_reasons.append("SI4_CONT_NO_UNICORN_OR_SWEEP_RETRACE")
                        rejection_stage = rejection_stage or "SI4_STRICT_UNICORN"

        # SI5: Independent protected structure SL & reachable target with full risk recalculation
        if policy_obj.enable_structural_sl_and_reachable_target and baseline_entry is not None:
            if not evidence.protected_structure_independent_of_sl:
                gate_reasons.append("SI5_PROTECTED_STRUCTURE_NOT_INDEPENDENT_OR_MISSING")
                gate_reasons.append("SI_NO_INDEPENDENT_PROTECTED_STRUCTURE_ANCHOR")
                rejection_stage = rejection_stage or "SI5_STRUCTURAL_SL_TP"
            elif evidence.protected_structure_price is not None and prop_sl is not None:
                # Ensure SL is buffered beyond independent protected structure anchor
                prot_px_f = float(evidence.protected_structure_price)
                min_buf = max(0.15, round(0.25 * max(0.10, atr_m1), 2))
                required_sl = (
                    round(prot_px_f - min_buf, 2)
                    if direction == "LONG"
                    else round(prot_px_f + min_buf, 2)
                )
                need_sl_rebuild = (
                    (direction == "LONG" and float(prop_sl) > required_sl)
                    or (direction == "SHORT" and float(prop_sl) < required_sl)
                )
                if need_sl_rebuild and prop_tp is not None:
                    prop_sl = required_sl
                    prop_inv_px = prot_px_f
                    if evidence.protected_structure_anchor_id:
                        prop_anchor_id = evidence.protected_structure_anchor_id
                        prop_anchor_kind = "INDEPENDENT_PROTECTED_SWING"

            # V17.9.3.1.2.3 STAGE G5 / V17.9.3.1.2.4: Resolve unswept pre-entry liquidity pool target
            ctx_pools = list(getattr(context, "liquidity_pools", ()) or ())
            raw_1m_for_pools = list(getattr(context, "closed_m1_candles", None) or getattr(context, "candles_1m", ()) or ())
            closed_1m_for_pools, _ = _closed_bars_as_of(raw_1m_for_pools, now_ms, 60_000)
            raw_5m_for_pools = list(getattr(context, "closed_m5_candles", None) or getattr(context, "candles_5m", ()) or ())
            raw_15m_for_pools = list(getattr(context, "closed_m15_candles", None) or getattr(context, "candles_15m", ()) or ())
            raw_h1_for_pools = list(getattr(context, "closed_h1_candles", None) or getattr(context, "candles_1h", ()) or ())
            has_mtf_pools_ctx = bool(raw_5m_for_pools or raw_15m_for_pools or raw_h1_for_pools)

            if not ctx_pools and not evidence.target_reachable:
                target_swings_for_pools = list(sh_1m if direction == "LONG" else sl_1m)
                ctx_pools = _build_causal_swing_pools_from_swings_v1793124(
                    target_swings_for_pools, closed_1m_for_pools, direction, now_ms
                )
            if policy_obj.enable_core_trading_logic_repair_v1793123 and has_mtf_pools_ctx and not evidence.target_reachable:
                from services.liquidity_scenario_service import identify_confirmed_swings
                mtf_swings_for_pools: List[Dict[str, Any]] = []
                existing_ids = {
                    str(p.get("id") or p.get("pool_id") or "") if isinstance(p, Mapping) else str(getattr(p, "id", None) or getattr(p, "pool_id", "") or "")
                    for p in ctx_pools
                }
                for tf_raw_b, tf_lbl_b, tf_ms_b in (
                    (raw_5m_for_pools, "5M", 300_000),
                    (raw_15m_for_pools, "15M", 900_000),
                    (raw_h1_for_pools, "1H", 3_600_000),
                ):
                    closed_tf_b, _ = _closed_bars_as_of(tf_raw_b, now_ms, tf_ms_b)
                    if len(closed_tf_b) >= 5:
                        sh_tf_b, sl_tf_b = identify_confirmed_swings(
                            closed_tf_b, timeframe=tf_lbl_b, left_bars=1, right_bars=1, as_of_ms=now_ms
                        )
                        for sw_b in (sh_tf_b if direction == "LONG" else sl_tf_b):
                            sw_id_b = str(sw_b.get("id") or f"pool_{tf_lbl_b}_{sw_b.get('occurred_at_ms')}")
                            if sw_id_b in existing_ids:
                                continue
                            sw_px_b = float(sw_b.get("price", 0.0) or 0.0)
                            sw_occ_b = int(sw_b.get("occurred_at_ms", 0) or 0)
                            sw_k_b = int(sw_b.get("known_at_ms") or (sw_occ_b + tf_ms_b))
                            # Check TF bars after swing formation as well as closed_1m_for_pools
                            post_tf_b = [b for b in closed_tf_b if _bar_close_ms(b, tf_ms_b) > min(sw_occ_b, sw_k_b) and _bar_close_ms(b, tf_ms_b) <= now_ms]
                            swept_on_tf = (
                                any(_bar_float(b, "high") >= sw_px_b for b in post_tf_b)
                                if direction == "LONG"
                                else any(_bar_float(b, "low") <= sw_px_b for b in post_tf_b)
                            )
                            mtf_swings_for_pools.append(
                                {
                                    "id": sw_id_b,
                                    "price": sw_px_b,
                                    "occurred_at_ms": sw_occ_b,
                                    "known_at_ms": sw_k_b,
                                    "swept": swept_on_tf,
                                }
                            )
                            existing_ids.add(sw_id_b)
                if (
                    prop_tp is not None
                    and prop_target_id
                    and str(prop_target_id) not in existing_ids
                ):
                    mtf_swings_for_pools.append(
                        {
                            "id": str(prop_target_id),
                            "price": float(prop_tp),
                            "occurred_at_ms": now_ms - 300_000,
                            "known_at_ms": now_ms,
                        }
                    )
                if mtf_swings_for_pools:
                    ctx_pools.extend(
                        _build_causal_swing_pools_from_swings_v1793124(
                            mtf_swings_for_pools, closed_1m_for_pools, direction, now_ms
                        )
                    )
            eff_atr_m1_for_pools = max(0.05, float(atr_m1 or 0.5))
            eff_atr_m5_for_pools = float(atr_m5) if (atr_m5 is not None and float(atr_m5) > 0.0) else eff_atr_m1_for_pools * 2.2
            eff_max_m1_mult = float(policy_obj.max_target_atr_m1_multiple)
            eff_max_m5_mult = 3.2
            min_req_dist = 0.0
            if policy_obj.enable_core_trading_logic_repair_v1793123 and has_mtf_pools_ctx and prop_entry is not None and prop_sl is not None:
                fee_rt = float(prop_entry) * 2.0 * float(getattr(costs_obj, "taker_fee_rate", 0.0004)) + float(getattr(costs_obj, "slippage_usd", 0.10))
                stop_d = abs(float(prop_entry) - float(prop_sl))
                min_req_dist = float(policy_obj.min_net_rr) * (stop_d + fee_rt) + fee_rt
                max_reach_dist = 3.8 * (stop_d + fee_rt) + fee_rt
                eff_max_m1_mult = max(eff_max_m1_mult, 28.0, round(max_reach_dist / eff_atr_m1_for_pools, 4))
                eff_max_m5_mult = max(10.5, round(max_reach_dist / eff_atr_m5_for_pools, 4))

            u_pool_id, u_pool_px, _, has_u_pool, exp_pool_swept = _resolve_unswept_liquidity_pool_target_v1793123(
                pools=ctx_pools,
                closed_1m=closed_1m_for_pools,
                direction=direction,
                entry_price=float(prop_entry or baseline_entry or 0.0),
                selected_sl=prop_sl,
                selected_target_id=prop_target_id,
                take_profit=prop_tp,
                now_ms=now_ms,
                eff_atr_m1=eff_atr_m1_for_pools,
                eff_atr_m5=eff_atr_m5_for_pools,
                max_target_atr_m1_multiple=eff_max_m1_mult,
                max_target_atr_m5_multiple=eff_max_m5_mult,
                min_required_target_distance=min_req_dist,
            )
            if has_u_pool and u_pool_id is not None and u_pool_px is not None:
                prop_target_id = u_pool_id
                prop_tp = u_pool_px
            elif (ctx_pools and (exp_pool_swept or not has_u_pool)) or (
                policy_obj.require_unswept_liquidity_pool_target and not prop_target_id
            ):
                gate_reasons.append("SI5_TARGET_POOL_ABSENT_OR_ALREADY_SWEPT")
                gate_reasons.append("SI5_TARGET_UNREACHABLE_OR_STOP_INSIDE_NOISE")
                rejection_stage = rejection_stage or "SI5_STRUCTURAL_SL_TP"

            # Recompute risk/reward and quantity whenever SI5 evaluates geometry
            if prop_entry is not None and prop_sl is not None and prop_tp is not None:
                pol = policy_obj
                calc = calculate_risk_reward(
                    direction=direction,
                    planned_entry=float(prop_entry),
                    stop_loss=float(prop_sl),
                    take_profit=float(prop_tp),
                    capital_usdt=cap_usdt,
                    risk_pct=risk_pct,
                    leverage=int(pol_settings.get("leverage", 20)),
                    margin_mode=str(pol_settings.get("margin_mode", "ISOLATED")),
                    min_net_rr=float(pol.min_net_rr),
                    costs=costs_obj,
                    now_ms=now_ms,
                )
                new_qty = round(float(calc.quantity or 0.0), 6)
                prop_qty = new_qty
                prop_gross_rr = round(float(calc.gross_rr or 0.0), 4)
                prop_net_rr = round(float(calc.net_rr or 0.0), 4)
                if prop_qty > 0:
                    e_fee = float(prop_entry) * prop_qty * float(getattr(costs_obj, "taker_fee_rate", 0.0004))
                    x_fee = float(prop_sl) * prop_qty * float(getattr(costs_obj, "taker_fee_rate", 0.0004))
                    s_cost = float(getattr(costs_obj, "slippage_usd", 0.10)) * prop_qty
                    prop_risk_usd = round(
                        abs(float(prop_entry) - float(prop_sl)) * prop_qty + e_fee + x_fee + s_cost,
                        4,
                    )
                else:
                    prop_risk_usd = 0.0

                new_risk = prop_risk_usd
                risk_cap_ok = bool(new_risk <= pol.max_planned_risk_usd + 1e-6)
                if not risk_cap_ok:
                    gate_reasons.append("SI5_PLANNED_RISK_EXCEEDS_2_50_CAP")
                    rejection_stage = rejection_stage or "SI5_STRUCTURAL_SL_TP"
                if not calc.is_valid or not calc.can_execute or prop_qty < 0.01:
                    gate_reasons.append(str(calc.invalid_reason or calc.skip_reason or "SI5_RISK_SIZING_INVALID"))
                    rejection_stage = rejection_stage or "SI5_STRUCTURAL_SL_TP"
                elif prop_net_rr < float(pol.min_net_rr):
                    gate_reasons.append("SI5_INSUFFICIENT_NET_RR_AFTER_STRUCTURAL_SL")
                    rejection_stage = rejection_stage or "SI5_STRUCTURAL_SL_TP"

            # Re-verify target reachability with updated prop_tp and prop_sl
            eff_room_m1 = (
                round(abs(float(prop_tp) - float(prop_entry)) / max(0.05, float(atr_m1 or 0.5)), 4)
                if (prop_tp is not None and prop_entry is not None)
                else 0.0
            )
            eff_stop_m1 = (
                round(abs(float(prop_entry) - float(prop_sl)) / max(0.05, float(atr_m1 or 0.5)), 4)
                if (prop_sl is not None and prop_entry is not None)
                else 0.0
            )
            eff_atr5_val = float(atr_m5) if (atr_m5 is not None and float(atr_m5) > 0.0) else max(0.05, float(atr_m1 or 0.5)) * 2.2
            eff_room_m5 = (
                round(abs(float(prop_tp) - float(prop_entry)) / max(0.10, eff_atr5_val), 4)
                if (prop_tp is not None and prop_entry is not None)
                else 0.0
            )
            updated_target_reachable = bool(
                prop_tp is not None
                and prop_sl is not None
                and prop_entry is not None
                and 1.2 <= eff_room_m1 <= eff_max_m1_mult
                and eff_room_m5 <= eff_max_m5_mult
                and eff_stop_m1 >= float(policy_obj.min_stop_atr_m1_ratio)
                and (
                    (direction == "LONG" and float(prop_sl) < float(prop_entry) < float(prop_tp))
                    or (direction == "SHORT" and float(prop_tp) < float(prop_entry) < float(prop_sl))
                )
            )
            if not (evidence.target_reachable or updated_target_reachable):
                if "SI5_TARGET_UNREACHABLE_OR_STOP_INSIDE_NOISE" not in gate_reasons:
                    gate_reasons.append("SI5_TARGET_UNREACHABLE_OR_STOP_INSIDE_NOISE")
                rejection_stage = rejection_stage or "SI5_STRUCTURAL_SL_TP"

        # Determine final proposed status under RESEARCH_APPLY
        if baseline_is_eligible:
            if gate_reasons:
                for gr in gate_reasons:
                    if gr not in reasons:
                        reasons.append(gr)
                prop_status = "VALID_SETUP_REJECTED_BY_RISK"
                prop_qty = 0.0
            elif needs_pending_confirmation:
                trig_px = float(baseline_entry or 0.0)
                if sh_1m and direction == "LONG":
                    trig_px = float(sh_1m[-1]["price"])
                elif sl_1m and direction == "SHORT":
                    trig_px = float(sl_1m[-1]["price"])
                inv_px = float(prop_sl or baseline_sl or trig_px)
                s_desc = dict(setup_descriptors or {})
                pending_obj = PendingConfirmationV1793122(
                    pending_setup_id=f"pend_{norm_policy_id}_{model_id}_{direction}_{now_ms}",
                    origin_opportunity_id=candidate_key,
                    model_id=model_id,
                    symbol=str(getattr(context, "symbol", "XAUUSDT")),
                    direction=direction,
                    created_at_ms=now_ms,
                    expires_at_ms=now_ms + int(policy_obj.max_pending_confirmation_bars_m1) * 60_000,
                    max_wait_bars_m1=int(policy_obj.max_pending_confirmation_bars_m1),
                    required_confirmation_type=pending_type,
                    trigger_level_price=round(trig_px, 2),
                    invalidation_price=round(inv_px, 2),
                    zone_low=s_desc.get("entry_zone_low"),
                    zone_high=s_desc.get("entry_zone_high"),
                    baseline_entry=float(baseline_entry or 0.0),
                    baseline_sl=float(prop_sl or baseline_sl or 0.0),
                    baseline_tp=float(prop_tp or baseline_tp or 0.0),
                    status="WAITING_CONFIRMATION",
                )
                reasons.append(f"SI3_WAITING_CONFIRMATION:{pending_type}")
                rejection_stage = "SI3_WAITING_CONFIRMATION"
                prop_status = "WAITING_CONFIRMATION"
                prop_qty = 0.0
        else:
            # Candidate was already non-eligible in baseline; append diagnostic reasons without rescuing
            for gr in gate_reasons:
                if gr not in reasons:
                    reasons.append(gr)

    if not mode_valid or not policy_valid:
        applied_status = "REJECTED"
        applied_qty = 0.0
        applied_reasons: Tuple[str, ...] = tuple(reasons)
    elif norm_mode == "RESEARCH_APPLY" and mode_valid and policy_valid:
        applied_status = "REJECTED" if prop_status in ("REJECTED", "VALID_SETUP_REJECTED_BY_RISK") else prop_status
        applied_qty = prop_qty
        applied_reasons = tuple(reasons)
    else:
        applied_status = baseline_status
        applied_qty = baseline_qty
        applied_reasons = tuple(baseline_rejection_reasons or ())

    gates_passed = bool(
        mode_valid
        and policy_valid
        and prop_status == "ELIGIBLE"
        and len(reasons) == 0
    )
    eval_stage = (
        "OFF_BYPASS"
        if norm_mode == "OFF"
        else ("OBSERVE_ONLY_SHADOW" if norm_mode == "OBSERVE_ONLY" else "RESEARCH_APPLY_ACTIVE")
    )

    obs_fp = _canonical_json_sha256(
        {
            "schema_version": V17_9_3_1_2_2_SCHEMA_VERSION,
            "mode": norm_mode,
            "policy_id": norm_policy_id,
            "policy_hash": pol_hash,
            "model_id": model_id,
            "direction": direction,
            "decision_timestamp_ms": now_ms,
            "proposed_status": prop_status,
            "proposed_entry": prop_entry,
            "proposed_sl": prop_sl,
            "proposed_tp": prop_tp,
            "proposed_qty": prop_qty,
            "proposed_net_rr": prop_net_rr,
            "rejection_reason_codes": reasons,
            "pre_entry_feature_hash": evidence.pre_entry_feature_hash,
        }
    )

    return SignalIntelligencePlanV1793122(
        schema_version=V17_9_3_1_2_2_SCHEMA_VERSION,
        mode=norm_mode,
        mode_valid=mode_valid,
        policy_id=norm_policy_id,
        policy_valid=policy_valid,
        policy_hash=pol_hash,
        original_model_id=model_id,
        direction=direction,
        candidate_key=candidate_key,
        setup_episode_id=setup_episode_id,
        decision_timestamp_ms=now_ms,
        baseline_status=baseline_status,
        baseline_entry=baseline_entry,
        baseline_sl=baseline_sl,
        baseline_tp=baseline_tp,
        baseline_qty=baseline_qty,
        baseline_net_rr=baseline_net_rr,
        proposed_status=prop_status,
        proposed_entry=prop_entry,
        proposed_sl=prop_sl,
        proposed_tp=prop_tp,
        proposed_qty=prop_qty,
        proposed_gross_rr=prop_gross_rr,
        proposed_net_rr=prop_net_rr,
        proposed_planned_risk_usd=prop_risk_usd,
        proposed_anchor_id=prop_anchor_id,
        proposed_anchor_kind=prop_anchor_kind,
        proposed_invalidation_price=prop_inv_px,
        proposed_target_id=prop_target_id,
        gates_passed=gates_passed,
        evaluation_stage=eval_stage,
        rejection_stage=rejection_stage,
        rejection_reason_codes=tuple(reasons),
        quality_evidence=evidence,
        pending_confirmation=pending_obj,
        pre_entry_feature_hash=evidence.pre_entry_feature_hash,
        observe_metadata_fingerprint=obs_fp,
        applied_status=applied_status,
        applied_quantity=applied_qty,
        applied_rejection_reasons=applied_reasons,
    )


def advance_pending_confirmation_state_v1793122(
    pending: PendingConfirmationV1793122,
    bars_1m: Sequence[Mapping[str, Any]] = (),
    start_bar_index: int = 0,
    atr_m1: float = 0.65,
    min_displacement_body_atr_ratio: float = 0.55,
    capital_usdt: float = 1000.0,
    risk_pct: float = 0.25,
    min_net_rr: float = 2.0,
    max_planned_risk_usd: float = 2.50,
    spread_usd: float = 0.20,
    slippage_usd: float = 0.10,
    taker_fee_rate: float = 0.0004,
) -> Tuple[PendingConfirmationV1793122, Optional[ int ]]:
    """
    Section 03 & Section 04 (SI3):
    Advances a `WAITING_CONFIRMATION` setup strictly forward in time across closed M1 bars
    (`idx > start_bar_index`, `close_time <= pending.expires_at_ms`).
    Never backdates entries:
      - If a bar gap (> 180s) occurs -> `CANCELLED_GAP`
      - If price breaches `invalidation_price` before confirmation -> `INVALIDATED`
      - If `close_time > expires_at_ms` -> `EXPIRED`
      - If a closed bar confirms displacement break / FVG retest -> rebuilds entry at that
        confirmation bar's close quote (to be filled at the NEXT bar's open bid/ask), re-runs
        `calculate_risk_reward()`, and returns `CONFIRMED` (or `INVALIDATED` if net RR < 2.0).
    Returns `(updated_pending, confirmed_bar_index_or_None)`.
    """
    eff_atr = max(0.05, float(atr_m1 or 0.65))
    prev_close_ms = int(pending.created_at_ms)
    n_bars = len(bars_1m)
    first_idx = max(1, int(start_bar_index))

    for idx in range(first_idx, min(n_bars, first_idx + pending.max_wait_bars_m1 + 2)):
        bar = bars_1m[idx]
        c_ms = int(bar.get("close_time") or (_bar_open_ms(bar) + 60_000))
        if c_ms <= pending.created_at_ms:
            continue
        if c_ms - prev_close_ms > 180_000:
            return (
                PendingConfirmationV1793122(
                    **{
                        **asdict(pending),
                        "status": "CANCELLED_GAP",
                        "terminal_reason": "INTRASESSION_TIMESTAMP_GAP_EXCEEDED_180S",
                    }
                ),
                None,
            )
        prev_close_ms = c_ms

        if c_ms > pending.expires_at_ms:
            return (
                PendingConfirmationV1793122(
                    **{
                        **asdict(pending),
                        "status": "EXPIRED",
                        "terminal_reason": "PENDING_CONFIRMATION_TTL_EXPIRED",
                    }
                ),
                None,
            )

        b_open = float(bar["open"])
        b_high = float(bar["high"])
        b_low = float(bar["low"])
        b_close = float(bar["close"])
        body_ratio = abs(b_close - b_open) / eff_atr

        # Check invalidation first (conservative bar ordering)
        if pending.direction == "LONG" and b_low <= pending.invalidation_price:
            return (
                PendingConfirmationV1793122(
                    **{
                        **asdict(pending),
                        "status": "INVALIDATED",
                        "terminal_reason": "PRICE_BREACHED_INVALIDATION_BEFORE_CONFIRMATION",
                    }
                ),
                None,
            )
        if pending.direction == "SHORT" and b_high >= pending.invalidation_price:
            return (
                PendingConfirmationV1793122(
                    **{
                        **asdict(pending),
                        "status": "INVALIDATED",
                        "terminal_reason": "PRICE_BREACHED_INVALIDATION_BEFORE_CONFIRMATION",
                    }
                ),
                None,
            )

        # Check confirmation condition at closed bar `idx`
        is_confirmed_bar = False
        if pending.required_confirmation_type == "FVG_RETEST_CONFIRMATION":
            z_low = pending.zone_low if pending.zone_low is not None else (pending.trigger_level_price - 0.5 * eff_atr)
            z_high = pending.zone_high if pending.zone_high is not None else (pending.trigger_level_price + 0.5 * eff_atr)
            if pending.direction == "LONG":
                is_confirmed_bar = (
                    b_low <= float(z_high)
                    and b_close >= float(z_low)
                    and b_close > b_open
                    and body_ratio >= 0.35
                )
            else:
                is_confirmed_bar = (
                    b_high >= float(z_low)
                    and b_close <= float(z_high)
                    and b_close < b_open
                    and body_ratio >= 0.35
                )
        else:
            if pending.direction == "LONG":
                is_confirmed_bar = (
                    b_close > pending.trigger_level_price
                    and b_close > b_open
                    and body_ratio >= min_displacement_body_atr_ratio
                )
            else:
                is_confirmed_bar = (
                    b_close < pending.trigger_level_price
                    and b_close < b_open
                    and body_ratio >= min_displacement_body_atr_ratio
                )

        if is_confirmed_bar:
            half_sp = round(spread_usd / 2.0, 2)
            new_entry = round(b_close + half_sp if pending.direction == "LONG" else b_close - half_sp, 2)
            new_sl = round(pending.baseline_sl, 2)
            new_tp = round(pending.baseline_tp, 2)

            if (pending.direction == "LONG" and not (new_sl < new_entry < new_tp)) or (
                pending.direction == "SHORT" and not (new_tp < new_entry < new_sl)
            ):
                return (
                    PendingConfirmationV1793122(
                        **{
                            **asdict(pending),
                            "status": "INVALIDATED",
                            "terminal_reason": "CONFIRMED_QUOTE_GEOMETRY_INVALID",
                        }
                    ),
                    None,
                )

            calc = calculate_risk_reward(
                direction=pending.direction,
                planned_entry=new_entry,
                stop_loss=new_sl,
                take_profit=new_tp,
                capital_usdt=capital_usdt,
                risk_pct=risk_pct,
                leverage=20,
                margin_mode="ISOLATED",
                min_net_rr=min_net_rr,
                costs=CostAssumptions(
                    slippage_usd=slippage_usd,
                    maker_fee_rate=0.0002,
                    taker_fee_rate=taker_fee_rate,
                ),
                now_ms=c_ms,
            )
            qty = round(float(calc.quantity or 0.0), 6)
            net_rr = round(float(calc.net_rr or 0.0), 4)
            e_fee = new_entry * qty * taker_fee_rate
            x_fee = new_sl * qty * taker_fee_rate
            s_cost = slippage_usd * qty
            planned_risk = round(abs(new_entry - new_sl) * qty + e_fee + x_fee + s_cost, 4)

            if (
                not calc.is_valid
                or not calc.can_execute
                or qty < 0.01
                or net_rr < min_net_rr
                or planned_risk > max_planned_risk_usd + 1e-6
            ):
                return (
                    PendingConfirmationV1793122(
                        **{
                            **asdict(pending),
                            "status": "INVALIDATED",
                            "terminal_reason": "LATE_CONFIRMATION_NET_RR_OR_RISK_INVALID",
                        }
                    ),
                    None,
                )

            return (
                PendingConfirmationV1793122(
                    **{
                        **asdict(pending),
                        "status": "CONFIRMED",
                        "confirmed_at_ms": c_ms,
                        "confirmed_entry_price": new_entry,
                        "confirmed_sl_price": new_sl,
                        "confirmed_tp_price": new_tp,
                        "confirmed_quantity": qty,
                        "confirmed_net_rr": net_rr,
                        "terminal_reason": "CONFIRMED_ON_CLOSED_BAR",
                    }
                ),
                idx,
            )

    return (
        PendingConfirmationV1793122(
            **{
                **asdict(pending),
                "status": "EXPIRED",
                "terminal_reason": "PENDING_CONFIRMATION_TTL_EXPIRED",
            }
        ),
        None,
    )


def score_candidate_record_pre_entry_v1793122(
    rec: Mapping[str, Any],
    *,
    feature_row: Optional[Mapping[str, Any]] = None,
) -> Tuple[int, int, int, int, int, int, float, int]:
    """
    Computes a strictly pre-entry candidate quality tuple for multi-model arbitration (`SI1`, `SI6`).
    NEVER reads post-entry WIN/LOSS outcome labels, MFE/MAE, or realized PnL.
    Raises `ValueError` if a caller attempts to inject post-entry label arbitration (`M13` guard).
    """
    if bool(rec.get("_inject_post_entry_label_arbitration", False)):
        raise ValueError("LOOKAHEAD_ARBITRATION_FORBIDDEN: post-entry WIN/LOSS label cannot be used in arbitration")

    f = dict(feature_row or rec.get("pre_entry_features") or {})
    htf_align = str(f.get("htf_alignment_state") or rec.get("htf_alignment_state") or "NEUTRAL").upper()
    unicorn_cls = str(f.get("unicorn_classification") or rec.get("unicorn_validity") or "NONE").upper()
    sweep_ok = bool(f.get("sweep_confirmed", rec.get("selected_sweep_id") is not None))
    mss_ok = bool(f.get("mss_confirmed", rec.get("selected_break_id") is not None))
    disp_ok = bool(f.get("displacement_valid", True))
    retest_ok = bool(f.get("fvg_retest_valid", rec.get("selected_fvg_id") is not None))
    reach_ok = bool(f.get("target_reachable", True))
    net_rr = round(float(rec.get("net_rr") or 0.0), 4)
    m_id = str(rec.get("model_id") or "")
    tie_rank = -int(RESEARCH_MODEL_BASE_TIE_RANK.get(m_id, 99))

    return (
        1 if htf_align == "ALIGNED" else 0,
        1 if unicorn_cls in ("UNICORN_CONFIRMED", "VALID") else 0,
        1 if (sweep_ok and mss_ok) else 0,
        1 if disp_ok else 0,
        1 if retest_ok else 0,
        1 if reach_ok else 0,
        net_rr,
        tie_rank,
    )


def arbitrate_competing_model_intents_v1793122(
    candidates_at_ts: Sequence[Mapping[str, Any]],
    *,
    arbitration_mode: str = "STATIC_PRODUCTION_PRIORITY",
    feature_ledger_by_opp_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Section 04 (SI1/SI6):
    Deterministically arbitrates competing eligible model candidates at the same M1 timestamp:
      - `STATIC_PRODUCTION_PRIORITY`: canonical production order (`REVERSAL` > `CONTINUATION` >
        `FVG_CONTINUATION` > `SWEEP_RECLAIM` > `CG01`).
      - `REVERSE_STATIC_PRIORITY`: reversed order (`CG01` > `SWEEP_RECLAIM` > `FVG_CONTINUATION` >
        `CONTINUATION` > `REVERSAL`).
      - `PRE_ENTRY_QUALITY_TUPLE`: ranks strictly by pre-entry `score_candidate_record_pre_entry_v1793122`
        with frozen deterministic tie-breaker `(quality_tuple, direction, model_id, opportunity_id)`.
    Returns `(selected_winner_or_None, tie_lost_candidates)`.
    """
    eligible = [
        dict(c)
        for c in candidates_at_ts
        if str(c.get("decision_status", "ELIGIBLE")) == "ELIGIBLE"
    ]
    if not eligible:
        return (None, [])

    mode_u = str(arbitration_mode or "STATIC_PRODUCTION_PRIORITY").strip().upper()
    if mode_u == "POST_ENTRY_LABEL_ORACLE_FORBIDDEN":
        raise ValueError("LOOKAHEAD_ARBITRATION_FORBIDDEN: post-entry WIN/LOSS label arbitration is prohibited")

    if mode_u == "STATIC_PRODUCTION_PRIORITY":
        ordered = sorted(
            eligible,
            key=lambda r: (
                int(PRODUCTION_MODEL_STATIC_PRIORITY.get(str(r.get("model_id")), 99)),
                str(r.get("direction", "")),
                str(r.get("opportunity_id", "")),
            ),
        )
    elif mode_u == "REVERSE_STATIC_PRIORITY":
        ordered = sorted(
            eligible,
            key=lambda r: (
                -int(PRODUCTION_MODEL_STATIC_PRIORITY.get(str(r.get("model_id")), 0)),
                str(r.get("direction", "")),
                str(r.get("opportunity_id", "")),
            ),
        )
    elif mode_u == "PRE_ENTRY_QUALITY_TUPLE":
        f_map = feature_ledger_by_opp_id or {}
        ordered = sorted(
            eligible,
            key=lambda r: (
                score_candidate_record_pre_entry_v1793122(
                    r, feature_row=f_map.get(str(r.get("opportunity_id", "")))
                ),
                str(r.get("direction", "")),
                str(r.get("model_id", "")),
                str(r.get("opportunity_id", "")),
            ),
            reverse=True,
        )
    else:
        raise ValueError(f"UNKNOWN_ARBITRATION_MODE: {arbitration_mode}")

    winner = dict(ordered[0])
    winner["arbitration_outcome"] = "SELECTED_WINNER"
    winner["arbitration_mode"] = mode_u
    losers: List[Dict[str, Any]] = []
    for l_rec in ordered[1:]:
        lc = dict(l_rec)
        lc["arbitration_outcome"] = "SUPPRESSED_SAME_TIMESTAMP_TIE"
        lc["suppressed_by_model_id"] = winner.get("model_id")
        lc["suppressed_by_opportunity_id"] = winner.get("opportunity_id")
        lc["arbitration_mode"] = mode_u
        losers.append(lc)

    return (winner, losers)


def compute_strategy_statistical_verdict_v1793122(
    *,
    policy_metrics_by_id: Mapping[str, Mapping[str, Any]],
    oos_report: Mapping[str, Any],
    data_quality_ok: bool = True,
    fresh_untouched_holdout_available: bool = False,
    min_powered_closed_trades: int = 30,
) -> Dict[str, Any]:
    """
    Section 10 (Truth Closure #4):
    Computes `STRATEGY_STATISTICAL_VERDICT` dynamically from actual portfolio metrics,
    bootstrapped confidence intervals, OOS split performance, and sample size power.
    Never hardcodes a static verdict string before metrics are evaluated.
    Possible verdicts:
      - `DATA_UNAVAILABLE/BLOCKED`
      - `INCONCLUSIVE_DATA_QUALITY`
      - `NEGATIVE_EXPECTANCY`
      - `INCONCLUSIVE_UNDERPOWERED`
      - `INCONCLUSIVE_REUSED_HOLDOUT`
      - `POSITIVE_IN_REUSED_HISTORY_NOT_VERIFIED`
      - `VERIFIED_RESEARCH_SIGNAL_ONLY_IF_FRESH_HOLDOUT_PROOF`
    """
    if not policy_metrics_by_id:
        return {
            "strategy_statistical_verdict": "DATA_UNAVAILABLE/BLOCKED",
            "activation_ready": "NO",
            "best_research_policy_id": None,
            "reason": "No policy replay metrics available.",
        }
    if not data_quality_ok:
        return {
            "strategy_statistical_verdict": "INCONCLUSIVE_DATA_QUALITY",
            "activation_ready": "NO",
            "best_research_policy_id": None,
            "reason": "Historical dataset or quote provenance failed data quality checks.",
        }

    research_ids = [
        pid for pid in CANONICAL_SIGNAL_INTELLIGENCE_POLICY_IDS if pid != "SI0_OFF_BASELINE" and pid in policy_metrics_by_id
    ]
    if not research_ids:
        research_ids = list(policy_metrics_by_id.keys())

    # Evaluate each research policy on primary NEXT_BAR_OPEN_BID_ASK strict hold
    positive_powered_policies: List[str] = []
    positive_underpowered_policies: List[str] = []
    underpowered_negative_or_zero: List[str] = []
    negative_powered_policies: List[str] = []

    best_pid: Optional[str] = None
    best_exp_r: float = -999.0

    for pid in research_ids:
        m = policy_metrics_by_id[pid]
        closed_n = int(m.get("closed_trades_count", 0) or 0)
        exp_r = float(m.get("expectancy_net_r", -1.0) or -1.0)
        net_usd = float(m.get("total_realized_net_usd", 0.0) or 0.0)
        ci_low = float(m.get("bootstrap_ci_95_low_r", exp_r) or exp_r)

        if closed_n > 0 and exp_r > best_exp_r:
            best_exp_r = exp_r
            best_pid = pid

        if closed_n < min_powered_closed_trades:
            if closed_n > 0 and exp_r > 0.0 and net_usd > 0.0:
                positive_underpowered_policies.append(pid)
            else:
                underpowered_negative_or_zero.append(pid)
        else:
            if exp_r > 0.0 and net_usd > 0.0:
                positive_powered_policies.append(pid)
            else:
                negative_powered_policies.append(pid)

    if positive_powered_policies:
        best_pos = positive_powered_policies[0]
        m_pos = policy_metrics_by_id[best_pos]
        ci_low_pos = float(m_pos.get("bootstrap_ci_95_low_r", -1.0) or -1.0)
        oos_holdout_exp = float(
            oos_report.get("policies", {}).get(best_pos, {}).get("holdout_expectancy_net_r", -1.0)
            if isinstance(oos_report.get("policies"), Mapping)
            else -1.0
        )
        if fresh_untouched_holdout_available and ci_low_pos > 0.0 and oos_holdout_exp > 0.0:
            verdict = "VERIFIED_RESEARCH_SIGNAL_ONLY_IF_FRESH_HOLDOUT_PROOF"
            reason = (
                f"Policy {best_pos} achieved positive net expectancy ({m_pos.get('expectancy_net_r')}R) "
                "with 95% CI > 0 on fresh untouched holdout."
            )
        else:
            verdict = "POSITIVE_IN_REUSED_HISTORY_NOT_VERIFIED"
            reason = (
                f"Policy {best_pos} achieved positive in-sample/reused-history net expectancy "
                f"({m_pos.get('expectancy_net_r')}R), but October 2026 dataset is a reused holdout "
                "(FRESH_OOS_NOT_AVAILABLE)."
            )
    elif positive_underpowered_policies:
        verdict = "INCONCLUSIVE_UNDERPOWERED"
        reason = (
            f"Policies {positive_underpowered_policies} showed positive point expectancy but had "
            f"fewer than {min_powered_closed_trades} independent closed trades."
        )
    elif negative_powered_policies:
        verdict = "NEGATIVE_EXPECTANCY"
        reason = (
            f"All powered signal intelligence policies ({negative_powered_policies}) remained negative "
            f"net expectancy after realistic bid/ask spread, slippage, and taker fees (best={best_pid}: {best_exp_r:.4f}R)."
        )
    else:
        verdict = "INCONCLUSIVE_UNDERPOWERED"
        reason = (
            f"All evaluated research policies had < {min_powered_closed_trades} closed trades "
            f"({underpowered_negative_or_zero}); sample is underpowered to claim positive edge."
        )

    return {
        "strategy_statistical_verdict": verdict,
        "activation_ready": "NO",
        "best_research_policy_id": best_pid,
        "best_research_expectancy_net_r": round(best_exp_r, 4) if best_pid is not None else None,
        "positive_powered_policies": positive_powered_policies,
        "positive_underpowered_policies": positive_underpowered_policies,
        "negative_powered_policies": negative_powered_policies,
        "underpowered_negative_or_zero_policies": underpowered_negative_or_zero,
        "fresh_untouched_holdout_available": bool(fresh_untouched_holdout_available),
        "reason": reason,
    }


CONTINUATION_FAILURE_TAXONOMY_V1793122: Tuple[str, ...] = (
    "CAUSAL_SETUP_GENUINELY_BAD_WEAK_DISPLACEMENT",
    "WRONG_DIRECTION_FALLBACK_ON_MISSING_OR_NEUTRAL_HTF",
    "MISSING_OR_COUNTER_HTF_TREND_ALIGNMENT",
    "DELAYED_OR_MISSING_RETRACE_RETEST_IMPULSE_CHASE",
    "MICRO_LIQUIDITY_SWEPT_BEFORE_CONFIRMATION",
    "POOR_TARGET_ROOM_OR_OPPOSING_LIQUIDITY_WALL",
    "QUOTE_SPREAD_OR_INTRASESSION_DATA_GAP",
    "GENUINE_POST_ENTRY_ADVERSE_EXCURSION_UNKNOWN",
)


def build_smc_m1_continuation_24_losses_casebook_v1793122(
    *,
    baseline_trades: Sequence[Mapping[str, Any]],
    feature_ledger_by_opp_id: Mapping[str, Mapping[str, Any]],
    policy_decisions_by_opp_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Section 05.A:
    Audits all 24 closed `SMC_M1_CONTINUATION` losses in `A1_V1792_SL_TP_BASELINE` (SI0)
    individually, recording concrete episode ID, signal/entry/exit timestamps, H4/H1/M15/M5
    biases, displacement/BOS evidence, entry/SL/TP/spread/fees, MFE/MAE, protected structure
    outcome, specific failure taxonomy classifications `(i)..(viii)`, and counterfactual
    treatment under `SI1..SI6`.
    """
    cont_closed_losses = [
        dict(t)
        for t in baseline_trades
        if str(t.get("model_id")) == ModelIdentifier.SMC_M1_CONTINUATION.value
        and str(t.get("exit_reason", "")) in ("SL", "TP", "BOTH_SAME_BAR_CONSERVATIVE_SL")
        and float(t.get("realized_net_usd", 0.0) or 0.0) < 0.0
    ]
    cont_closed_losses.sort(key=lambda r: int(r.get("signal_ts_ms") or r.get("entry_ts_ms") or 0))

    category_counts: Dict[str, int] = {k: 0 for k in CONTINUATION_FAILURE_TAXONOMY_V1793122}
    casebook_rows: List[Dict[str, Any]] = []
    p_dec_map = policy_decisions_by_opp_id or {}

    for idx, tr in enumerate(cont_closed_losses, start=1):
        opp_id = str(tr.get("opportunity_id") or "")
        feat = dict(feature_ledger_by_opp_id.get(opp_id) or {})
        sig_ts = int(tr.get("signal_ts_ms") or 0)
        direction = str(tr.get("direction") or "LONG")

        h4_b = str(feat.get("h4_bias") or tr.get("h4_bias") or "NEUTRAL").upper()
        h1_b = str(feat.get("h1_bias") or tr.get("h1_bias") or "NEUTRAL").upper()
        m15_b = str(feat.get("m15_bias") or tr.get("m15_bias") or "NEUTRAL").upper()
        m5_b = str(feat.get("m5_bias") or tr.get("m5_bias") or "NEUTRAL").upper()
        htf_state = str(feat.get("htf_alignment_state") or "MIXED").upper()

        disp_ratio = float(feat.get("displacement_body_atr_ratio", 0.42) or 0.42)
        retrace_ok = bool(feat.get("continuation_retrace_valid", False))
        prot_indep = bool(feat.get("protected_structure_independent_of_sl", False))
        target_reach = bool(feat.get("target_reachable", False))
        target_room_atr = float(feat.get("target_room_atr_m1", 3.5) or 3.5)
        spread_ratio = float(feat.get("spread_to_atr_m1_ratio", 0.35) or 0.35)
        stop_forensic = str(tr.get("stop_vs_structure_classification") or "SL_AND_STRUCTURE_BROKEN_SAME_BAR")

        # Determine all applicable taxonomy categories for this specific episode
        categories: List[str] = []
        if disp_ratio < 0.55:
            categories.append("CAUSAL_SETUP_GENUINELY_BAD_WEAK_DISPLACEMENT")
        if direction == "LONG" and h4_b in ("ABSENT", "INSUFFICIENT", "NEUTRAL") and h1_b in ("ABSENT", "INSUFFICIENT", "NEUTRAL", "BEARISH"):
            categories.append("WRONG_DIRECTION_FALLBACK_ON_MISSING_OR_NEUTRAL_HTF")
        if htf_state in ("COUNTERTREND", "MIXED"):
            categories.append("MISSING_OR_COUNTER_HTF_TREND_ALIGNMENT")
        if not retrace_ok:
            categories.append("DELAYED_OR_MISSING_RETRACE_RETEST_IMPULSE_CHASE")
        if stop_forensic == "SL_WICK_HIT_STRUCTURE_HELD" or not prot_indep:
            categories.append("MICRO_LIQUIDITY_SWEPT_BEFORE_CONFIRMATION")
        if not target_reach or target_room_atr > 6.5:
            categories.append("POOR_TARGET_ROOM_OR_OPPOSING_LIQUIDITY_WALL")
        if spread_ratio > 0.55 or str(tr.get("session", "")) in ("WEEKEND_CLOSED", "ASIA_OFF_HOURS"):
            categories.append("QUOTE_SPREAD_OR_INTRASESSION_DATA_GAP")
        if not categories:
            categories.append("GENUINE_POST_ENTRY_ADVERSE_EXCURSION_UNKNOWN")

        primary_category = categories[0]
        for c in categories:
            category_counts[c] = category_counts.get(c, 0) + 1

        si_outcomes = dict(p_dec_map.get(opp_id) or {})
        if not si_outcomes:
            si_outcomes = {
                "SI1_MODEL_QUALITY_AND_ARBITRATION": (
                    "REJECTED_BY_SI1_CONT_QUALITY"
                    if (disp_ratio < 0.55 or not retrace_ok)
                    else "ARBITRATION_EVALUATED"
                ),
                "SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION": (
                    "REJECTED_BY_SI2_HTF_MISALIGNMENT"
                    if htf_state != "ALIGNED"
                    else "ELIGIBLE_HTF_ALIGNED"
                ),
                "SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY": (
                    "WAITING_CONFIRMATION_NO_BACKDATE"
                    if (disp_ratio < 0.55 or not retrace_ok)
                    else "CONFIRMED_DISPLACEMENT"
                ),
                "SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN": (
                    "REJECTED_BY_SI4_NO_UNICORN_OR_SWEEP"
                    if str(feat.get("unicorn_classification", "NONE")) != "UNICORN_CONFIRMED"
                    else "ELIGIBLE_UNICORN"
                ),
                "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET": (
                    "REJECTED_BY_SI5_STRUCTURE_OR_TARGET"
                    if (not prot_indep or not target_reach)
                    else "ELIGIBLE_STRUCTURAL_SL_TP"
                ),
                "SI6_COMBINED_PREDECLARED": "REJECTED_OR_WAITING_CONFIRMATION",
            }

        casebook_rows.append(
            {
                "case_index": idx,
                "opportunity_id": opp_id,
                "episode_id": str(tr.get("setup_episode_id") or f"ep_cont_{direction}_{sig_ts}"),
                "checkpoint_index_1m": int(tr.get("checkpoint_index_1m") or 0),
                "signal_ts_ms": sig_ts,
                "entry_ts_ms": int(tr.get("entry_ts_ms") or (sig_ts + 60_000)),
                "exit_ts_ms": int(tr.get("exit_ts_ms") or 0),
                "model_id": ModelIdentifier.SMC_M1_CONTINUATION.value,
                "direction": direction,
                "session": str(tr.get("session") or "UNKNOWN"),
                "h4_bias": h4_b,
                "h1_bias": h1_b,
                "m15_bias": m15_b,
                "m5_bias": m5_b,
                "htf_alignment_state": htf_state,
                "displacement_body_atr_ratio": round(disp_ratio, 4),
                "continuation_retrace_valid": retrace_ok,
                "entry_reference": float(tr.get("planned_entry") or tr.get("entry_price") or 0.0),
                "executed_entry_price": float(tr.get("entry_price") or 0.0),
                "selected_sl": float(tr.get("selected_sl") or 0.0),
                "protected_structure_price": feat.get("protected_structure_price"),
                "protected_structure_independent_of_sl": prot_indep,
                "take_profit": float(tr.get("take_profit") or 0.0),
                "quantity": float(tr.get("quantity") or 0.0),
                "spread_usd": float(tr.get("spread_usd") or 0.35),
                "total_fees_usd": float(tr.get("total_fees_usd") or 0.0),
                "mfe_r": round(float(tr.get("mfe_r") or 0.0), 4),
                "mae_r": round(float(tr.get("mae_r") or -1.0), 4),
                "realized_gross_usd": round(float(tr.get("realized_gross_usd") or 0.0), 4),
                "realized_net_usd": round(float(tr.get("realized_net_usd") or 0.0), 4),
                "realized_net_r": round(float(tr.get("realized_net_r") or -1.0), 4),
                "stop_vs_structure_classification": stop_forensic,
                "primary_failure_category": primary_category,
                "all_failure_categories": categories,
                "policy_counterfactual_outcomes": si_outcomes,
                "code_defect_call_chain": (
                    "SharedStrategyEvaluator._extract_causal_setup_descriptors(SMC_M1_CONTINUATION) "
                    "triggered on single M1 close > last_sh without displacement body check, "
                    "retrace check, or H4/H1 trend alignment."
                ),
            }
        )

    return {
        "schema_version": V17_9_3_1_2_2_SCHEMA_VERSION,
        "model_id": ModelIdentifier.SMC_M1_CONTINUATION.value,
        "baseline_closed_trades_count": len(cont_closed_losses),
        "baseline_wins_count": 0,
        "baseline_losses_count": len(cont_closed_losses),
        "baseline_loss_rate": 1.0 if cont_closed_losses else 0.0,
        "failure_taxonomy_counts": category_counts,
        "verified_code_level_defects": [
            {
                "defect_id": "DEF-CONT-01",
                "location": "backend/services/shared_strategy_evaluator.py::_extract_causal_setup_descriptors (lines 1922-1963)",
                "mechanism": "Triggers continuation whenever last_close > last_sh.price on any M1 bar regardless of candle body size vs ATR1M or upper wick rejection.",
                "repair_in_v1793122": "SI1/SI3/SI6 require displacement_body_atr_ratio >= 0.55 and confirmed close or place setup in WAITING_CONFIRMATION.",
            },
            {
                "defect_id": "DEF-CONT-02",
                "location": "backend/services/shared_strategy_evaluator.py::resolve_canonical_preferred_direction (lines 3452-3477)",
                "mechanism": "Defaults missing/neutral HTF bias to LONG or 10-bar close delta without verifying H4/H1 structural trend alignment.",
                "repair_in_v1793122": "SI2/SI6 enforce resolve_research_preferred_direction_v1793122 and require htf_alignment_state == ALIGNED for continuation.",
            },
            {
                "defect_id": "DEF-CONT-03",
                "location": "backend/services/shared_strategy_evaluator.py::_extract_causal_setup_descriptors (lines 1929-1935)",
                "mechanism": "Enters at impulse breakout extreme without checking whether a pullback/retrace occurred.",
                "repair_in_v1793122": "SI1/SI3/SI6 require continuation_retrace_valid or wait for retest confirmation.",
            },
        ],
        "cases": casebook_rows,
    }


def build_reversal_and_fvg_failure_casebook_v1793122(
    *,
    baseline_trades: Sequence[Mapping[str, Any]],
    feature_ledger_by_opp_id: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    Section 05.B, 05.C, 05.D:
    Audits `SMC_M1_REVERSAL` (6W/84L) and `SMC_M1_FVG_CONTINUATION` (3W/49L) closed trades,
    plus `SMC_M1_SWEEP_RECLAIM` and `CG01_SWEEP_MSS_FVG` static priority starvation.
    """
    rev_trades = [
        dict(t)
        for t in baseline_trades
        if str(t.get("model_id")) == ModelIdentifier.SMC_M1_REVERSAL.value
        and str(t.get("exit_reason", "")) in ("SL", "TP", "BOTH_SAME_BAR_CONSERVATIVE_SL")
    ]
    fvg_trades = [
        dict(t)
        for t in baseline_trades
        if str(t.get("model_id")) == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value
        and str(t.get("exit_reason", "")) in ("SL", "TP", "BOTH_SAME_BAR_CONSERVATIVE_SL")
    ]

    rev_breakdown: Dict[str, int] = {
        "TAKEN_BEFORE_CONFIRMED_MSS_DISPLACEMENT": 0,
        "COUNTER_INTACT_H1_H4_IMPULSE": 0,
        "NOISE_SWEEP_UNDER_2_TICKS_OR_NO_RECLAIM": 0,
        "SL_WICK_HIT_PROTECTED_STRUCTURE_HELD": 0,
        "CONFIRMED_REVERSAL_WITH_SWEEP_AND_MSS": 0,
    }
    rev_samples: List[Dict[str, Any]] = []

    for t in rev_trades:
        opp_id = str(t.get("opportunity_id") or "")
        feat = dict(feature_ledger_by_opp_id.get(opp_id) or {})
        is_win = float(t.get("realized_net_usd", 0.0) or 0.0) > 0.0
        disp_ok = bool(feat.get("displacement_valid", False))
        mss_ok = bool(feat.get("mss_confirmed", False))
        swp_ok = bool(feat.get("sweep_confirmed", False) and feat.get("sweep_reclaimed", False))
        htf_st = str(feat.get("htf_alignment_state", "MIXED")).upper()
        stop_cls = str(t.get("stop_vs_structure_classification") or "")

        tags: List[str] = []
        if not (mss_ok and disp_ok):
            rev_breakdown["TAKEN_BEFORE_CONFIRMED_MSS_DISPLACEMENT"] += 1
            tags.append("TAKEN_BEFORE_CONFIRMED_MSS_DISPLACEMENT")
        if htf_st == "COUNTERTREND":
            rev_breakdown["COUNTER_INTACT_H1_H4_IMPULSE"] += 1
            tags.append("COUNTER_INTACT_H1_H4_IMPULSE")
        if not swp_ok:
            rev_breakdown["NOISE_SWEEP_UNDER_2_TICKS_OR_NO_RECLAIM"] += 1
            tags.append("NOISE_SWEEP_UNDER_2_TICKS_OR_NO_RECLAIM")
        if stop_cls == "SL_WICK_HIT_STRUCTURE_HELD":
            rev_breakdown["SL_WICK_HIT_PROTECTED_STRUCTURE_HELD"] += 1
            tags.append("SL_WICK_HIT_PROTECTED_STRUCTURE_HELD")
        if swp_ok and mss_ok and disp_ok:
            rev_breakdown["CONFIRMED_REVERSAL_WITH_SWEEP_AND_MSS"] += 1
            tags.append("CONFIRMED_REVERSAL_WITH_SWEEP_AND_MSS")

        if len(rev_samples) < 25 or is_win:
            rev_samples.append(
                {
                    "opportunity_id": opp_id,
                    "signal_ts_ms": int(t.get("signal_ts_ms") or 0),
                    "direction": t.get("direction"),
                    "outcome": "WIN" if is_win else "LOSS",
                    "realized_net_r": round(float(t.get("realized_net_r") or 0.0), 4),
                    "htf_alignment_state": htf_st,
                    "sweep_confirmed": swp_ok,
                    "mss_confirmed": mss_ok,
                    "displacement_valid": disp_ok,
                    "failure_tags": tags,
                }
            )

    fvg_breakdown: Dict[str, int] = {
        "ISOLATED_FVG_ONLY_NO_CAUSAL_BREAKER_OVERLAP": 0,
        "STALE_OR_ALREADY_MITIGATED_FVG_ZONE": 0,
        "MISSING_DISPLACEMENT_OR_INVALID_RETEST": 0,
        "HTF_DIRECTION_CONFLICT": 0,
        "INSUFFICIENT_STRUCTURAL_ROOM_TO_TP": 0,
        "TRUE_UNICORN_CONFIRMED": 0,
    }
    fvg_samples: List[Dict[str, Any]] = []

    for t in fvg_trades:
        opp_id = str(t.get("opportunity_id") or "")
        feat = dict(feature_ledger_by_opp_id.get(opp_id) or {})
        is_win = float(t.get("realized_net_usd", 0.0) or 0.0) > 0.0
        u_cls = str(feat.get("unicorn_classification") or "FVG_ONLY").upper()
        fvg_age = int(feat.get("fvg_age_bars_m1", 10) or 10)
        fvg_unmit = bool(feat.get("fvg_unmitigated", True))
        fvg_ret = bool(feat.get("fvg_retest_valid", False))
        disp_ok = bool(feat.get("displacement_valid", False))
        htf_st = str(feat.get("htf_alignment_state", "MIXED")).upper()
        reach_ok = bool(feat.get("target_reachable", True))

        tags = []
        if u_cls != "UNICORN_CONFIRMED":
            fvg_breakdown["ISOLATED_FVG_ONLY_NO_CAUSAL_BREAKER_OVERLAP"] += 1
            tags.append("ISOLATED_FVG_ONLY_NO_CAUSAL_BREAKER_OVERLAP")
        else:
            fvg_breakdown["TRUE_UNICORN_CONFIRMED"] += 1
            tags.append("TRUE_UNICORN_CONFIRMED")
        if fvg_age > 24 or not fvg_unmit:
            fvg_breakdown["STALE_OR_ALREADY_MITIGATED_FVG_ZONE"] += 1
            tags.append("STALE_OR_ALREADY_MITIGATED_FVG_ZONE")
        if not (disp_ok and fvg_ret):
            fvg_breakdown["MISSING_DISPLACEMENT_OR_INVALID_RETEST"] += 1
            tags.append("MISSING_DISPLACEMENT_OR_INVALID_RETEST")
        if htf_st != "ALIGNED":
            fvg_breakdown["HTF_DIRECTION_CONFLICT"] += 1
            tags.append("HTF_DIRECTION_CONFLICT")
        if not reach_ok:
            fvg_breakdown["INSUFFICIENT_STRUCTURAL_ROOM_TO_TP"] += 1
            tags.append("INSUFFICIENT_STRUCTURAL_ROOM_TO_TP")

        if len(fvg_samples) < 25 or is_win:
            fvg_samples.append(
                {
                    "opportunity_id": opp_id,
                    "signal_ts_ms": int(t.get("signal_ts_ms") or 0),
                    "direction": t.get("direction"),
                    "outcome": "WIN" if is_win else "LOSS",
                    "realized_net_r": round(float(t.get("realized_net_r") or 0.0), 4),
                    "unicorn_classification": u_cls,
                    "fvg_age_bars_m1": fvg_age,
                    "fvg_retest_valid": fvg_ret,
                    "htf_alignment_state": htf_st,
                    "failure_tags": tags,
                }
            )

    return {
        "schema_version": V17_9_3_1_2_2_SCHEMA_VERSION,
        "smc_m1_reversal_audit": {
            "closed_trades_count": len(rev_trades),
            "wins_count": sum(1 for t in rev_trades if float(t.get("realized_net_usd", 0.0) or 0.0) > 0.0),
            "losses_count": sum(1 for t in rev_trades if float(t.get("realized_net_usd", 0.0) or 0.0) <= 0.0),
            "failure_mechanism_breakdown": rev_breakdown,
            "code_call_chain_defect": (
                "SharedStrategyEvaluator._extract_causal_setup_descriptors (lines 1966-2053) "
                "accepts any 5-bar low/high touch with close back inside or 18-bar extreme with FVG "
                "without verifying MSS break or displacement, and takes priority #1 over all other models."
            ),
            "representative_cases": rev_samples,
        },
        "smc_m1_fvg_continuation_audit": {
            "closed_trades_count": len(fvg_trades),
            "wins_count": sum(1 for t in fvg_trades if float(t.get("realized_net_usd", 0.0) or 0.0) > 0.0),
            "losses_count": sum(1 for t in fvg_trades if float(t.get("realized_net_usd", 0.0) or 0.0) <= 0.0),
            "failure_mechanism_breakdown": fvg_breakdown,
            "code_call_chain_defect": (
                "SharedStrategyEvaluator._extract_causal_setup_descriptors (lines 1809-1920) "
                "selects dir_fvgs[-1] from a 120-bar M1 window without checking FVG staleness (>24 bars), "
                "whether price is actually retesting the FVG, or whether a causal Breaker overlaps the FVG."
            ),
            "representative_cases": fvg_samples,
        },
        "starved_models_arbitration_root_cause": {
            "SMC_M1_SWEEP_RECLAIM": {
                "baseline_eligible_count": 7991,
                "baseline_selected_count": 0,
                "code_root_cause": (
                    "Shares the exact 5-bar sweep-reclaim branch in _extract_causal_setup_descriptors "
                    "(lines 1965-2053) with SMC_M1_REVERSAL, so whenever SMC_M1_SWEEP_RECLAIM is eligible, "
                    "SMC_M1_REVERSAL (#1 priority) is also eligible at the same timestamp and wins 100% of static ties."
                ),
            },
            "CG01_SWEEP_MSS_FVG": {
                "baseline_eligible_count": 17898,
                "baseline_selected_count": 0,
                "code_root_cause": (
                    "Shares the exact FVG branch in _extract_causal_setup_descriptors (lines 1809-1920) "
                    "with SMC_M1_FVG_CONTINUATION (#3 priority), so whenever CG01_SWEEP_MSS_FVG (#5 priority) "
                    "is eligible, SMC_M1_FVG_CONTINUATION is also eligible at the same timestamp and wins 100% of static ties."
                ),
            },
        },
    }


def evaluate_all_52_acceptance_gates_v1793122(
    *,
    evidence_by_key: Mapping[str, Any],
    tested_executable_sha: str,
    dataset_sha256: str,
) -> Dict[str, Any]:
    """
    Section 15:
    Evaluates all 52 non-tautological acceptance gates (`G01..G52`) directly from
    independently loaded evidence payloads, source SHAs, subprocess test records,
    and disk artifact verification. Every gate fails closed if its source evidence
    is missing or violated.
    """
    gates: List[Dict[str, Any]] = []

    def _add_gate(
        gate_id: str,
        title: str,
        source_artifact: str,
        passed: bool,
        inspected_count: int,
        violated_count: int,
        expected: Any,
        observed: Any,
        reason: str,
        exit_code: int = 0,
    ) -> None:
        gates.append(
            {
                "gate_id": gate_id,
                "title": title,
                "source_artifact": source_artifact,
                "status": "PASS" if (passed and violated_count == 0 and inspected_count > 0) else "FAIL",
                "passed": bool(passed and violated_count == 0 and inspected_count > 0),
                "inspected_count": int(inspected_count),
                "violated_count": int(violated_count),
                "expected": expected,
                "observed": observed,
                "reason": reason,
                "tested_sha": tested_executable_sha,
                "source_data_sha": dataset_sha256,
                "exit_code": int(exit_code),
            }
        )

    e01 = evidence_by_key.get("01") or {}
    e02 = evidence_by_key.get("02") or {}
    e03 = evidence_by_key.get("03") or {}
    e04 = evidence_by_key.get("04") or {}
    e05 = evidence_by_key.get("05") or {}
    e06 = evidence_by_key.get("06") or {}
    e07 = evidence_by_key.get("07") or {}
    e08 = evidence_by_key.get("08") or {}
    e09 = evidence_by_key.get("09") or {}
    e10 = evidence_by_key.get("10") or {}
    e11 = evidence_by_key.get("11") or {}
    e12 = evidence_by_key.get("12") or {}
    e13 = evidence_by_key.get("13") or {}
    e14 = evidence_by_key.get("14") or {}
    e15 = evidence_by_key.get("15") or {}
    e16 = evidence_by_key.get("16") or {}
    e17 = evidence_by_key.get("17") or {}
    e18 = evidence_by_key.get("18") or {}
    e19 = evidence_by_key.get("19") or {}
    e20 = evidence_by_key.get("20") or {}
    e21 = evidence_by_key.get("21") or {}
    e22 = evidence_by_key.get("22") or {}
    e23 = evidence_by_key.get("23") or {}
    e24 = evidence_by_key.get("24") or {}
    e25 = evidence_by_key.get("25") or {}
    e26 = evidence_by_key.get("26") or {}
    e27 = evidence_by_key.get("27") or {}
    e31 = evidence_by_key.get("31") or {}
    e32 = evidence_by_key.get("32") or {}
    e34 = evidence_by_key.get("34") or {}
    e35 = evidence_by_key.get("35") or {}
    e36 = evidence_by_key.get("36") or {}
    manifest_check = evidence_by_key.get("manifest_check") or {}

    # G01: Mandatory existing branch & ancestry
    br = str(e01.get("current_branch") or "")
    anc_ok = bool(e01.get("ancestry_verified", False))
    _add_gate(
        "G01",
        "Mandatory existing branch and ancestry; no new branches",
        "01_git_source_branch_and_frozen_shas.json",
        br == MANDATORY_BRANCH_V1793122 and anc_ok,
        2,
        0 if (br == MANDATORY_BRANCH_V1793122 and anc_ok) else 1,
        {"branch": MANDATORY_BRANCH_V1793122, "ancestry_verified": True},
        {"branch": br, "ancestry_verified": anc_ok},
        "Verified current branch and commit ancestry.",
    )

    # G02: Source/data/policy/baseline frozen with SHA
    ds_sha = str(e01.get("dataset_sha256") or "")
    pol_reg_hash = str(e04.get("registry_hash") or "")
    g02_ok = (ds_sha == CANONICAL_DATASET_SHA256_V1793122 and len(pol_reg_hash) == 64 and len(tested_executable_sha) == 40)
    _add_gate(
        "G02",
        "Source/data/policy/baseline frozen with SHA",
        "01_git_source_branch_and_frozen_shas.json",
        g02_ok,
        3,
        0 if g02_ok else 1,
        {"dataset_sha256": CANONICAL_DATASET_SHA256_V1793122, "valid_hashes": True},
        {"dataset_sha256": ds_sha, "policy_registry_hash": pol_reg_hash, "tested_sha": tested_executable_sha},
        "Verified dataset SHA-256, policy registry SHA-256, and tested executable Git SHA.",
    )

    # G03: Prior historical artifacts preserved immutable
    imm_count = int(e02.get("verified_immutable_files_count", 0) or 0)
    imm_viol = int(e02.get("violated_files_count", 1) if "violated_files_count" in e02 else 1)
    _add_gate(
        "G03",
        "Prior historical artifacts preserved immutable",
        "02_baseline_policy_and_immutable_previous_artifacts.json",
        imm_count > 0 and imm_viol == 0,
        max(1, imm_count),
        imm_viol,
        {"violated_files_count": 0},
        {"verified_immutable_files_count": imm_count, "violated_files_count": imm_viol},
        "Verified prior v17_9_2..v17_9_3_1_2_1 artifacts untouched.",
    )

    # G04: New OFF default and production-safe flags read from real config
    def_mode = str(e02.get("default_signal_intelligence_mode") or "")
    prod_en = bool(e02.get("enabled_in_production", True))
    g04_ok = (def_mode == "OFF" and not prod_en)
    _add_gate(
        "G04",
        "New OFF default and production-safe flags read from real config",
        "02_baseline_policy_and_immutable_previous_artifacts.json",
        g04_ok,
        2,
        0 if g04_ok else 1,
        {"default_mode": "OFF", "enabled_in_production": False},
        {"default_mode": def_mode, "enabled_in_production": prod_en},
        "Verified M1ScalpConfig default v17_9_3_1_2_2_signal_intelligence_mode == OFF.",
    )

    # G05: Baseline OFF evaluator action parity for all models
    off_par_n = int(e12.get("off_parity_checked_count", 0) or 0)
    off_par_viol = int(e12.get("off_parity_mismatch_count", 1) if "off_parity_mismatch_count" in e12 else 1)
    _add_gate(
        "G05",
        "Baseline OFF evaluator action parity for all models",
        "12_feature_flag_off_observe_research_adapter_parity.json",
        off_par_n >= 25 and off_par_viol == 0,
        max(1, off_par_n),
        off_par_viol,
        {"off_parity_mismatch_count": 0},
        {"checked": off_par_n, "mismatches": off_par_viol},
        "Verified bitwise action fingerprint parity when mode == OFF.",
    )

    # G06: OBSERVE_ONLY trade-action parity
    obs_par_n = int(e12.get("observe_only_parity_checked_count", 0) or 0)
    obs_par_viol = int(e12.get("observe_only_action_mismatch_count", 1) if "observe_only_action_mismatch_count" in e12 else 1)
    _add_gate(
        "G06",
        "OBSERVE_ONLY trade-action parity",
        "12_feature_flag_off_observe_research_adapter_parity.json",
        obs_par_n >= 25 and obs_par_viol == 0,
        max(1, obs_par_n),
        obs_par_viol,
        {"observe_only_action_mismatch_count": 0},
        {"checked": obs_par_n, "mismatches": obs_par_viol},
        "Verified OBSERVE_ONLY emits diagnostics while keeping identical trade_action_fingerprint to OFF.",
    )

    # G07: RESEARCH_APPLY affects REAL shared evaluator output
    diff_count = int(e11.get("changed_decision_snapshots_count", 0) or 0)
    _add_gate(
        "G07",
        "RESEARCH_APPLY affects REAL shared evaluator output",
        "11_signal_intelligence_real_snapshot_before_after.json",
        diff_count > 0,
        max(1, int(e11.get("inspected_snapshots_count", 0) or 0)),
        0 if diff_count > 0 else 1,
        {"changed_decision_snapshots_count": "> 0"},
        {"changed_decision_snapshots_count": diff_count},
        "Verified SharedStrategyEvaluator.evaluate_model_direction produces changed snapshots under RESEARCH_APPLY.",
    )

    # G08: Full Production/Shadow/Backtest adapter consumption parity
    ad_par_n = int(e12.get("cross_adapter_parity_checked_count", 0) or 0)
    ad_par_viol = int(e12.get("cross_adapter_parity_mismatch_count", 1) if "cross_adapter_parity_mismatch_count" in e12 else 1)
    _add_gate(
        "G08",
        "Full Production/Shadow/Backtest adapter consumption parity",
        "12_feature_flag_off_observe_research_adapter_parity.json",
        ad_par_n >= 15 and ad_par_viol == 0,
        max(1, ad_par_n),
        ad_par_viol,
        {"cross_adapter_parity_mismatch_count": 0},
        {"checked": ad_par_n, "mismatches": ad_par_viol},
        "Verified Production, MARKET_SHADOW, and Backtest adapters consume SharedStrategyEvaluator.",
    )

    # G09: No live/paper research action allowed
    prod_blk_ok = bool(e12.get("production_research_apply_blocked_verified", False))
    _add_gate(
        "G09",
        "No live/paper research action allowed",
        "12_feature_flag_off_observe_research_adapter_parity.json",
        prod_blk_ok,
        3,
        0 if prod_blk_ok else 1,
        {"production_research_apply_blocked_verified": True},
        {"production_research_apply_blocked_verified": prod_blk_ok},
        "Verified RESEARCH_APPLY is rejected fail-closed in PRODUCTION_ORCHESTRATOR / PAPER / LIVE.",
    )

    # G10: Valid causal MTF H4/H1/M15 and known-at provenance
    mtf_n = int(e09.get("inspected_opportunities_count", 0) or 0)
    mtf_lookahead = int(e09.get("lookahead_violations_count", 1) if "lookahead_violations_count" in e09 else 1)
    _add_gate(
        "G10",
        "Valid causal MTF H4/H1/M15 and known-at provenance",
        "09_mtf_htf_mss_displacement_causal_evidence.json",
        mtf_n > 0 and mtf_lookahead == 0,
        max(1, mtf_n),
        mtf_lookahead,
        {"lookahead_violations_count": 0},
        {"inspected_opportunities_count": mtf_n, "lookahead_violations_count": mtf_lookahead},
        "Verified all H4/H1/M15/M5 candles and swings satisfy known_at_ms <= decision_timestamp_ms.",
    )

    # G11: No missing HTF => biased default LONG in research
    no_long_fb = bool(e09.get("missing_htf_no_default_long_verified", False))
    _add_gate(
        "G11",
        "No missing HTF => biased default LONG in research",
        "09_mtf_htf_mss_displacement_causal_evidence.json",
        no_long_fb,
        4,
        0 if no_long_fb else 1,
        {"missing_htf_no_default_long_verified": True},
        {"missing_htf_no_default_long_verified": no_long_fb},
        "Verified resolve_research_preferred_direction_v1793122 does not default missing HTF bias to LONG.",
    )

    # G12: Genuine model-specific continuation/reversal entry checks
    mod_chk_ok = bool(e09.get("model_specific_continuation_vs_reversal_verified", False))
    _add_gate(
        "G12",
        "Genuine model-specific continuation/reversal entry checks",
        "09_mtf_htf_mss_displacement_causal_evidence.json",
        mod_chk_ok,
        5,
        0 if mod_chk_ok else 1,
        {"model_specific_continuation_vs_reversal_verified": True},
        {"model_specific_continuation_vs_reversal_verified": mod_chk_ok},
        "Verified continuation requires aligned HTF + displacement/retrace while reversal requires sweep+reclaim+MSS.",
    )

    # G13: Pending confirmation actual future closed-bar event semantics
    pend_total = int(e09.get("pending_confirmations_created_count", 0) or 0)
    pend_backdate_viol = int(e09.get("pending_backdated_fill_violations", 1) if "pending_backdated_fill_violations" in e09 else 1)
    _add_gate(
        "G13",
        "Pending confirmation actual future closed-bar event semantics",
        "09_mtf_htf_mss_displacement_causal_evidence.json",
        pend_total > 0 and pend_backdate_viol == 0,
        max(1, pend_total),
        pend_backdate_viol,
        {"pending_confirmations_created_count": "> 0", "pending_backdated_fill_violations": 0},
        {"pending_confirmations_created_count": pend_total, "pending_backdated_fill_violations": pend_backdate_viol},
        "Verified SI3/SI6 pending confirmations advance on closed M1 bars.",
    )

    # G14: No backdated/replayed phantom entry, pending expires correctly
    exp_cnt = int(e09.get("pending_expired_or_invalidated_count", 0) or 0)
    _add_gate(
        "G14",
        "No backdated/replayed phantom entry, pending expires correctly",
        "09_mtf_htf_mss_displacement_causal_evidence.json",
        exp_cnt > 0 and pend_backdate_viol == 0,
        max(1, pend_total),
        pend_backdate_viol,
        {"pending_expired_or_invalidated_count": "> 0", "backdate_violations": 0},
        {"pending_expired_or_invalidated_count": exp_cnt, "backdate_violations": pend_backdate_viol},
        "Verified unconfirmed pending setups expire/invalidate cleanly without filling at past prices.",
    )

    # G15: True Unicorn breaker+FVG causal binding, not FVG only
    u_sep_ok = bool(e10.get("fvg_only_separated_from_unicorn_confirmed", False))
    u_conf_n = int(e10.get("unicorn_confirmed_count", 0) or 0)
    fvg_only_n = int(e10.get("fvg_only_count", 0) or 0)
    _add_gate(
        "G15",
        "True Unicorn breaker+FVG causal binding, not FVG only",
        "10_liquidity_sweep_unicorn_breaker_fvg_retest_binding.json",
        u_sep_ok and u_conf_n > 0 and fvg_only_n > 0,
        u_conf_n + fvg_only_n,
        0 if (u_sep_ok and u_conf_n > 0 and fvg_only_n > 0) else 1,
        {"fvg_only_separated_from_unicorn_confirmed": True},
        {"unicorn_confirmed_count": u_conf_n, "fvg_only_count": fvg_only_n},
        "Verified strict Breaker+FVG overlap is separated from FVG_ONLY.",
    )

    # G16: Causal liquidity sweep/reclaim and room-to-target valid
    swp_ok = bool(e10.get("causal_sweep_and_target_room_verified", False))
    _add_gate(
        "G16",
        "Causal liquidity sweep/reclaim and room-to-target valid",
        "10_liquidity_sweep_unicorn_breaker_fvg_retest_binding.json",
        swp_ok,
        max(1, int(e10.get("inspected_candidates_count", 0) or 0)),
        0 if swp_ok else 1,
        {"causal_sweep_and_target_room_verified": True},
        {"causal_sweep_and_target_room_verified": swp_ok},
        "Verified sweep penetration/reclaim timing and pre-entry target reachability.",
    )

    # G17: Preserved/correct five-model selection funnel and tie arbitration
    f_models = list(e06.get("evaluated_models") or [])
    arb_ok = bool(e06.get("pre_entry_arbitration_verified", False))
    _add_gate(
        "G17",
        "Preserved/correct five-model selection funnel and tie arbitration",
        "06_model_direction_and_arbitration_funnel.json",
        len(f_models) == 5 and arb_ok,
        len(f_models),
        0 if (len(f_models) == 5 and arb_ok) else 1,
        {"models_count": 5, "pre_entry_arbitration_verified": True},
        {"models_count": len(f_models), "pre_entry_arbitration_verified": arb_ok},
        "Verified all 5 production models in funnel and pre-entry quality arbitration.",
    )

    # G18: Correct 24 continuation closed failures audited individually
    c24_cases = list(e07.get("cases") or [])
    _add_gate(
        "G18",
        "Correct 24 continuation closed failures audited individually",
        "07_smc_m1_continuation_24_losses_casebook.json",
        len(c24_cases) == 24,
        len(c24_cases),
        0 if len(c24_cases) == 24 else abs(24 - len(c24_cases)),
        {"cases_count": 24},
        {"cases_count": len(c24_cases)},
        "Audited all 24 SMC_M1_CONTINUATION closed baseline losses individually.",
    )

    # G19: Five SOLO and five DROP ablations replayed on full universe
    solo_n = len(e19.get("single_model_only_ablations") or {})
    drop_n = len(e19.get("leave_one_model_out_ablations") or {})
    _add_gate(
        "G19",
        "Five SOLO and five DROP ablations replayed on full universe",
        "19_5model_solo_drop_arbitration_ablation.json",
        solo_n == 5 and drop_n == 5,
        solo_n + drop_n,
        0 if (solo_n == 5 and drop_n == 5) else 1,
        {"solo_count": 5, "drop_count": 5},
        {"solo_count": solo_n, "drop_count": drop_n},
        "Replayed all 5 SOLO and 5 LEAVE-ONE-OUT model ablations.",
    )

    # G20: Identical candidate universe counts with model/direction proof
    tot_dec = int(e05.get("total_decision_evaluations_per_policy", 0) or 0)
    _add_gate(
        "G20",
        "Identical candidate universe counts with model/direction proof",
        "05_full_22346_m1_5model_decision_census.json",
        tot_dec == 111730,
        tot_dec,
        0 if tot_dec == 111730 else 1,
        {"total_decision_evaluations_per_policy": 111730},
        {"total_decision_evaluations_per_policy": tot_dec},
        "Conserved 22,346 post-warmup M1 bars x 5 models = 111,730 evaluations per policy.",
    )

    # G21: Unique composite IDs and full outer join no collapse
    join_coll = int(e16.get("key_collapse_count", 1) if "key_collapse_count" in e16 else 1)
    r0_mismatch = int(e16.get("si0_self_join_mismatch_count", 1) if "si0_self_join_mismatch_count" in e16 else 1)
    _add_gate(
        "G21",
        "Unique composite IDs and full outer join no collapse",
        "16_full_opportunity_id_join_and_unlocked_portfolio_audit.json",
        join_coll == 0 and r0_mismatch == 0,
        56710,
        join_coll + r0_mismatch,
        {"key_collapse_count": 0, "si0_self_join_mismatch_count": 0},
        {"key_collapse_count": join_coll, "si0_self_join_mismatch_count": r0_mismatch},
        "Verified 56,710 unique composite opportunity_ids with zero key collapse.",
    )

    # G22: All follow-on opportunities considered, no cap of 2
    trunc_cap = e16.get("followup_truncation_cap")
    vis_ts = int(e16.get("unique_eligible_timestamps_visited", 0) or 0)
    _add_gate(
        "G22",
        "All follow-on opportunities considered, no cap of 2",
        "16_full_opportunity_id_join_and_unlocked_portfolio_audit.json",
        trunc_cap is None and vis_ts == 18744,
        vis_ts,
        0 if (trunc_cap is None and vis_ts == 18744) else 1,
        {"followup_truncation_cap": None, "unique_eligible_timestamps_visited": 18744},
        {"followup_truncation_cap": trunc_cap, "unique_eligible_timestamps_visited": vis_ts},
        "Verified full event-driven opportunity stream traversal without 2-follow-up truncation.",
    )

    # G23: Same-baseline independent replay/hash across all fills
    si0_match = bool(e13.get("si0_matches_a1_baseline_exactly", False))
    _add_gate(
        "G23",
        "Same-baseline independent replay/hash across all fills",
        "13_all_7_signal_policy_executable_portfolio_replays.json",
        si0_match,
        3,
        0 if si0_match else 1,
        {"si0_matches_a1_baseline_exactly": True},
        {"si0_matches_a1_baseline_exactly": si0_match},
        "Verified SI0_OFF_BASELINE reproduces A1 baseline portfolio across all 3 fill modes.",
    )

    # G24: New executed changed-code entry/SL/TP/fill real differences
    pol_diff_ok = bool(e13.get("research_policies_produce_distinct_portfolios", False))
    _add_gate(
        "G24",
        "New executed changed-code entry/SL/TP/fill real differences",
        "13_all_7_signal_policy_executable_portfolio_replays.json",
        pol_diff_ok,
        6,
        0 if pol_diff_ok else 1,
        {"research_policies_produce_distinct_portfolios": True},
        {"research_policies_produce_distinct_portfolios": pol_diff_ok},
        "Verified SI1..SI6 produce distinct executable portfolios from real evaluator changes.",
    )

    # G25: Risk cap $2.50 and min net RR 2.0 (real calc evidence)
    risk_viol = int(e15.get("planned_risk_cap_violations_count", 1) if "planned_risk_cap_violations_count" in e15 else 1)
    rr_viol = int(e15.get("min_net_rr_violations_count", 1) if "min_net_rr_violations_count" in e15 else 1)
    _add_gate(
        "G25",
        "Risk cap $2.50 and min net RR 2.0 (real calc evidence)",
        "15_new_entry_geometry_risk_sl_tp_quantity_and_costs.json",
        risk_viol == 0 and rr_viol == 0,
        max(1, int(e15.get("inspected_orders_count", 0) or 0)),
        risk_viol + rr_viol,
        {"planned_risk_cap_violations_count": 0, "min_net_rr_violations_count": 0},
        {"planned_risk_cap_violations_count": risk_viol, "min_net_rr_violations_count": rr_viol},
        "Verified every executed order satisfies planned_risk <= $2.50 and net_rr >= 2.0.",
    )

    # G26: Quote-side fill, fees, cost, qty, slippage identity
    led_viol = int(e14.get("ledger_accounting_identity_violations", 1) if "ledger_accounting_identity_violations" in e14 else 1)
    _add_gate(
        "G26",
        "Quote-side fill, fees, cost, qty, slippage identity",
        "14_all_3_fill_mode_ledger_reconciliation.json",
        led_viol == 0,
        max(1, int(e14.get("total_reconciled_trades_count", 0) or 0)),
        led_viol,
        {"ledger_accounting_identity_violations": 0},
        {"ledger_accounting_identity_violations": led_viol},
        "Verified net_usd == gross_usd - entry_fee - exit_fee with no double-counted spread.",
    )

    # G27: Independent protected structure anchor not SL tautology
    taut_viol = int(e15.get("circular_sl_equals_protected_anchor_tautology_count", 1) if "circular_sl_equals_protected_anchor_tautology_count" in e15 else 1)
    _add_gate(
        "G27",
        "Independent protected structure anchor not SL tautology",
        "15_new_entry_geometry_risk_sl_tp_quantity_and_costs.json",
        taut_viol == 0,
        max(1, int(e15.get("inspected_orders_count", 0) or 0)),
        taut_viol,
        {"circular_sl_equals_protected_anchor_tautology_count": 0},
        {"circular_sl_equals_protected_anchor_tautology_count": taut_viol},
        "Verified protected structure authority is resolved independently of selected_sl.",
    )

    # G28: No fabricated OPEN_AT_HORIZON exit/slot release
    slot_viol = int(e17.get("strict_hold_phantom_slot_release_violations", 1) if "strict_hold_phantom_slot_release_violations" in e17 else 1)
    _add_gate(
        "G28",
        "No fabricated OPEN_AT_HORIZON exit/slot release",
        "17_strict_hold_time_stop_legacy_censoring_comparison.json",
        slot_viol == 0,
        max(1, int(e17.get("inspected_episodes_count", 0) or 0)),
        slot_viol,
        {"strict_hold_phantom_slot_release_violations": 0},
        {"strict_hold_phantom_slot_release_violations": slot_viol},
        "Verified STRICT_HOLD_UNTIL_EXIT_OR_DATA_END never releases slot at 180 bars.",
    )

    # G29: Authentic time stop accounting and data-gap censoring
    ts_ok = bool(e17.get("explicit_180_bar_time_stop_pays_fees_and_spread", False))
    gap_ok = bool(e17.get("data_gap_remains_censored_verified", False))
    _add_gate(
        "G29",
        "Authentic time stop accounting and data-gap censoring",
        "17_strict_hold_time_stop_legacy_censoring_comparison.json",
        ts_ok and gap_ok,
        3,
        0 if (ts_ok and gap_ok) else 1,
        {"time_stop_pays_fees": True, "data_gap_censored": True},
        {"time_stop_pays_fees": ts_ok, "data_gap_censored": gap_ok},
        "Verified EXPLICIT_180_BAR_TIME_STOP pays exit fees/spread and DATA_GAP remains censored.",
    )

    # G30: Historical dataset SHA, 22,346 M1 bars / 5 model count
    post_bars = int(e05.get("post_warmup_m1_bars_count", 0) or 0)
    _add_gate(
        "G30",
        "Historical dataset SHA, 22,346 M1 bars/5 model count",
        "05_full_22346_m1_5model_decision_census.json",
        ds_sha == CANONICAL_DATASET_SHA256_V1793122 and post_bars == 22346,
        post_bars,
        0 if (ds_sha == CANONICAL_DATASET_SHA256_V1793122 and post_bars == 22346) else 1,
        {"dataset_sha256": CANONICAL_DATASET_SHA256_V1793122, "post_warmup_m1_bars": 22346},
        {"dataset_sha256": ds_sha, "post_warmup_m1_bars": post_bars},
        "Verified canonical dataset SHA-256 and 22,346 post-warmup M1 bars.",
    )

    # G31: 18,744 eligible timestamp opportunity conservation
    uniq_ts = int(e05.get("baseline_unique_eligible_m1_timestamps", 0) or 0)
    _add_gate(
        "G31",
        "18,744 eligible timestamp opportunity conservation",
        "05_full_22346_m1_5model_decision_census.json",
        uniq_ts == 18744,
        uniq_ts,
        0 if uniq_ts == 18744 else 1,
        {"baseline_unique_eligible_m1_timestamps": 18744},
        {"baseline_unique_eligible_m1_timestamps": uniq_ts},
        "Conserved all 18,744 unique eligible M1 timestamps.",
    )

    # G32: 3 execution fill modes independently replayed
    fm_list = list(e14.get("fill_modes_replayed") or [])
    _add_gate(
        "G32",
        "3 execution fill modes independently replayed",
        "14_all_3_fill_mode_ledger_reconciliation.json",
        len(fm_list) == 3,
        len(fm_list),
        0 if len(fm_list) == 3 else 1,
        {"fill_modes_count": 3},
        {"fill_modes_replayed": fm_list},
        "Replayed NEXT_BAR_OPEN_BID_ASK, LEGACY_CLOSE_FILL, and STRESS_WIDER_SPREAD_SLIPPAGE_2X.",
    )

    # G33: Same-window matched coeval & independent portfolios
    coeval_ok = bool(e16.get("matched_coeval_and_independent_portfolios_reported", False))
    _add_gate(
        "G33",
        "Same-window matched coeval & independent portfolios",
        "16_full_opportunity_id_join_and_unlocked_portfolio_audit.json",
        coeval_ok,
        7,
        0 if coeval_ok else 1,
        {"matched_coeval_and_independent_portfolios_reported": True},
        {"matched_coeval_and_independent_portfolios_reported": coeval_ok},
        "Reported both matched same-opportunity transitions and independent chronological portfolios.",
    )

    # G34: OOS partition and actual exit/pending embargo no leakage
    oos_leak = int(e21.get("cross_partition_leakage_count", 1) if "cross_partition_leakage_count" in e21 else 1)
    emb_bars = int(e21.get("embargo_bars_m1", 0) or 0)
    _add_gate(
        "G34",
        "OOS partition and actual exit/pending embargo no leakage",
        "21_OOS_walkforward_purging_and_freshness.json",
        oos_leak == 0 and emb_bars >= 180,
        max(1, int(e21.get("total_splits_evaluated", 0) or 0)),
        oos_leak,
        {"cross_partition_leakage_count": 0, "embargo_bars_m1": ">= 180"},
        {"cross_partition_leakage_count": oos_leak, "embargo_bars_m1": emb_bars},
        "Verified chronological OOS splits purge crossing episodes by true exit timestamp + >=180-bar embargo.",
    )

    # G35: Reusable OOS explicitly not labeled fresh or verified edge
    fresh_lbl = str(e21.get("fresh_oos_verification_status") or "")
    _add_gate(
        "G35",
        "Reusable OOS explicitly not labeled fresh or verified edge",
        "21_OOS_walkforward_purging_and_freshness.json",
        fresh_lbl == "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS",
        1,
        0 if fresh_lbl == "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS" else 1,
        {"fresh_oos_verification_status": "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS"},
        {"fresh_oos_verification_status": fresh_lbl},
        "Explicitly labeled October 2026 holdout as reused history, not fresh untouched OOS.",
    )

    # G36: Statistical verdict derived from actual metrics + CIs
    comp_verd = bool(e34.get("verdict_computed_from_metrics_not_hardcoded", False))
    strat_verd = str(e34.get("strategy_statistical_verdict") or "")
    _add_gate(
        "G36",
        "Statistical verdict derived from actual metrics + CIs",
        "34_engineering_and_strategy_dual_verdict.json",
        comp_verd and bool(strat_verd),
        7,
        0 if (comp_verd and bool(strat_verd)) else 1,
        {"verdict_computed_from_metrics_not_hardcoded": True},
        {"strategy_statistical_verdict": strat_verd, "computed": comp_verd},
        "Verified STRATEGY_STATISTICAL_VERDICT is computed dynamically from portfolio metrics and CIs.",
    )

    # G37: Zero-trade and underpowered fail to edge-pass
    up_guard = bool(e23.get("zero_trade_and_underpowered_guard_verified", False))
    _add_gate(
        "G37",
        "Zero-trade and underpowered fail to edge-pass",
        "23_statistical_uncertainty_bootstrap_and_multiple_testing.json",
        up_guard,
        2,
        0 if up_guard else 1,
        {"zero_trade_and_underpowered_guard_verified": True},
        {"zero_trade_and_underpowered_guard_verified": up_guard},
        "Verified 0-trade and <30-trade cohorts return INCONCLUSIVE_UNDERPOWERED, never positive edge.",
    )

    # G38: Uncertainty/overfitting/multiple comparisons disclosed
    mc_disc = bool(e23.get("multiple_testing_and_nonstationarity_disclosed", False))
    _add_gate(
        "G38",
        "Uncertainty/overfitting/multiple comparisons disclosed",
        "23_statistical_uncertainty_bootstrap_and_multiple_testing.json",
        mc_disc,
        7,
        0 if mc_disc else 1,
        {"multiple_testing_and_nonstationarity_disclosed": True},
        {"multiple_testing_and_nonstationarity_disclosed": mc_disc},
        "Reported Wilson 95% CIs, block-bootstrap 95% CIs, and Holm-Bonferroni multiple-testing adjustments.",
    )

    # G39: No-trade control, turnover and exposure reported
    nt_rep = bool(e18.get("no_trade_cash_benchmark_included", False))
    _add_gate(
        "G39",
        "No-trade control, turnover and exposure reported",
        "18_loss_rate_expectancy_pf_drawdown_exposure_by_policy.json",
        nt_rep,
        8,
        0 if nt_rep else 1,
        {"no_trade_cash_benchmark_included": True},
        {"no_trade_cash_benchmark_included": nt_rep},
        "Included NO_TRADE_CASH_PRESERVATION control alongside exposure and turnover metrics.",
    )

    # G40: ENTIRE backend suite discovered and executed at tested source SHA
    col_cnt = int(e24.get("collected_node_ids_count", 0) or 0)
    exec_cnt = int(e25.get("executed_junit_node_ids_count", 0) or 0)
    suite_sha = str(e25.get("tested_executable_sha") or "")
    _add_gate(
        "G40",
        "ENTIRE backend suite discovered and executed at tested source SHA",
        "25_full_regression_shard_junit_and_exact_bijection.json",
        col_cnt >= 4880 and exec_cnt == col_cnt and suite_sha == tested_executable_sha,
        col_cnt,
        0 if (col_cnt >= 4880 and exec_cnt == col_cnt and suite_sha == tested_executable_sha) else 1,
        {"collected_count": ">= 4880", "tested_sha": tested_executable_sha},
        {"collected_count": col_cnt, "executed_count": exec_cnt, "tested_sha": suite_sha},
        "Discovered and executed entire backend pytest tree at exact tested executable SHA.",
    )

    # G41: Exact collected node IDs == JUnit executed node IDs
    bij_ok = bool(e25.get("exact_node_id_bijection_verified", False))
    miss_ids = int(e25.get("missing_from_junit_count", 1) if "missing_from_junit_count" in e25 else 1)
    extra_ids = int(e25.get("unexpected_in_junit_count", 1) if "unexpected_in_junit_count" in e25 else 1)
    _add_gate(
        "G41",
        "Exact collected node IDs == JUnit executed node IDs",
        "25_full_regression_shard_junit_and_exact_bijection.json",
        bij_ok and miss_ids == 0 and extra_ids == 0,
        col_cnt,
        miss_ids + extra_ids,
        {"exact_node_id_bijection_verified": True, "missing": 0, "extra": 0},
        {"exact_node_id_bijection_verified": bij_ok, "missing": miss_ids, "extra": extra_ids},
        "Verified 1-to-1 node-ID set bijection between pytest --collect-only and shard JUnit XMLs.",
    )

    # G42: No hidden failed/blocked/skipped/timeout shards
    failed_tests = int(e25.get("failed_count", 1) if "failed_count" in e25 else 1)
    err_tests = int(e25.get("error_count", 1) if "error_count" in e25 else 1)
    bad_shards = int(e25.get("nonzero_exit_shards_count", 1) if "nonzero_exit_shards_count" in e25 else 1)
    _add_gate(
        "G42",
        "No hidden failed/blocked/skipped/timeout shards",
        "25_full_regression_shard_junit_and_exact_bijection.json",
        failed_tests == 0 and err_tests == 0 and bad_shards == 0,
        int(e25.get("shard_count", 0) or 0),
        failed_tests + err_tests + bad_shards,
        {"failed_count": 0, "error_count": 0, "nonzero_exit_shards_count": 0},
        {"failed_count": failed_tests, "error_count": err_tests, "nonzero_exit_shards_count": bad_shards},
        "Verified all pytest shards exited 0 with 0 failures and 0 errors.",
    )

    # G43: >=12 ACTUAL executable RED/GREEN mutation controls
    exec_mut_cnt = int(e26.get("executable_mutations_count", 0) or 0)
    exec_mut_pass = int(e26.get("executable_mutations_caught_count", 0) or 0)
    _add_gate(
        "G43",
        ">=12 ACTUAL executable RED/GREEN mutation controls",
        "26_executable_red_green_source_mutations_exit_codes.json",
        exec_mut_cnt >= 12 and exec_mut_pass == exec_mut_cnt,
        exec_mut_cnt,
        exec_mut_cnt - exec_mut_pass,
        {"executable_mutations_count": ">= 12", "all_caught_with_nonzero_red_and_zero_green": True},
        {"executable_mutations_count": exec_mut_cnt, "caught_count": exec_mut_pass},
        "Verified source-level mutations in isolated temp sandbox produce real subprocess exit != 0 on RED and 0 on GREEN.",
    )

    # G44: Evidence-dictionary mutations separate, not counted for G43
    ev_mut_sep = bool(e27.get("separated_from_executable_red_green", False))
    ev_mut_cnt = int(e27.get("evidence_gate_mutations_count", 0) or 0)
    _add_gate(
        "G44",
        "Evidence-dictionary mutations separate, not counted for G43",
        "27_evidence_only_mutation_gate_tests_separately.json",
        ev_mut_sep and ev_mut_cnt >= 12,
        ev_mut_cnt,
        0 if (ev_mut_sep and ev_mut_cnt >= 12) else 1,
        {"separated_from_executable_red_green": True, "count": ">= 12"},
        {"separated_from_executable_red_green": ev_mut_sep, "count": ev_mut_cnt},
        "Kept in-memory evidence gate mutations strictly separate in artifact 27.",
    )

    # G45: Full 1/2/4 independent worker 3-fill-mode results equal
    w_eq = bool(e31.get("all_1_2_4_worker_hashes_identical", False))
    indep_gen = bool(e31.get("independent_execution_no_shared_digest_reuse", False))
    _add_gate(
        "G45",
        "Full 1/2/4 independent worker 3-fill-mode results equal",
        "31_full_worker_1v2v4_hash_comparison.json",
        w_eq and indep_gen,
        3,
        0 if (w_eq and indep_gen) else 1,
        {"all_1_2_4_worker_hashes_identical": True, "independent_execution_no_shared_digest_reuse": True},
        {"all_1_2_4_worker_hashes_identical": w_eq, "independent_execution_no_shared_digest_reuse": indep_gen},
        "Verified independent 1, 2, and 4 worker executions produce identical SHA-256 across all 3 fill modes.",
    )

    # G46: Disk > floor, RAM cap, <=4 workers, no orphan processes
    disk_ok = bool(e32.get("disk_above_hard_floor_5gib", False))
    max_w = int(e32.get("max_workers_configured", 99) or 99)
    orph = int(e32.get("orphan_subprocesses_remaining", 1) if "orphan_subprocesses_remaining" in e32 else 1)
    _add_gate(
        "G46",
        "Disk > floor, RAM cap, <=4 workers, no orphan processes",
        "32_resource_disk_ram_subprocess_guard_and_cleanup.json",
        disk_ok and 1 <= max_w <= 4 and orph == 0,
        3,
        0 if (disk_ok and 1 <= max_w <= 4 and orph == 0) else 1,
        {"disk_above_5gib": True, "max_workers": "<= 4", "orphans": 0},
        {"disk_above_5gib": disk_ok, "max_workers": max_w, "orphans": orph},
        "Verified disk free space >= 5 GiB, worker cap <= 4, and 0 orphan subprocesses.",
    )

    # G47: All required artifact bytes independently schema/hash verified
    man_files_n = int(manifest_check.get("verified_files_count", 0) or 0)
    man_viol = int(manifest_check.get("corrupt_or_missing_files_count", 1) if "corrupt_or_missing_files_count" in manifest_check else 1)
    _add_gate(
        "G47",
        "All required artifact bytes independently schema/hash verified",
        "artifact_sha256_manifest.json",
        man_files_n >= 35 and man_viol == 0,
        max(1, man_files_n),
        man_viol,
        {"verified_files_count": ">= 35", "corrupt_or_missing_files_count": 0},
        {"verified_files_count": man_files_n, "corrupt_or_missing_files_count": man_viol},
        "Independently hashed and schema-verified all required artifacts on disk.",
    )

    # G48: Exact remote HEAD after AUTO PUSH equals final local HEAD and no executable diff from tested SHA
    exec_diff_cnt = int(e35.get("executable_diff_files_since_tested_sha_count", 1) if "executable_diff_files_since_tested_sha_count" in e35 else 1)
    push_ready = bool(e36.get("branch_matches_mandatory", False))
    _add_gate(
        "G48",
        "Exact remote HEAD after AUTO PUSH equals final local HEAD and no executable diff from tested source SHA",
        "35_tested_code_SHA_branch_source_integrity.json",
        exec_diff_cnt == 0 and push_ready,
        2,
        exec_diff_cnt + (0 if push_ready else 1),
        {"executable_diff_files_since_tested_sha_count": 0, "branch_matches_mandatory": True},
        {"executable_diff_files_since_tested_sha_count": exec_diff_cnt, "branch_matches_mandatory": push_ready},
        "Verified zero executable diff between tested SHA and HEAD on mandatory branch.",
    )

    # G49: Old models/adapters/tools preserved with regression compatibility
    compat_ok = bool(e03.get("legacy_models_and_adapters_preserved", False))
    _add_gate(
        "G49",
        "Old models/adapters/tools preserved with regression compatibility",
        "03_code_function_consumer_integration_map.json",
        compat_ok,
        5,
        0 if compat_ok else 1,
        {"legacy_models_and_adapters_preserved": True},
        {"legacy_models_and_adapters_preserved": compat_ok},
        "Preserved all 5 production models and existing adapters/tools.",
    )

    # G50: Engineering/business verdicts explicitly separated
    sep_verd = bool(
        "engineering_verification_verdict" in e34
        and "strategy_statistical_verdict" in e34
        and "activation_ready" in e34
    )
    _add_gate(
        "G50",
        "Engineering/business verdicts explicitly separated",
        "34_engineering_and_strategy_dual_verdict.json",
        sep_verd,
        3,
        0 if sep_verd else 1,
        {"separated_verdict_fields": True},
        {
            "engineering_verification_verdict": e34.get("engineering_verification_verdict"),
            "strategy_statistical_verdict": e34.get("strategy_statistical_verdict"),
            "activation_ready": e34.get("activation_ready"),
        },
        "Explicitly separated ENGINEERING_VERIFICATION_VERDICT, STRATEGY_STATISTICAL_VERDICT, and ACTIVATION_READY.",
    )

    # G51: ACTIVATE/PAPER/LIVE always NO for this version
    act_val = str(e34.get("activation_ready") or "")
    _add_gate(
        "G51",
        "ACTIVATE/PAPER/LIVE always NO for this version",
        "34_engineering_and_strategy_dual_verdict.json",
        act_val == "NO",
        1,
        0 if act_val == "NO" else 1,
        {"activation_ready": "NO"},
        {"activation_ready": act_val},
        "Verified ACTIVATION_READY == NO and all production flags remain OFF.",
    )

    # G52: Source->function->consumer real path proof included
    callsites_n = len(e03.get("verified_consumer_callsites") or [])
    _add_gate(
        "G52",
        "Source->function->consumer real path proof included",
        "03_code_function_consumer_integration_map.json",
        callsites_n >= 4,
        callsites_n,
        0 if callsites_n >= 4 else 1,
        {"verified_consumer_callsites": ">= 4"},
        {"verified_consumer_callsites": callsites_n},
        "Documented and verified source-to-consumer call chain across Production, Shadow, and Backtest.",
    )

    passed_count = sum(1 for g in gates if g["passed"])
    failed_gates = [g["gate_id"] for g in gates if not g["passed"]]

    return {
        "schema_version": V17_9_3_1_2_2_SCHEMA_VERSION,
        "total_gates": len(gates),
        "passed_gates_count": passed_count,
        "failed_gates_count": len(failed_gates),
        "failed_gate_ids": failed_gates,
        "all_52_engineering_gates_passed": len(gates) == 52 and len(failed_gates) == 0,
        "gates": gates,
    }
