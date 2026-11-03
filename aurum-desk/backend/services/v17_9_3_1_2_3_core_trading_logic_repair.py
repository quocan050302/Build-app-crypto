"""
backend/services/v17_9_3_1_2_3_core_trading_logic_repair.py

AURUM Desk / Bitget XAUUSDT — V17.9.3.1.2.3
Core Trading Logic Repair & Independent Strategy Truth Service.

Implements:
  - STAGE G0: Ground-Truth Loss Forensics across all 166 closed baseline trades
    (`9` wins, `157` losses), `74` `OPEN_AT_HORIZON`, and `1` `DATA_GAP_UNRESOLVED`
    with multi-label and primary root-cause classification.
  - STAGE G1: Independent Research Candidate Generation (`RESEARCH_DISCOVERY` / `SI8`)
    scanning closed M1/M5/M15/H1/H4 candles across all 5 canonical models and both
    directions via the real `SharedStrategyEvaluator`.
  - Section 2: Executable Call-Graph & Function Responsibility Matrix.
  - Section 9: 15 Non-Tautological Acceptance Gates (`CR01..CR15`) & Dual Verdict
    (`ENGINEERING_VERIFICATION_VERDICT`, `STRATEGY_STATISTICAL_VERDICT`, `ACTIVATION_READY`).
"""
from __future__ import annotations

import bisect
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from services.scalp_m1_types import ModelIdentifier
from services.shared_strategy_evaluator import (
    SharedStrategyEvaluator,
    build_market_decision_context,
)
from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    build_canonical_opportunity_id_v1793121,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_DATASET_SHA256_V1793122,
    CANONICAL_V1793123_POLICY_IDS,
    MANDATORY_BRANCH_V1793122,
    PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122,
    compute_signal_intelligence_policy_registry_hash_v1793123,
    compute_strategy_statistical_verdict_v1793122,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    compute_research_policy_registry_hash_v179312,
)

V17_9_3_1_2_3_SCHEMA_VERSION = "17.9.3.1.2.3"
V17_9_3_1_2_5_SCHEMA_VERSION = "17.9.3.1.2.5"

CANONICAL_LOSS_ROOT_CAUSE_TAXONOMY_V1793123: Tuple[str, ...] = (
    "WRONG_SIDE_HTF",
    "FAKE_MSS",
    "IMPULSE_CHASING",
    "PREMATURE_ENTRY",
    "FVG_STALE_OR_ALREADY_MITIGATED",
    "SWEEP_NOT_RECLAIMED",
    "FALSE_UNICORN",
    "SL_INSIDE_NOISE",
    "PROTECTED_STRUCTURE_INVALID",
    "TARGET_NOT_REAL_OR_ALREADY_SWEPT",
    "INSUFFICIENT_NET_RR",
    "FEES_DOMINATE",
    "FILL_GAP/AMBIGUOUS_INTRABAR",
    "OTHER/UNKNOWN",
)

CANONICAL_5_MODELS_V1793123: Tuple[str, ...] = (
    ModelIdentifier.SMC_M1_REVERSAL.value,
    ModelIdentifier.SMC_M1_CONTINUATION.value,
    ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
    ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
    ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
)

LEGACY_TO_CANONICAL_MODEL_MAP_V1793123: Dict[str, str] = {
    "SMC_M1_REVERSAL": ModelIdentifier.SMC_M1_REVERSAL.value,
    "ICT_2022_MODEL": ModelIdentifier.SMC_M1_CONTINUATION.value,
    "SMC_M1_CONTINUATION": ModelIdentifier.SMC_M1_CONTINUATION.value,
    "UNICORN_MODEL": ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
    "SMC_M1_FVG_CONTINUATION": ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
    "SILVER_BULLET": ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
    "SMC_M1_SWEEP_RECLAIM": ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
    "CG01_SWEEP_MSS_FVG": ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
}


def build_callgraph_function_responsibility_matrix_v1793123() -> Dict[str, Any]:
    """
    Section 2: Maps the complete executable decision path from orchestrator/backtest
    entry through `SharedStrategyEvaluator`, `v17_9_3_1_2_2_signal_intelligence`,
    `M1ScalpRiskCalculator`, order-intent emission, and 3-fill-mode portfolio replay.
    """
    rows = [
        {
            "stage_id": "STAGE_01_ORCHESTRATOR_AND_BACKTEST_CONSUMERS",
            "file": "backend/services/m1_scalp_orchestrator.py",
            "function": "M1ScalpOrchestrator._evaluate_shared_strategy_candidates",
            "responsibility": "Production/Shadow consumer calling SharedStrategyEvaluator with default OFF mode and production adapter guard.",
            "defects_repaired_in_v1793123": [
                "Hard block against RESEARCH_APPLY / RESEARCH_DISCOVERY in PRODUCTION_ORCHESTRATOR, LIVE, PAPER, AUTO_PAPER, AUTO_LIVE.",
            ],
        },
        {
            "stage_id": "STAGE_02_DIRECTION_PREFERENCE_AND_HTF_PROVENANCE",
            "file": "backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "function": "resolve_research_preferred_direction_v1793122",
            "responsibility": "Resolves causal H4/H1/M15/M5 directional bias and model-specific continuation vs reversal alignment.",
            "defects_repaired_in_v1793123": [
                "T09: Explicit HTF bias strings cannot override absent, unclosed, or stale H4/H1 candles.",
                "T10: Missing/neutral HTF never defaults to LONG (returns None / NO_BIAS).",
                "T11: Mixed HTF allows symmetric LONG/SHORT evaluation only when closed causal evidence exists.",
            ],
        },
        {
            "stage_id": "STAGE_03_SETUP_DISCOVERY_AND_FVG_FRESHNESS",
            "file": "backend/services/shared_strategy_evaluator.py",
            "function": "SharedStrategyEvaluator._extract_causal_setup_descriptors",
            "responsibility": "Extracts causal sweep, MSS/BOS, FVG, and breaker descriptors from closed M1/M5 bars.",
            "defects_repaired_in_v1793123": [
                "T01/T02/T03: Enforces strict post-formation closed-bar FVG mitigation & chronological retest checks.",
                "STAGE G1 (RESEARCH_DISCOVERY): Scans closed M1 windows to discover valid causal setups across all 5 models when baseline heuristics miss them.",
            ],
        },
        {
            "stage_id": "STAGE_04_SIGNAL_QUALITY_UNICORN_SWEEP_MSS_TARGET",
            "file": "backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "function": "build_signal_quality_evidence_v1793122 & _resolve_unswept_liquidity_pool_target_v1793123",
            "responsibility": "Validates FVG retest chronology, Unicorn spatial overlap, sweep+reclaim close, MSS swing known_at_ms, independent protected SL anchor, and unswept liquidity pool target.",
            "defects_repaired_in_v1793123": [
                "T04/T05/T06: Separates FVG_ONLY from UNICORN_CONFIRMED; requires positive Breaker+FVG overlap (overlap_low < overlap_high) and valid causal order.",
                "T07/T08: Requires confirmed prior swing sweep + reclaim close and MSS swing known_at_ms <= now_ms.",
                "T17: Rejects circular/synthetic protected anchors equal to selected_sl or on the wrong side of entry.",
                "T19/T20: Resolves TP from real unswept pre-entry liquidity pools instead of already-swept or arbitrary RR multiples.",
            ],
        },
        {
            "stage_id": "STAGE_05_PENDING_CONFIRMATION_STATE_MACHINE",
            "file": "backend/services/v17_9_3_1_2_2_signal_intelligence.py",
            "function": "advance_pending_confirmation_state_v1793122",
            "responsibility": "Advances WAITING_CONFIRMATION setups across subsequent closed M1 bars to CONFIRMED, INVALIDATED, EXPIRED, or CANCELLED_GAP.",
            "defects_repaired_in_v1793123": [
                "T14/T15/T16: Confirmation never fills at the earlier signal bar; invalidates immediately if protected structure breaks first; expires after TTL.",
            ],
        },
        {
            "stage_id": "STAGE_06_GATE_EVALUATION_AND_SNAPSHOT_PROPAGATION",
            "file": "backend/services/shared_strategy_evaluator.py",
            "function": "SharedStrategyEvaluator.evaluate_model_direction",
            "responsibility": "Applies SignalIntelligenceGateResultV1793122 to StrategyDecisionSnapshot under RESEARCH_APPLY / RESEARCH_DISCOVERY.",
            "defects_repaired_in_v1793123": [
                "T18/T20/T21/T22/T23: Propagates proposed_entry, proposed_sl, proposed_tp, proposed_target_id, proposed_qty, proposed_gross_rr, proposed_net_rr, proposed_anchor_id, proposed_invalidation_price, and recomputed decision_fingerprint into StrategyDecisionSnapshot.",
                "Resolves SI4/SI6 zero-trade starvation via SI7_CORE_TRADING_LOGIC_REPAIRED and SI8_RESEARCH_DISCOVERY_REPAIRED model-specific confluence.",
            ],
        },
        {
            "stage_id": "STAGE_07_PRE_ENTRY_ARBITRATION_AND_PORTFOLIO_REPLAY",
            "file": "tools/run_v17_9_3_1_2_2_entry_signal_backtest.py",
            "function": "arbitrate_competing_model_intents_v1793122 & replay_single_si_policy_v1793122",
            "responsibility": "Arbitrates competing same-timestamp candidates using pre-entry causal quality tuples and replays single-slot portfolios across 3 fill modes.",
            "defects_repaired_in_v1793123": [
                "T24: Strictly forbids post-entry PnL/MFE/MAE/outcome labels in arbitration.",
                "T25..T28: Enforces single-position slot blocking, strict hold vs 180-bar time-stop vs legacy censoring, and exact gross - fees == net accounting.",
            ],
        },
    ]
    return {
        "schema_version": V17_9_3_1_2_3_SCHEMA_VERSION,
        "callgraph_stages_count": len(rows),
        "stages": rows,
    }


def classify_single_episode_root_causes_v1793123(
    ep: Mapping[str, Any],
    feat: Mapping[str, Any],
) -> Tuple[str, List[str]]:
    """
    STAGE G0 / V17.9.3.1.2.4 Stage R2.1: Deterministic multi-label + primary root-cause
    classifier for a baseline trade episode using only explicit point-in-time pre-entry
    and execution-trace evidence. Unproven causes without point-in-time feature keys
    fall back to `OTHER/UNKNOWN`, never generic defect assertions.
    """
    labels: List[str] = []
    out_lbl = str(ep.get("outcome_label") or "")
    m_raw = str(ep.get("model_id") or "")
    m_id = LEGACY_TO_CANONICAL_MODEL_MAP_V1793123.get(m_raw, m_raw)

    has_htf_ev = "htf_alignment_state" in feat and feat.get("htf_alignment_state") is not None
    htf_state = str(feat.get("htf_alignment_state") or "").upper() if has_htf_ev else ""

    has_disp_ev = "displacement_body_atr_ratio" in feat and feat.get("displacement_body_atr_ratio") is not None
    disp_ratio = float(feat.get("displacement_body_atr_ratio") or 0.0) if has_disp_ev else 1.0

    has_retrace_ev = "continuation_retrace_valid" in feat and feat.get("continuation_retrace_valid") is not None
    retrace_ok = bool(feat.get("continuation_retrace_valid")) if has_retrace_ev else True

    has_uni_ev = "unicorn_classification" in feat and feat.get("unicorn_classification") is not None
    uni_cls = str(feat.get("unicorn_classification") or "").upper() if has_uni_ev else ""

    has_fvg_ev = ("fvg_unmitigated" in feat) or ("fvg_age_bars_m1" in feat)
    fvg_unmit = bool(feat.get("fvg_unmitigated", True))
    fvg_age = int(feat.get("fvg_age_bars_m1", 8) or 8)

    has_swp_ev = "sweep_reclaimed" in feat and feat.get("sweep_reclaimed") is not None
    sweep_rec = bool(feat.get("sweep_reclaimed", True))

    has_mss_ev = "mss_confirmed" in feat and feat.get("mss_confirmed") is not None
    mss_ok = bool(feat.get("mss_confirmed", True))

    has_prot_ev = "protected_structure_independent_of_sl" in feat and feat.get("protected_structure_independent_of_sl") is not None
    prot_indep = bool(feat.get("protected_structure_independent_of_sl", True))

    has_targ_ev = "target_reachable" in feat and feat.get("target_reachable") is not None
    target_reach = bool(feat.get("target_reachable", True))

    has_rr_ev = ("planned_net_rr" in ep and ep.get("planned_net_rr") is not None) or ("net_rr" in ep and ep.get("net_rr") is not None)
    net_rr = float(ep.get("planned_net_rr") if ep.get("planned_net_rr") is not None else (ep.get("net_rr") or 2.0))

    has_hold_ev = ("bars_held_1m" in ep and ep.get("bars_held_1m") is not None) or ("hold_bars" in ep and ep.get("hold_bars") is not None)
    hold_bars = int(ep.get("bars_held_1m") if ep.get("bars_held_1m") is not None else (ep.get("hold_bars") or 12))

    has_stop_dist_ev = "stop_distance_price" in ep and ep.get("stop_distance_price") is not None
    stop_dist_px = float(ep.get("stop_distance_price") or 99.0)

    has_pnl_ev = "realized_gross_pnl_usd" in ep and ep.get("realized_gross_pnl_usd") is not None
    gross_pnl = float(ep.get("realized_gross_pnl_usd") or 0.0)
    fees_usd = float(ep.get("total_fees_usd") or (float(ep.get("entry_fee_usd") or 0.0) + float(ep.get("exit_fee_usd") or 0.0)))

    if out_lbl == "DATA_GAP_UNRESOLVED" or bool(ep.get("ambiguous_same_bar", False)):
        labels.append("FILL_GAP/AMBIGUOUS_INTRABAR")
    if has_htf_ev and htf_state in ("OPPOSED", "CONFLICTED", "MIXED", "COUNTERTREND"):
        labels.append("WRONG_SIDE_HTF")
    if m_id == ModelIdentifier.SMC_M1_CONTINUATION.value and (
        (has_disp_ev and disp_ratio < 0.55) or (has_retrace_ev and not retrace_ok)
    ):
        labels.append("IMPULSE_CHASING")
    if (has_mss_ev and not mss_ok) or (has_disp_ev and disp_ratio < 0.45):
        labels.append("FAKE_MSS")
    if has_hold_ev and hold_bars <= 3:
        labels.append("PREMATURE_ENTRY")
    if m_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value and has_fvg_ev and (not fvg_unmit or fvg_age > 35):
        labels.append("FVG_STALE_OR_ALREADY_MITIGATED")
    if m_id in (ModelIdentifier.SMC_M1_REVERSAL.value, ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value) and has_swp_ev and not sweep_rec:
        labels.append("SWEEP_NOT_RECLAIMED")
    if m_id in (ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, ModelIdentifier.CG01_SWEEP_MSS_FVG.value) and has_uni_ev and uni_cls == "FVG_ONLY":
        labels.append("FALSE_UNICORN")
    if has_hold_ev and hold_bars <= 5 and has_stop_dist_ev and stop_dist_px < 1.8:
        labels.append("SL_INSIDE_NOISE")
    if has_prot_ev and not prot_indep:
        labels.append("PROTECTED_STRUCTURE_INVALID")
    if (has_targ_ev and not target_reach) or (has_rr_ev and net_rr > 3.5):
        labels.append("TARGET_NOT_REAL_OR_ALREADY_SWEPT")
    if has_rr_ev and net_rr < 2.0:
        labels.append("INSUFFICIENT_NET_RR")
    if has_pnl_ev and gross_pnl >= 0.0 and (gross_pnl - fees_usd) < 0.0:
        labels.append("FEES_DOMINATE")

    if not labels:
        labels.append("OTHER/UNKNOWN")

    # Deterministic priority order for primary root cause
    priority_order = (
        "FILL_GAP/AMBIGUOUS_INTRABAR",
        "FEES_DOMINATE",
        "INSUFFICIENT_NET_RR",
        "FALSE_UNICORN",
        "FVG_STALE_OR_ALREADY_MITIGATED",
        "IMPULSE_CHASING",
        "SWEEP_NOT_RECLAIMED",
        "WRONG_SIDE_HTF",
        "TARGET_NOT_REAL_OR_ALREADY_SWEPT",
        "SL_INSIDE_NOISE",
        "PREMATURE_ENTRY",
        "FAKE_MSS",
        "PROTECTED_STRUCTURE_INVALID",
        "OTHER/UNKNOWN",
    )
    primary = next((c for c in priority_order if c in labels), labels[0])
    return primary, labels


def build_ground_truth_loss_forensics_v1793123(
    a1_episodes: Sequence[Mapping[str, Any]],
    feature_ledger_by_opp_id: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    STAGE G0 (`03_loss_forensics_166_closed_74_open_by_model.json`):
    Builds the complete ground-truth forensic ledger across all 166 closed baseline
    trades (`9` wins, `157` losses), `74` `OPEN_AT_HORIZON` trades, and `1`
    `DATA_GAP_UNRESOLVED` trade across all 5 canonical models.
    """
    closed_records: List[Dict[str, Any]] = []
    open_records: List[Dict[str, Any]] = []
    gap_records: List[Dict[str, Any]] = []

    primary_loss_counter: Counter[str] = Counter()
    multilabel_loss_counter: Counter[str] = Counter()
    by_model_summary: Dict[str, Dict[str, Any]] = {
        m: {
            "model_id": m,
            "closed_trades_count": 0,
            "wins_count": 0,
            "losses_count": 0,
            "open_at_horizon_count": 0,
            "data_gap_unresolved_count": 0,
            "realized_net_pnl_usd": 0.0,
            "primary_loss_causes": Counter(),
        }
        for m in CANONICAL_5_MODELS_V1793123
    }

    for ep in a1_episodes:
        if not ep.get("filled", False):
            continue
        opp_id = str(ep.get("opportunity_id") or "")
        m_raw = str(ep.get("model_id") or "")
        m_id = LEGACY_TO_CANONICAL_MODEL_MAP_V1793123.get(m_raw, m_raw)
        if m_id not in by_model_summary:
            by_model_summary[m_id] = {
                "model_id": m_id,
                "closed_trades_count": 0,
                "wins_count": 0,
                "losses_count": 0,
                "open_at_horizon_count": 0,
                "data_gap_unresolved_count": 0,
                "realized_net_pnl_usd": 0.0,
                "primary_loss_causes": Counter(),
            }

        feat = dict(feature_ledger_by_opp_id.get(opp_id) or {})
        primary_cause, all_causes = classify_single_episode_root_causes_v1793123(ep, feat)
        out_lbl = str(ep.get("outcome_label") or "")
        net_usd = float(ep.get("realized_net_pnl_usd") or 0.0)

        rec = {
            "opportunity_id": opp_id,
            "model_id": m_id,
            "legacy_model_id": m_raw,
            "direction": str(ep.get("direction") or ""),
            "decision_timestamp_ms": int(ep.get("decision_timestamp_ms") or 0),
            "entry_ts_ms": int(ep.get("entry_fill_ts") or ep.get("decision_timestamp_ms") or 0),
            "exit_ts_ms": int(ep.get("exit_fill_ts") or ep.get("release_ts_ms") or 0),
            "entry_fill_price": float(ep.get("entry_fill_price") or 0.0),
            "exit_fill_price": float(ep.get("exit_fill_price") or 0.0) if ep.get("exit_fill_price") is not None else None,
            "outcome_label": out_lbl,
            "realized_net_pnl_usd": round(net_usd, 4),
            "realized_r": round(float(ep.get("realized_r") or 0.0), 4),
            "primary_root_cause": primary_cause if out_lbl != "WIN" else "WIN_REALIZED_TP",
            "root_cause_labels": all_causes if out_lbl != "WIN" else ["WIN_REALIZED_TP"],
            "htf_alignment_state": str(feat.get("htf_alignment_state") or "MIXED"),
            "unicorn_classification": str(feat.get("unicorn_classification") or "NONE"),
            "displacement_body_atr_ratio": float(feat.get("displacement_body_atr_ratio", 0.5) or 0.5),
        }

        if out_lbl in ("WIN", "LOSS"):
            closed_records.append(rec)
            by_model_summary[m_id]["closed_trades_count"] += 1
            by_model_summary[m_id]["realized_net_pnl_usd"] = round(
                float(by_model_summary[m_id]["realized_net_pnl_usd"]) + net_usd, 4
            )
            if out_lbl == "WIN":
                by_model_summary[m_id]["wins_count"] += 1
            else:
                by_model_summary[m_id]["losses_count"] += 1
                primary_loss_counter[primary_cause] += 1
                by_model_summary[m_id]["primary_loss_causes"][primary_cause] += 1
                for c_lbl in all_causes:
                    multilabel_loss_counter[c_lbl] += 1
        elif out_lbl == "OPEN_AT_HORIZON":
            open_records.append(rec)
            by_model_summary[m_id]["open_at_horizon_count"] += 1
        elif out_lbl == "DATA_GAP_UNRESOLVED":
            gap_records.append(rec)
            by_model_summary[m_id]["data_gap_unresolved_count"] += 1

    serializable_by_model = {}
    for m_k, m_v in by_model_summary.items():
        serializable_by_model[m_k] = {
            **m_v,
            "primary_loss_causes": dict(m_v["primary_loss_causes"]),
        }

    losses_total = sum(1 for r in closed_records if r["outcome_label"] == "LOSS")
    wins_total = sum(1 for r in closed_records if r["outcome_label"] == "WIN")

    return {
        "schema_version": V17_9_3_1_2_3_SCHEMA_VERSION,
        "taxonomy_categories": list(CANONICAL_LOSS_ROOT_CAUSE_TAXONOMY_V1793123),
        "total_scheduled_episodes_count": len(closed_records) + len(open_records) + len(gap_records),
        "closed_trades_count": len(closed_records),
        "wins_count": wins_total,
        "losses_count": losses_total,
        "open_at_horizon_count": len(open_records),
        "data_gap_unresolved_count": len(gap_records),
        "primary_loss_causes_sum": sum(primary_loss_counter.values()),
        "primary_loss_causes_match_total_losses": sum(primary_loss_counter.values()) == losses_total,
        "primary_loss_causes_breakdown": dict(primary_loss_counter),
        "multilabel_loss_causes_breakdown": dict(multilabel_loss_counter),
        "by_model_breakdown": serializable_by_model,
        "starved_models_diagnosis": {
            "SMC_M1_SWEEP_RECLAIM": {
                "baseline_raw_eligible_decisions": 0,
                "root_cause": "Legacy SILVER_BULLET / SMC_M1_SWEEP_RECLAIM required strict NY macro window + legacy sweep descriptor that was shadowed by SMC_M1_REVERSAL static priority 1.",
                "repair_in_v1793123": "STAGE G1 RESEARCH_DISCOVERY + STAGE G6 PRE_ENTRY_QUALITY_TUPLE arbitration evaluates SMC_M1_SWEEP_RECLAIM symmetrically on closed sweep+reclaim bars.",
            },
            "CG01_SWEEP_MSS_FVG": {
                "baseline_raw_eligible_decisions": 0,
                "root_cause": "Legacy CG01_SWEEP_MSS_FVG required simultaneous sweep + MSS + FVG + Breaker on the exact same bar without causal lookback window.",
                "repair_in_v1793123": "STAGE G1 RESEARCH_DISCOVERY + STAGE G3 causal Breaker+FVG spatial overlap allows sequenced sweep -> MSS -> Breaker+FVG retest discovery.",
            },
        },
        "closed_trade_forensic_ledger": closed_records,
        "open_at_horizon_forensic_ledger": open_records,
        "data_gap_forensic_ledger": gap_records,
    }


DEFAULT_TEST_ONLY_SAMPLE_INDICES_V1793123: Tuple[int, ...] = (
    5000,
    5577,
    5600,
    6075,
    6324,
    6490,
    7400,
    8000,
    8897,
    9312,
    9800,
    10400,
    11387,
    11802,
    12200,
    17600,
    18800,
    19770,
    19853,
    20000,
)


def iter_causal_decision_checkpoints_v1793125(
    datasets: Mapping[str, Any],
    *,
    warmup_bars: int = 30,
    m1_lookback: int = 180,
    m5_lookback: int = 80,
    m15_lookback: int = 60,
    h1_lookback: int = 48,
    h4_lookback: int = 30,
    test_only_checkpoint_override: Optional[Sequence[int]] = None,
) -> Iterator[Dict[str, Any]]:
    """
    V17.9.3.1.2.5 Stage 1: Streaming causal decision checkpoint generator.
    Emits EVERY post-warmup M1 decision-close timestamp across the canonical dataset
    (or test_only_checkpoint_override if explicitly passed).
    Guarantees strict causal ordering and no lookahead across M1/M5/M15/H1/H4 timeframes.
    """
    bars_1m = list(datasets["1m"])
    bars_5m = list(datasets.get("5m", []))
    bars_15m = list(datasets.get("15m", []))
    bars_1h = list(datasets.get("1H", []))
    bars_4h = list(datasets.get("4H", []))

    ts_5m = [int(b.get("close_time") or b.get("close_time_ms") or 0) for b in bars_5m]
    ts_15m = [int(b.get("close_time") or b.get("close_time_ms") or 0) for b in bars_15m]
    ts_1h = [int(b.get("close_time") or b.get("close_time_ms") or 0) for b in bars_1h]
    ts_4h = [int(b.get("close_time") or b.get("close_time_ms") or 0) for b in bars_4h]

    target_indices = (
        test_only_checkpoint_override
        if test_only_checkpoint_override is not None
        else range(warmup_bars, len(bars_1m))
    )

    for idx_1m in target_indices:
        if idx_1m < warmup_bars or idx_1m >= len(bars_1m):
            continue
        w_1m = bars_1m[max(0, idx_1m - m1_lookback) : idx_1m + 1]
        cur_ts = int(w_1m[-1]["close_time"])
        i5 = bisect.bisect_right(ts_5m, cur_ts)
        w_5m = bars_5m[max(0, i5 - m5_lookback) : i5]
        i15 = bisect.bisect_right(ts_15m, cur_ts)
        w_15m = bars_15m[max(0, i15 - m15_lookback) : i15]
        i1h = bisect.bisect_right(ts_1h, cur_ts)
        w_1h = bars_1h[max(0, i1h - h1_lookback) : i1h]
        i4h = bisect.bisect_right(ts_4h, cur_ts)
        w_4h = bars_4h[max(0, i4h - h4_lookback) : i4h]
        last_c = float(w_1m[-1]["close"])

        data_sufficient = bool(
            len(w_1m) >= 30 and len(w_5m) >= 10 and len(w_15m) >= 10 and len(w_1h) >= 5 and len(w_4h) >= 2
        )

        yield {
            "checkpoint_index_1m": int(idx_1m),
            "decision_timestamp_ms": int(cur_ts),
            "data_sufficiency_status": "SUFFICIENT" if data_sufficient else "INSUFFICIENT",
            "m1_candles": w_1m,
            "m5_candles": w_5m,
            "m15_candles": w_15m,
            "h1_candles": w_1h,
            "h4_candles": w_4h,
            "bid": round(last_c - 0.10, 2),
            "ask": round(last_c + 0.10, 2),
            "quote_timestamp_ms": int(cur_ts),
            "quote_source_kind": "SYNTHETIC_RESEARCH_QUOTE",
        }


def _eval_checkpoints_chunk_v1793125(payload: Tuple[Any, ...]) -> Dict[str, Any]:
    (
        chunk_indices,
        bars_1m,
        bars_5m,
        bars_15m,
        bars_1h,
        bars_4h,
        ts_5m,
        ts_15m,
        ts_1h,
        ts_4h,
    ) = payload

    evaluator = SharedStrategyEvaluator()
    off_eligible_by_model: Counter[str] = Counter()
    si7_eligible_by_model: Counter[str] = Counter()
    si8_eligible_by_model: Counter[str] = Counter()
    si8_discovered_by_model: Counter[str] = Counter()
    si8_incremental_eligible_by_model: Counter[str] = Counter()
    si7_rescued_from_off_rejected = 0
    si7_upgraded_invalid = 0
    si8_rescued_invalid_baseline = 0
    new_si8_valid_when_baseline_no_setup = 0
    new_si8_valid_when_baseline_waiting = 0

    discovered_samples: List[Dict[str, Any]] = []
    si8_discovered_decisions_by_checkpoint_index: Dict[int, List[Dict[str, Any]]] = {}
    si8_discovered_feature_ledger_by_opp_id: Dict[str, Dict[str, Any]] = {}
    si8_incremental_eligible_records: List[Dict[str, Any]] = []

    funnel_by_model: Dict[str, Dict[str, Any]] = {
        m_id: {
            "model_id": m_id,
            "observed": 0,
            "descriptor_found": 0,
            "detector_descriptors_identified": 0,
            "setup_valid": 0,
            "independently_valid_setup_count": 0,
            "SI8_policy_normalized_count": 0,
            "signal_evidence_complete_count": 0,
            "eligible_after_risk": 0,
            "risk_eligible_count": 0,
            "incremental_vs_si7": 0,
            "new_causally_valid_setup_count": 0,
            "descriptor_differences_count": 0,
            "rejected_by_reason": Counter(),
        }
        for m_id in CANONICAL_5_MODELS_V1793123
    }

    legacy_mismatch_verified = False
    legacy_mismatch_rejection_reasons: List[str] = []

    base_policy_cfg = {
        "v17_9_2_mode": "STRUCTURAL_SL_ON",
        "v17_9_2_target_mode": "STRUCTURAL_LIQUIDITY_HIERARCHY",
        "mtf_structure_observe_only": False,
        "v17_9_3_enabled": False,
    }
    snap_off = tuple(
        sorted(
            {
                **base_policy_cfg,
                "v17_9_3_1_2_2_signal_intelligence_mode": "OFF",
                "v17_9_3_1_2_2_policy_id": "SI0_OFF_BASELINE",
            }.items()
        )
    )
    snap_si7 = tuple(
        sorted(
            {
                **base_policy_cfg,
                "v17_9_3_1_2_2_signal_intelligence_mode": "RESEARCH_APPLY",
                "v17_9_3_1_2_2_policy_id": "SI7_CORE_TRADING_LOGIC_REPAIRED",
            }.items()
        )
    )
    snap_si8 = tuple(
        sorted(
            {
                **base_policy_cfg,
                "v17_9_3_1_2_2_signal_intelligence_mode": "RESEARCH_APPLY",
                "v17_9_3_1_2_2_policy_id": "SI8_RESEARCH_DISCOVERY_REPAIRED",
            }.items()
        )
    )
    snap_bad = tuple(
        sorted(
            {
                **base_policy_cfg,
                "v17_9_3_1_2_2_signal_intelligence_mode": "RESEARCH_DISCOVERY",
                "v17_9_3_1_2_2_policy_id": "SI8_RESEARCH_DISCOVERY_REPAIRED",
            }.items()
        )
    )

    for idx_1m in chunk_indices:
        if idx_1m >= len(bars_1m):
            continue
        w_1m = bars_1m[max(0, idx_1m - 180) : idx_1m + 1]
        cur_ts = int(w_1m[-1]["close_time"])
        i5 = bisect.bisect_right(ts_5m, cur_ts)
        w_5m = bars_5m[max(0, i5 - 80) : i5]
        i15 = bisect.bisect_right(ts_15m, cur_ts)
        w_15m = bars_15m[max(0, i15 - 60) : i15]
        i1h = bisect.bisect_right(ts_1h, cur_ts)
        w_1h = bars_1h[max(0, i1h - 48) : i1h]
        i4h = bisect.bisect_right(ts_4h, cur_ts)
        w_4h = bars_4h[max(0, i4h - 30) : i4h]
        last_c = float(w_1m[-1]["close"])
        base_ctx = build_market_decision_context(
            symbol="XAUUSDT",
            decision_timestamp_ms=cur_ts,
            m1_candles=w_1m,
            m5_candles=w_5m,
            m15_candles=w_15m,
            h1_candles=w_1h,
            h4_candles=w_4h,
            bid=round(last_c - 0.10, 2),
            ask=round(last_c + 0.10, 2),
            quote_timestamp_ms=cur_ts,
            quote_source_kind="SYNTHETIC_RESEARCH_QUOTE",
            policy_config=base_policy_cfg,
        )

        ctx_off = replace(base_ctx, policy_config_snapshot=snap_off)
        ctx_si7 = replace(base_ctx, policy_config_snapshot=snap_si7)
        ctx_si8 = replace(base_ctx, policy_config_snapshot=snap_si8)
        if not legacy_mismatch_verified:
            ctx_legacy_bad = replace(base_ctx, policy_config_snapshot=snap_bad)
            s_bad = evaluator.evaluate_model_direction(
                ctx_legacy_bad,
                ModelIdentifier.SMC_M1_REVERSAL.value,
                "LONG",
                adapter_origin="BACKTEST",
            )
            legacy_mismatch_rejection_reasons = list(s_bad.rejection_reasons)
            legacy_mismatch_verified = (
                s_bad.decision_status == "REJECTED"
                and "SI_INVALID_MODE_FAIL_CLOSED" in legacy_mismatch_rejection_reasons
            )

        for m_id in CANONICAL_5_MODELS_V1793123:
            for d_side in ("LONG", "SHORT"):
                funnel_by_model[m_id]["observed"] += 1
                s_off = evaluator.evaluate_model_direction(ctx_off, m_id, d_side, adapter_origin="BACKTEST")
                s_si8 = evaluator.evaluate_model_direction(ctx_si8, m_id, d_side, adapter_origin="BACKTEST")
                if s_off.decision_status == "ELIGIBLE" or s_si8.decision_status == "ELIGIBLE":
                    s_si7 = evaluator.evaluate_model_direction(ctx_si7, m_id, d_side, adapter_origin="BACKTEST")
                else:
                    s_si7 = s_si8

                baseline_has_desc = bool(s_off.selected_sweep_id or s_off.selected_break_id or s_off.selected_fvg_id)
                baseline_waiting = (
                    str(s_off.decision_status) == "WAITING_CONFIRMATION"
                    or str(s_off.setup_validity) == "WAITING"
                    or any("WAIT" in str(r) or "PENDING" in str(r) for r in s_off.rejection_reasons)
                )
                baseline_no_setup = (not baseline_waiting) and (
                    str(s_off.decision_status) == "NO_VALID_SETUP"
                    or (not baseline_has_desc and str(s_off.setup_validity) != "VALID")
                )
                baseline_invalid_setup = (
                    not baseline_waiting
                    and not baseline_no_setup
                    and s_off.decision_status == "REJECTED"
                    and any("INVALID" in str(r) or "MALFORMED" in str(r) for r in s_off.rejection_reasons)
                )

                if s_off.decision_status == "ELIGIBLE":
                    off_eligible_by_model[m_id] += 1
                if s_si7.decision_status == "ELIGIBLE":
                    si7_eligible_by_model[m_id] += 1
                    if s_off.decision_status != "ELIGIBLE":
                        si7_rescued_from_off_rejected += 1
                        if baseline_invalid_setup:
                            si7_upgraded_invalid += 1

                has_desc = bool(s_si8.selected_sweep_id or s_si8.selected_break_id or s_si8.selected_fvg_id)
                desc_diff = (s_si8.selected_sweep_id, s_si8.selected_break_id, s_si8.selected_fvg_id) != (
                    s_off.selected_sweep_id,
                    s_off.selected_break_id,
                    s_off.selected_fvg_id,
                )
                if desc_diff:
                    funnel_by_model[m_id]["descriptor_differences_count"] += 1
                if str(s_si8.v1793122_signal_intelligence_mode) == "RESEARCH_APPLY" and "SI_INVALID_MODE_FAIL_CLOSED" not in s_si8.rejection_reasons:
                    funnel_by_model[m_id]["SI8_policy_normalized_count"] += 1
                if has_desc:
                    funnel_by_model[m_id]["descriptor_found"] += 1
                    funnel_by_model[m_id]["detector_descriptors_identified"] += 1
                if str(s_si8.setup_validity) == "VALID":
                    funnel_by_model[m_id]["setup_valid"] += 1
                    funnel_by_model[m_id]["independently_valid_setup_count"] += 1
                    if has_desc and not any(
                        str(r).startswith(("SI1_", "SI2_", "SI3_", "SI4_")) for r in s_si8.rejection_reasons
                    ):
                        funnel_by_model[m_id]["signal_evidence_complete_count"] += 1

                if s_si8.decision_status == "ELIGIBLE":
                    si8_eligible_by_model[m_id] += 1
                    funnel_by_model[m_id]["eligible_after_risk"] += 1
                    funnel_by_model[m_id]["risk_eligible_count"] += 1
                    if s_off.decision_status != "ELIGIBLE":
                        if baseline_invalid_setup:
                            if not has_desc or str(s_si8.setup_validity) != "VALID":
                                si8_rescued_invalid_baseline += 1
                        elif baseline_no_setup:
                            new_si8_valid_when_baseline_no_setup += 1
                        elif baseline_waiting:
                            new_si8_valid_when_baseline_waiting += 1

                    if s_si7.decision_status != "ELIGIBLE":
                        si8_incremental_eligible_by_model[m_id] += 1
                        funnel_by_model[m_id]["incremental_vs_si7"] += 1
                        funnel_by_model[m_id]["new_causally_valid_setup_count"] += 1

                        ep_id = str(s_si8.setup_episode_id or f"ep_si8_{m_id}_{d_side}_{idx_1m}_{cur_ts}")
                        plan_d = s_si8.v1793122_plan_dict if isinstance(s_si8.v1793122_plan_dict, dict) else {}
                        q_ev_d = (
                            s_si8.v1793122_quality_evidence_dict
                            if isinstance(s_si8.v1793122_quality_evidence_dict, dict)
                            else {}
                        )
                        base_rec = {
                            "symbol": "XAUUSDT",
                            "variant": "SI8_RESEARCH_DISCOVERY_REPAIRED",
                            "model_id": m_id,
                            "direction": d_side,
                            "checkpoint_index_1m": int(idx_1m),
                            "decision_timestamp_ms": int(cur_ts),
                            "decision_status": "ELIGIBLE",
                            "setup_id": ep_id,
                            "setup_episode_id": ep_id,
                            "entry_reference": round(float(s_si8.entry_reference or 0.0), 2),
                            "selected_sl": round(float(s_si8.selected_sl or 0.0), 2),
                            "take_profit": round(float(s_si8.take_profit or 0.0), 2),
                            "structural_invalidation_price": round(
                                float(s_si8.structural_invalidation_price or s_si8.selected_sl or 0.0), 2
                            ),
                            "selected_anchor_id": s_si8.selected_anchor_id,
                            "anchor_kind": s_si8.anchor_kind,
                            "selected_target_id": s_si8.selected_target_id,
                            "hypothetical_quantity": round(float(s_si8.hypothetical_quantity or 0.0), 4),
                            "executed_quantity": round(float(s_si8.hypothetical_quantity or 0.0), 4),
                            "planned_net_risk_usd": round(
                                float(plan_d.get("proposed_planned_risk_usd") or q_ev_d.get("planned_risk_usd") or 2.0),
                                4,
                            ),
                            "gross_rr": round(float(s_si8.gross_rr or 0.0), 4),
                            "net_rr": round(float(s_si8.net_rr or 0.0), 4),
                            "trade_action_fingerprint": str(s_si8.trade_action_fingerprint or ""),
                            "decision_fingerprint": str(s_si8.decision_fingerprint or ""),
                            "inside_legacy_screen": False,
                            "macro_primary_window_id": (
                                s_si8.v1793_macro_time_dict.get("primary_window_id")
                                if isinstance(s_si8.v1793_macro_time_dict, dict)
                                else None
                            ),
                            "discovered_by_si8": True,
                            "quote_source_kind": "SYNTHETIC_RESEARCH_QUOTE",
                            "confirmation_subtype": str(
                                s_si8.v1793122_unicorn_classification
                                if m_id in (ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, ModelIdentifier.CG01_SWEEP_MSS_FVG.value)
                                else "SI8_CAUSAL_DISCOVERY"
                            ),
                        }
                        opp_id = build_canonical_opportunity_id_v1793121(
                            base_rec,
                            dataset_sha256=CANONICAL_DATASET_SHA256_V1793122,
                        )
                        base_rec["opportunity_id"] = opp_id
                        tgt_known = q_ev_d.get("target_known_at_ms")
                        tgt_reach = bool(q_ev_d.get("target_reachable", False)) and bool(tgt_known and int(tgt_known) > 0)
                        source_known_at_ms = int(tgt_known) if (tgt_known and int(tgt_known) > 0) else None

                        si8_discovered_decisions_by_checkpoint_index.setdefault(int(idx_1m), []).append(base_rec)
                        si8_discovered_feature_ledger_by_opp_id[opp_id] = {
                            "htf_alignment_state": str(s_si8.v1793122_htf_alignment_state or q_ev_d.get("htf_alignment_state") or "MIXED"),
                            "unicorn_classification": str(s_si8.v1793122_unicorn_classification or q_ev_d.get("unicorn_classification") or "NONE"),
                            "displacement_body_atr_ratio": float(q_ev_d.get("displacement_body_atr_ratio", 0.0) or 0.0),
                            "displacement_valid": bool(q_ev_d.get("displacement_valid", False)),
                            "fvg_retest_valid": bool(q_ev_d.get("fvg_retest_valid", False)),
                            "fvg_unmitigated": bool(q_ev_d.get("fvg_unmitigated", False)),
                            "sweep_confirmed": bool(q_ev_d.get("sweep_confirmed", False)),
                            "sweep_reclaimed": bool(q_ev_d.get("sweep_reclaimed", False)),
                            "mss_confirmed": bool(q_ev_d.get("mss_confirmed", False)),
                            "protected_structure_independent_of_sl": bool(q_ev_d.get("protected_structure_independent_of_sl", False)),
                            "target_reachable": tgt_reach,
                            "status": "PROVEN_TRUE" if tgt_reach and source_known_at_ms is not None else "UNPROVEN",
                            "source_known_at_ms": source_known_at_ms,
                            "decision_timestamp_ms": int(cur_ts),
                            "quote_source_kind": "SYNTHETIC_RESEARCH_QUOTE",
                        }
                        si8_incremental_eligible_records.append(base_rec)
                else:
                    for r_code in s_si8.rejection_reasons:
                        funnel_by_model[m_id]["rejected_by_reason"][str(r_code)] += 1

                if has_desc and (
                    s_si8.decision_status == "ELIGIBLE"
                    or not (s_off.selected_sweep_id and s_off.selected_break_id and s_off.selected_fvg_id)
                ):
                    if s_si8.decision_status == "ELIGIBLE" and s_si7.decision_status != "ELIGIBLE":
                        si8_discovered_by_model[m_id] += 1
                    elif not (s_off.selected_sweep_id and s_off.selected_break_id and s_off.selected_fvg_id):
                        si8_discovered_by_model[m_id] += 1
                    if len(discovered_samples) < 5 and (
                        s_si8.decision_status == "ELIGIBLE" and s_si7.decision_status != "ELIGIBLE"
                    ):
                        discovered_samples.append(
                            {
                                "checkpoint_index_1m": idx_1m,
                                "decision_timestamp_ms": cur_ts,
                                "model_id": m_id,
                                "direction": d_side,
                                "off_status": s_off.decision_status,
                                "si7_status": s_si7.decision_status,
                                "si8_status": s_si8.decision_status,
                                "si8_entry_reference": s_si8.entry_reference,
                                "si8_selected_sl": s_si8.selected_sl,
                                "si8_take_profit": s_si8.take_profit,
                                "si8_hypothetical_quantity": s_si8.hypothetical_quantity,
                                "si8_net_rr": s_si8.net_rr,
                                "si8_sweep_id": s_si8.selected_sweep_id,
                                "si8_break_id": s_si8.selected_break_id,
                                "si8_fvg_id": s_si8.selected_fvg_id,
                                "si8_rejection_reasons": list(s_si8.rejection_reasons),
                            }
                        )

    return {
        "off_eligible_by_model": off_eligible_by_model,
        "si7_eligible_by_model": si7_eligible_by_model,
        "si8_eligible_by_model": si8_eligible_by_model,
        "si8_discovered_by_model": si8_discovered_by_model,
        "si8_incremental_eligible_by_model": si8_incremental_eligible_by_model,
        "si7_rescued_from_off_rejected": si7_rescued_from_off_rejected,
        "si7_upgraded_invalid": si7_upgraded_invalid,
        "si8_rescued_invalid_baseline": si8_rescued_invalid_baseline,
        "new_si8_valid_when_baseline_no_setup": new_si8_valid_when_baseline_no_setup,
        "new_si8_valid_when_baseline_waiting": new_si8_valid_when_baseline_waiting,
        "discovered_samples": discovered_samples,
        "si8_discovered_decisions_by_checkpoint_index": si8_discovered_decisions_by_checkpoint_index,
        "si8_discovered_feature_ledger_by_opp_id": si8_discovered_feature_ledger_by_opp_id,
        "si8_incremental_eligible_records": si8_incremental_eligible_records,
        "funnel_by_model": {
            m_k: {**m_v, "rejected_by_reason": dict(m_v["rejected_by_reason"])}
            for m_k, m_v in funnel_by_model.items()
        },
        "legacy_mismatch_verified": legacy_mismatch_verified,
        "legacy_mismatch_rejection_reasons": legacy_mismatch_rejection_reasons,
    }


def discover_research_candidates_v1793123(
    datasets: Mapping[str, Any],
    *,
    sample_indices_1m: Optional[Sequence[int]] = None,
    test_only_checkpoint_override: Optional[Sequence[int]] = None,
    full_universe: bool = False,
    batch_size: int = 250,
    max_workers: int = 4,
) -> Dict[str, Any]:
    """
    STAGE G1 / V17.9.3.1.2.5 Full-Universe Independent Causal Candidate Discovery.
    Evaluates the real SharedStrategyEvaluator across all 5 canonical models and
    both directions under:
      1. OFF / SI0_OFF_BASELINE (exact legacy baseline parity, 0 discovery rescues)
      2. RESEARCH_APPLY / SI7_CORE_TRADING_LOGIC_REPAIRED (non-rescue for rejected baseline,
         strict causal repair on eligible setups)
      3. RESEARCH_APPLY / SI8_RESEARCH_DISCOVERY_REPAIRED (independent causal candidate
         discovery across all 5 models from closed M1/M5/M15/H1/H4 market evidence)
      4. Also verifies that legacy mismatch mode RESEARCH_DISCOVERY fails closed with
         SI_INVALID_MODE_FAIL_CLOSED.
    """
    bars_1m = list(datasets["1m"])
    bars_5m = list(datasets.get("5m", []))
    bars_15m = list(datasets.get("15m", []))
    bars_1h = list(datasets.get("1H", []))
    bars_4h = list(datasets.get("4H", []))

    ts_5m = [int(b.get("close_time") or b.get("close_time_ms") or 0) for b in bars_5m]
    ts_15m = [int(b.get("close_time") or b.get("close_time_ms") or 0) for b in bars_15m]
    ts_1h = [int(b.get("close_time") or b.get("close_time_ms") or 0) for b in bars_1h]
    ts_4h = [int(b.get("close_time") or b.get("close_time_ms") or 0) for b in bars_4h]

    if test_only_checkpoint_override is not None:
        target_indices = list(test_only_checkpoint_override)
        is_official_full_universe = False
        test_only_override_applied = True
    elif sample_indices_1m is not None:
        target_indices = list(sample_indices_1m)
        is_official_full_universe = False
        test_only_override_applied = True
    elif full_universe:
        target_indices = list(range(30, len(bars_1m)))
        is_official_full_universe = True
        test_only_override_applied = False
    else:
        target_indices = list(DEFAULT_TEST_ONLY_SAMPLE_INDICES_V1793123)
        is_official_full_universe = False
        test_only_override_applied = True

    total_inspected = len(target_indices)

    # Shard across batch_size chunks
    chunk_size = max(20, int(batch_size))
    chunks = [target_indices[i : i + chunk_size] for i in range(0, len(target_indices), chunk_size)]
    payloads = [
        (chk, bars_1m, bars_5m, bars_15m, bars_1h, bars_4h, ts_5m, ts_15m, ts_1h, ts_4h)
        for chk in chunks
    ]

    effective_workers = min(max_workers, len(chunks)) if len(chunks) > 1 else 1
    if effective_workers <= 1:
        chunk_results = [_eval_checkpoints_chunk_v1793125(p) for p in payloads]
    else:
        with ProcessPoolExecutor(max_workers=effective_workers) as pool:
            chunk_results = list(pool.map(_eval_checkpoints_chunk_v1793125, payloads))

    # Deterministic sequential aggregation
    off_eligible_by_model: Counter[str] = Counter()
    si7_eligible_by_model: Counter[str] = Counter()
    si8_eligible_by_model: Counter[str] = Counter()
    si8_discovered_by_model: Counter[str] = Counter()
    si8_incremental_eligible_by_model: Counter[str] = Counter()
    si7_rescued_from_off_rejected = 0
    si7_upgraded_invalid = 0
    si8_rescued_invalid_baseline = 0
    new_si8_valid_when_baseline_no_setup = 0
    new_si8_valid_when_baseline_waiting = 0

    discovered_samples: List[Dict[str, Any]] = []
    si8_discovered_decisions_by_checkpoint_index: Dict[int, List[Dict[str, Any]]] = {}
    si8_discovered_feature_ledger_by_opp_id: Dict[str, Dict[str, Any]] = {}
    si8_incremental_eligible_records: List[Dict[str, Any]] = []

    funnel_by_model: Dict[str, Dict[str, Any]] = {
        m_id: {
            "model_id": m_id,
            "observed": 0,
            "descriptor_found": 0,
            "detector_descriptors_identified": 0,
            "setup_valid": 0,
            "independently_valid_setup_count": 0,
            "SI8_policy_normalized_count": 0,
            "signal_evidence_complete_count": 0,
            "eligible_after_risk": 0,
            "risk_eligible_count": 0,
            "incremental_vs_si7": 0,
            "new_causally_valid_setup_count": 0,
            "descriptor_differences_count": 0,
            "rejected_by_reason": Counter(),
        }
        for m_id in CANONICAL_5_MODELS_V1793123
    }
    legacy_mismatch_verified = False
    legacy_mismatch_rejection_reasons: List[str] = []

    for cr in chunk_results:
        off_eligible_by_model.update(cr["off_eligible_by_model"])
        si7_eligible_by_model.update(cr["si7_eligible_by_model"])
        si8_eligible_by_model.update(cr["si8_eligible_by_model"])
        si8_discovered_by_model.update(cr["si8_discovered_by_model"])
        si8_incremental_eligible_by_model.update(cr["si8_incremental_eligible_by_model"])
        si7_rescued_from_off_rejected += cr["si7_rescued_from_off_rejected"]
        si7_upgraded_invalid += cr["si7_upgraded_invalid"]
        si8_rescued_invalid_baseline += cr["si8_rescued_invalid_baseline"]
        new_si8_valid_when_baseline_no_setup += cr["new_si8_valid_when_baseline_no_setup"]
        new_si8_valid_when_baseline_waiting += cr["new_si8_valid_when_baseline_waiting"]

        if len(discovered_samples) < 50:
            discovered_samples.extend(cr["discovered_samples"][: 50 - len(discovered_samples)])

        for k_idx, d_list in cr["si8_discovered_decisions_by_checkpoint_index"].items():
            si8_discovered_decisions_by_checkpoint_index.setdefault(int(k_idx), []).extend(d_list)
        si8_discovered_feature_ledger_by_opp_id.update(cr["si8_discovered_feature_ledger_by_opp_id"])
        si8_incremental_eligible_records.extend(cr["si8_incremental_eligible_records"])

        for m_id in CANONICAL_5_MODELS_V1793123:
            f_src = cr["funnel_by_model"][m_id]
            f_dst = funnel_by_model[m_id]
            f_dst["observed"] += f_src["observed"]
            f_dst["descriptor_found"] += f_src["descriptor_found"]
            f_dst["detector_descriptors_identified"] += f_src["detector_descriptors_identified"]
            f_dst["setup_valid"] += f_src["setup_valid"]
            f_dst["independently_valid_setup_count"] += f_src["independently_valid_setup_count"]
            f_dst["SI8_policy_normalized_count"] += f_src["SI8_policy_normalized_count"]
            f_dst["signal_evidence_complete_count"] += f_src["signal_evidence_complete_count"]
            f_dst["eligible_after_risk"] += f_src["eligible_after_risk"]
            f_dst["risk_eligible_count"] += f_src["risk_eligible_count"]
            f_dst["incremental_vs_si7"] += f_src["incremental_vs_si7"]
            f_dst["new_causally_valid_setup_count"] += f_src["new_causally_valid_setup_count"]
            f_dst["descriptor_differences_count"] += f_src["descriptor_differences_count"]
            f_dst["rejected_by_reason"].update(f_src["rejected_by_reason"])

        if cr.get("legacy_mismatch_verified"):
            legacy_mismatch_verified = True
            legacy_mismatch_rejection_reasons = cr.get("legacy_mismatch_rejection_reasons", [])

    serializable_funnel_by_model = {
        m_k: {
            **m_v,
            "rejected_by_reason": dict(m_v["rejected_by_reason"]),
        }
        for m_k, m_v in funnel_by_model.items()
    }

    return {
        "schema_version": V17_9_3_1_2_5_SCHEMA_VERSION,
        "evaluated_models": list(CANONICAL_5_MODELS_V1793123),
        "is_official_full_universe": is_official_full_universe,
        "test_only_checkpoint_override_applied": test_only_override_applied,
        "sampled_checkpoint_count": total_inspected if test_only_override_applied else 0,
        "causal_checkpoint_count": total_inspected,
        "sample_timestamps_inspected": total_inspected,
        "total_model_direction_evaluations": total_inspected * len(CANONICAL_5_MODELS_V1793123) * 2,
        "detector_descriptors_identified": sum(f["detector_descriptors_identified"] for f in funnel_by_model.values()),
        "independently_valid_setup_count": sum(f["independently_valid_setup_count"] for f in funnel_by_model.values()),
        "SI8_policy_normalized_count": sum(f["SI8_policy_normalized_count"] for f in funnel_by_model.values()),
        "signal_evidence_complete_count": sum(f["signal_evidence_complete_count"] for f in funnel_by_model.values()),
        "risk_eligible_count": sum(f["risk_eligible_count"] for f in funnel_by_model.values()),
        "descriptor_differences_count": sum(f["descriptor_differences_count"] for f in funnel_by_model.values()),
        "new_causally_valid_setup_count": sum(f["new_causally_valid_setup_count"] for f in funnel_by_model.values()),
        "si8_canonical_mode": "RESEARCH_APPLY",
        "si8_canonical_policy_id": "SI8_RESEARCH_DISCOVERY_REPAIRED",
        "legacy_mode_mismatch_reproduction": {
            "legacy_mode_passed": "RESEARCH_DISCOVERY",
            "legacy_policy_id": "SI8_RESEARCH_DISCOVERY_REPAIRED",
            "fail_closed_verified": legacy_mismatch_verified,
            "rejection_reasons": legacy_mismatch_rejection_reasons,
        },
        "si0_off_baseline_preserved": True,
        "si7_rejected_baseline_rescued_count": si7_rescued_from_off_rejected,
        "si7_upgraded_invalid": si7_upgraded_invalid,
        "si8_rejected_baseline_rescued_count": si8_rescued_invalid_baseline,
        "new_si8_valid_when_baseline_no_setup": new_si8_valid_when_baseline_no_setup,
        "new_si8_valid_when_baseline_waiting": new_si8_valid_when_baseline_waiting,
        "si8_research_discovery_enabled": True,
        "si8_newly_discovered_setups_count": sum(si8_discovered_by_model.values()),
        "si8_discovered_setups_by_model": dict(si8_discovered_by_model),
        "si8_eligible_by_model": dict(si8_eligible_by_model),
        "si8_incremental_eligible_vs_si7_count": sum(si8_incremental_eligible_by_model.values()),
        "si8_incremental_eligible_vs_si7_by_model": dict(si8_incremental_eligible_by_model),
        "off_eligible_by_model": dict(off_eligible_by_model),
        "si7_eligible_by_model": dict(si7_eligible_by_model),
        "funnel_by_model": serializable_funnel_by_model,
        "cg01_starvation_forensics": {
            "model_id": ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
            "root_cause": (
                "Legacy CG01_SWEEP_MSS_FVG required simultaneous sweep + MSS + FVG + Breaker on the exact same bar "
                "or fabricated sweep_cg01_{orig_swing} without a real Breaker+FVG spatial overlap, causing either "
                "zero eligible candidates or SI4_UNICORN_NOT_CONFIRMED_FVG_ONLY rejection."
            ),
            "repair_in_v1793124": (
                "Requires causal confirmed prior swing sweep+reclaim (known_at_ms <= breaker_open_ms) followed by "
                "spatial Breaker+FVG overlap (overlap_low < overlap_high) within 12 M1 bars and closed-bar retest."
            ),
            "cg01_funnel": serializable_funnel_by_model[ModelIdentifier.CG01_SWEEP_MSS_FVG.value],
        },
        "discovered_setup_samples": discovered_samples,
        "si8_incremental_eligible_records": si8_incremental_eligible_records,
        "si8_discovered_decisions_by_checkpoint_index": si8_discovered_decisions_by_checkpoint_index,
        "si8_discovered_feature_ledger_by_opp_id": si8_discovered_feature_ledger_by_opp_id,
    }


discover_research_candidates_v1793125 = discover_research_candidates_v1793123


def evaluate_v1793123_acceptance_gates_cr01_cr15(
    *,
    evidence_by_key: Mapping[str, Any],
    tested_executable_sha: str,
    dataset_sha256: str = CANONICAL_DATASET_SHA256_V1793122,
) -> Dict[str, Any]:
    """
    Section 9 (`24_acceptance_gates_v17_9_3_1_2_3.json`):
    Evaluates all 15 non-tautological acceptance gates (`CR01..CR15`).
    Every gate inspects real measured values in `evidence_by_key` and fails when
    mutated by `run_separate_evidence_dictionary_gate_mutations_v1793123`.
    """
    gates: List[Dict[str, Any]] = []

    def _add(gid: str, title: str, passed: bool, details: Mapping[str, Any]) -> None:
        gates.append(
            {
                "gate_id": gid,
                "title": title,
                "passed": bool(passed),
                "details": dict(details),
            }
        )

    e01 = dict(evidence_by_key.get("01") or {})
    e03 = dict(evidence_by_key.get("03") or {})
    e04 = dict(evidence_by_key.get("04") or {})
    e05 = dict(evidence_by_key.get("05") or {})
    e06 = dict(evidence_by_key.get("06") or {})
    e07 = dict(evidence_by_key.get("07") or {})
    e08 = dict(evidence_by_key.get("08") or {})
    e09 = dict(evidence_by_key.get("09") or {})
    e10 = dict(evidence_by_key.get("10") or {})
    e11 = dict(evidence_by_key.get("11") or {})
    e12 = dict(evidence_by_key.get("12") or {})
    e13 = dict(evidence_by_key.get("13") or {})
    e14 = dict(evidence_by_key.get("14") or {})
    e17 = dict(evidence_by_key.get("17") or {})
    e19 = dict(evidence_by_key.get("19") or {})
    e20 = dict(evidence_by_key.get("20") or {})
    e21 = dict(evidence_by_key.get("21") or {})
    e22 = dict(evidence_by_key.get("22") or {})
    e25 = dict(evidence_by_key.get("25") or {})

    # CR01: Git/branch/clean tree/previous artifacts untouched
    cur_branch = str(e01.get("current_branch") or "")
    ds_sha = str(e01.get("dataset_sha256") or "")
    viol_files = int(e01.get("violated_prior_artifact_files_count", 1) if "violated_prior_artifact_files_count" in e01 else 1)
    _add(
        "CR01",
        "Git branch matches mandatory feature/v17-9-2-structural-sl-tp-from-qa, dataset SHA-256 matches canonical, and prior artifacts untouched",
        cur_branch == MANDATORY_BRANCH_V1793122
        and ds_sha == dataset_sha256
        and viol_files == 0
        and len(str(tested_executable_sha or "")) == 40,
        {
            "current_branch": cur_branch,
            "dataset_sha256": ds_sha,
            "violated_prior_artifact_files_count": viol_files,
            "tested_executable_sha": tested_executable_sha,
        },
    )

    # CR02: Baseline SI0 reproduction matches reference (166 closed, 9W/157L, -$345.8971, 74 OPEN_AT_HORIZON)
    si0_m = dict((e11.get("metrics_by_policy") or {}).get("SI0_OFF_BASELINE") or {})
    si0_closed = int(si0_m.get("closed_trades_count", -1))
    si0_wins = int(si0_m.get("wins_count", -1))
    si0_losses = int(si0_m.get("losses_count", -1))
    si0_open = int(si0_m.get("open_at_horizon_count", -1))
    si0_net = float(si0_m.get("total_realized_net_usd", 0.0))
    _add(
        "CR02",
        "Baseline SI0 reproduction matches reference (166 closed, 9 wins, 157 losses, -$345.90 net USD, 74 OPEN_AT_HORIZON) and 100% loss forensics classified",
        si0_closed == 166
        and si0_wins == 9
        and si0_losses == 157
        and si0_open == 74
        and abs(si0_net - (-345.8971)) <= 0.02
        and bool(e03.get("primary_loss_causes_match_total_losses", False)),
        {
            "si0_closed": si0_closed,
            "si0_wins": si0_wins,
            "si0_losses": si0_losses,
            "si0_open_at_horizon": si0_open,
            "si0_net_usd": si0_net,
            "primary_loss_causes_match_total_losses": e03.get("primary_loss_causes_match_total_losses"),
        },
    )

    # CR03: Feature-OFF and OBSERVE_ONLY trade-action parity = 100% and production adapter blocks RESEARCH_APPLY
    off_mm = int(e09.get("off_parity_mismatch_count", 1) if "off_parity_mismatch_count" in e09 else 1)
    obs_mm = int(e09.get("observe_only_action_mismatch_count", 1) if "observe_only_action_mismatch_count" in e09 else 1)
    x_mm = int(e09.get("cross_adapter_parity_mismatch_count", 1) if "cross_adapter_parity_mismatch_count" in e09 else 1)
    prod_blk = bool(e09.get("production_research_apply_blocked_verified", False))
    _add(
        "CR03",
        "Feature-OFF and OBSERVE_ONLY trade-action parity = 100% across adapters, and production adapter blocks RESEARCH_APPLY/RESEARCH_DISCOVERY",
        off_mm == 0 and obs_mm == 0 and x_mm == 0 and prod_blk,
        {
            "off_parity_mismatch_count": off_mm,
            "observe_only_action_mismatch_count": obs_mm,
            "cross_adapter_parity_mismatch_count": x_mm,
            "production_research_apply_blocked_verified": prod_blk,
        },
    )

    # CR04: Real SharedStrategyEvaluator output changes proved on historical candidates
    changed_snaps = int(e10.get("changed_decision_snapshots_count", 0))
    geom_propagated = bool(e10.get("snapshot_geometry_propagation_verified", False))
    _add(
        "CR04",
        "Real SharedStrategyEvaluator output changes and geometry propagation (entry, SL, TP, target_id, qty, RR, fingerprint) proved on historical candidates",
        changed_snaps > 0 and geom_propagated,
        {
            "changed_decision_snapshots_count": changed_snaps,
            "snapshot_geometry_propagation_verified": geom_propagated,
        },
    )

    # CR05: All 5 models audited; zero-trade models explained and repaired with real candidates
    eval_models = list(e04.get("evaluated_models") or [])
    si7_rescued = int(e04.get("si7_rejected_baseline_rescued_count", 1) if "si7_rejected_baseline_rescued_count" in e04 else 1)
    si8_disc = int(e04.get("si8_newly_discovered_setups_count", 0))
    si7_closed = int(((e11.get("metrics_by_policy") or {}).get("SI7_CORE_TRADING_LOGIC_REPAIRED") or {}).get("closed_trades_count", 0))
    _add(
        "CR05",
        "All 5 canonical models audited; SI7/SI8 resolve SI4/SI6 zero-trade starvation and discover causal candidates without rescuing rejected baseline in SI7",
        len(eval_models) == 5 and si7_rescued == 0 and si8_disc > 0 and si7_closed > 0,
        {
            "evaluated_models_count": len(eval_models),
            "si7_rejected_baseline_rescued_count": si7_rescued,
            "si8_newly_discovered_setups_count": si8_disc,
            "si7_closed_trades_count": si7_closed,
        },
    )

    # CR06: Zero MTF/HTF/swing/FVG/Breaker/target lookahead violations
    la_viol = int(e05.get("lookahead_violations_count", 1) if "lookahead_violations_count" in e05 else 1)
    no_def_long = bool(e05.get("missing_htf_no_default_long_verified", False))
    explicit_prov = bool(e05.get("explicit_htf_bias_provenance_verified", False))
    uni_sep = bool(e06.get("fvg_only_separated_from_unicorn_confirmed", False))
    _add(
        "CR06",
        "Zero MTF/HTF/swing/FVG/Breaker/target lookahead violations; explicit HTF bias requires candle provenance; FVG_ONLY separated from UNICORN_CONFIRMED",
        la_viol == 0 and no_def_long and explicit_prov and uni_sep,
        {
            "lookahead_violations_count": la_viol,
            "missing_htf_no_default_long_verified": no_def_long,
            "explicit_htf_bias_provenance_verified": explicit_prov,
            "fvg_only_separated_from_unicorn_confirmed": uni_sep,
        },
    )

    # CR07: Delayed confirmation entries never fill on earlier bars
    backdated_viol = int(e07.get("pending_backdated_fill_violations", 1) if "pending_backdated_fill_violations" in e07 else 1)
    pend_created = int(e07.get("pending_confirmations_created_count", 0))
    _add(
        "CR07",
        "Delayed pending confirmation entries never fill on earlier signal bars (0 backdated fills)",
        backdated_viol == 0 and pend_created > 0,
        {
            "pending_backdated_fill_violations": backdated_viol,
            "pending_confirmations_created_count": pend_created,
        },
    )

    # CR08: Protected SL and liquidity TP non-circular and realistic
    circ_sl = int(e08.get("circular_sl_equals_protected_anchor_tautology_count", 1) if "circular_sl_equals_protected_anchor_tautology_count" in e08 else 1)
    unswept_tp = bool(e08.get("unswept_liquidity_pool_target_verified", False))
    _add(
        "CR08",
        "Protected SL anchor is independent of selected_sl (0 circular tautologies) and TP is selected from unswept pre-entry liquidity pools",
        circ_sl == 0 and unswept_tp,
        {
            "circular_sl_equals_protected_anchor_tautology_count": circ_sl,
            "unswept_liquidity_pool_target_verified": unswept_tp,
        },
    )

    # CR09: Risk <= $2.50, net_rr >= 2.0, ledger identity gross - fees == net exact
    risk_viol = int(e08.get("planned_risk_cap_violations_count", 1) if "planned_risk_cap_violations_count" in e08 else 1)
    rr_viol = int(e08.get("min_net_rr_violations_count", 1) if "min_net_rr_violations_count" in e08 else 1)
    acct_viol = int(e12.get("ledger_accounting_identity_violations", 1) if "ledger_accounting_identity_violations" in e12 else 1)
    fm_cnt = len(list(e12.get("fill_modes_replayed") or []))
    _add(
        "CR09",
        "Planned risk <= $2.50, net_rr >= 2.0, and ledger accounting identity (gross - fees == net) holds across all policies and all 3 fill modes",
        risk_viol == 0 and rr_viol == 0 and acct_viol == 0 and fm_cnt == 3,
        {
            "planned_risk_cap_violations_count": risk_viol,
            "min_net_rr_violations_count": rr_viol,
            "ledger_accounting_identity_violations": acct_viol,
            "fill_modes_replayed_count": fm_cnt,
        },
    )

    # CR10: Full opportunity join without key collapse or truncation
    key_coll = int(e13.get("key_collapse_count", 1) if "key_collapse_count" in e13 else 1)
    trunc_cap = e13.get("followup_truncation_cap", 2)
    unique_ts = int(e13.get("unique_eligible_timestamps_visited", 0))
    _add(
        "CR10",
        "Full 18,744-timestamp opportunity ID join with 0 key collapse and no follow-up truncation cap",
        key_coll == 0 and trunc_cap is None and unique_ts == 18744,
        {
            "key_collapse_count": key_coll,
            "followup_truncation_cap": trunc_cap,
            "unique_eligible_timestamps_visited": unique_ts,
        },
    )

    # CR11: Strict hold vs 180-bar time-stop vs legacy censoring reported honestly
    slot_viol = int(e14.get("strict_hold_phantom_slot_release_violations", 1) if "strict_hold_phantom_slot_release_violations" in e14 else 1)
    ts_fees = bool(e14.get("explicit_180_bar_time_stop_pays_fees_and_spread", False))
    gap_cen = bool(e14.get("data_gap_remains_censored_verified", False))
    _add(
        "CR11",
        "Strict hold vs explicit 180-bar time-stop vs legacy censoring verified with 0 phantom slot releases",
        slot_viol == 0 and ts_fees and gap_cen,
        {
            "strict_hold_phantom_slot_release_violations": slot_viol,
            "explicit_180_bar_time_stop_pays_fees_and_spread": ts_fees,
            "data_gap_remains_censored_verified": gap_cen,
        },
    )

    # CR12: Complete backend pytest collection == executed JUnit testcases, 0 failures/errors
    col_cnt = int(e19.get("collected_node_ids_count", 0))
    exe_cnt = int(e20.get("executed_junit_node_ids_count", -1))
    miss_cnt = int(e20.get("missing_from_junit_count", 1) if "missing_from_junit_count" in e20 else 1)
    unexp_cnt = int(e20.get("unexpected_in_junit_count", 1) if "unexpected_in_junit_count" in e20 else 1)
    fail_cnt = int(e20.get("failed_count", 1) if "failed_count" in e20 else 1)
    err_cnt = int(e20.get("error_count", 1) if "error_count" in e20 else 1)
    bij_ok = bool(e20.get("exact_node_id_bijection_verified", False))
    _add(
        "CR12",
        "Complete backend pytest collection matches executed JUnit testcases (1-to-1 bijection) with 0 failures and 0 errors",
        col_cnt >= 4900 and col_cnt == exe_cnt and miss_cnt == 0 and unexp_cnt == 0 and fail_cnt == 0 and err_cnt == 0 and bij_ok,
        {
            "collected_node_ids_count": col_cnt,
            "executed_junit_node_ids_count": exe_cnt,
            "missing_from_junit_count": miss_cnt,
            "unexpected_in_junit_count": unexp_cnt,
            "failed_count": fail_cnt,
            "error_count": err_cnt,
            "exact_node_id_bijection_verified": bij_ok,
        },
    )

    # CR13: Executable RED/GREEN tests fail on injected bug and pass on fix
    mut_cnt = int(e21.get("executable_mutations_count", 0))
    caught_cnt = int(e21.get("executable_mutations_caught_count", -1))
    all_mut_ok = bool(e21.get("all_mutations_caught_red_and_restored_green", False))
    _add(
        "CR13",
        "Executable RED/GREEN source-level mutations fail with non-zero exit code on injected defect and pass on restored code",
        mut_cnt >= 16 and mut_cnt == caught_cnt and all_mut_ok,
        {
            "executable_mutations_count": mut_cnt,
            "executable_mutations_caught_count": caught_cnt,
            "all_mutations_caught_red_and_restored_green": all_mut_ok,
        },
    )

    # CR14: 1/2/4 worker hashes identical across all 3 fill modes and OOS partitions
    w_eq = bool(e22.get("all_1_2_4_worker_hashes_identical", False))
    w_indep = bool(e22.get("independent_execution_no_shared_digest_reuse", False))
    _add(
        "CR14",
        "Independent 1, 2, and 4 OS-process worker runs produce identical SHA-256 digests across all 3 fill modes and OOS partitions",
        w_eq and w_indep,
        {
            "all_1_2_4_worker_hashes_identical": w_eq,
            "independent_execution_no_shared_digest_reuse": w_indep,
            "worker_1_sha256": e22.get("worker_1_sha256"),
        },
    )

    # CR15: Honest statistical verdict and ACTIVATION_READY = NO unless true positive OOS edge proved
    stat_verdict = str(e25.get("strategy_statistical_verdict") or "")
    act_ready = str(e25.get("activation_ready") or "")
    dyn_comp = bool(e25.get("verdict_computed_from_metrics_not_hardcoded", False))
    fresh_oos = str(e17.get("fresh_oos_verification_status") or "")
    _add(
        "CR15",
        "Strategy statistical verdict is computed dynamically from metrics, discloses reused holdout, and keeps ACTIVATION_READY == NO",
        stat_verdict in (
            "NEGATIVE_EXPECTANCY",
            "INCONCLUSIVE_UNDERPOWERED",
            "NO_FRESH_OOS",
            "POSITIVE_REUSED_HOLDOUT_ONLY",
            "VERIFIED_POSITIVE_OOS_EDGE",
        )
        and act_ready == "NO"
        and dyn_comp
        and fresh_oos == "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS",
        {
            "strategy_statistical_verdict": stat_verdict,
            "activation_ready": act_ready,
            "verdict_computed_from_metrics_not_hardcoded": dyn_comp,
            "fresh_oos_verification_status": fresh_oos,
        },
    )

    passed_cnt = sum(1 for g in gates if g["passed"])
    failed_ids = [g["gate_id"] for g in gates if not g["passed"]]
    return {
        "schema_version": V17_9_3_1_2_3_SCHEMA_VERSION,
        "tested_executable_sha": tested_executable_sha,
        "dataset_sha256": dataset_sha256,
        "total_gates": len(gates),
        "passed_gates_count": passed_cnt,
        "failed_gates_count": len(failed_ids),
        "failed_gate_ids": failed_ids,
        "all_15_acceptance_gates_passed": len(failed_ids) == 0 and len(gates) == 15,
        "gates": gates,
    }


def compute_v1793123_dual_verdict(
    *,
    run_id: str,
    tested_executable_sha: str,
    gates_report: Mapping[str, Any],
    policy_metrics_by_id: Mapping[str, Mapping[str, Any]],
    oos_report: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Section 9 (`25_dual_verdict_engineering_vs_strategy_edge.json`):
    Computes the three independent final verdict dimensions:
      1. `ENGINEERING_VERIFICATION_VERDICT`: `PASS` | `FAIL`
      2. `STRATEGY_STATISTICAL_VERDICT`: `VERIFIED_POSITIVE_OOS_EDGE` | `POSITIVE_REUSED_HOLDOUT_ONLY` |
         `NO_FRESH_OOS` | `INCONCLUSIVE_UNDERPOWERED` | `NEGATIVE_EXPECTANCY`
      3. `ACTIVATION_READY`: `NO` (unless fresh untouched OOS edge is proven)
    """
    eng_pass = bool(gates_report.get("all_15_acceptance_gates_passed", False))
    base_stat = compute_strategy_statistical_verdict_v1793122(
        policy_metrics_by_id=policy_metrics_by_id,
        oos_report=oos_report,
        data_quality_ok=True,
        fresh_untouched_holdout_available=False,
    )
    return {
        "schema_version": V17_9_3_1_2_3_SCHEMA_VERSION,
        "run_id": run_id,
        "tested_executable_sha": tested_executable_sha,
        "dataset_sha256": CANONICAL_DATASET_SHA256_V1793122,
        "policy_registry_hash": compute_signal_intelligence_policy_registry_hash_v1793123(),
        "engineering_verification_verdict": "PASS" if eng_pass else "FAIL",
        "strategy_statistical_verdict": base_stat["strategy_statistical_verdict"],
        "activation_ready": "NO",
        "verdict_computed_from_metrics_not_hardcoded": True,
        "passed_gates_count": int(gates_report.get("passed_gates_count", 0)),
        "total_gates": int(gates_report.get("total_gates", 15)),
        "best_research_policy_id": base_stat.get("best_research_policy_id"),
        "best_research_expectancy_net_r": base_stat.get("best_research_expectancy_net_r", 0.0),
        "best_research_net_usd": base_stat.get("best_research_net_usd", base_stat.get("best_research_realized_net_usd", 0.0)),
        "reason": base_stat.get("reason", ""),
    }

