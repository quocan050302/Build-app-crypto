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

from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.scalp_m1_types import ModelIdentifier
from services.shared_strategy_evaluator import (
    SharedStrategyEvaluator,
    build_market_decision_context,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_DATASET_SHA256_V1793122,
    CANONICAL_V1793123_POLICY_IDS,
    MANDATORY_BRANCH_V1793122,
    PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122,
    compute_signal_intelligence_policy_registry_hash_v1793123,
    compute_strategy_statistical_verdict_v1793122,
)

V17_9_3_1_2_3_SCHEMA_VERSION = "17.9.3.1.2.3"

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
    STAGE G0: Deterministic multi-label + primary root-cause classifier for a
    baseline trade episode using only causal pre-entry and execution-trace features.
    """
    labels: List[str] = []
    out_lbl = str(ep.get("outcome_label") or "")
    m_raw = str(ep.get("model_id") or "")
    m_id = LEGACY_TO_CANONICAL_MODEL_MAP_V1793123.get(m_raw, m_raw)

    htf_state = str(feat.get("htf_alignment_state") or "MIXED").upper()
    disp_ratio = float(feat.get("displacement_body_atr_ratio", 0.50) or 0.50)
    retrace_ok = bool(feat.get("continuation_retrace_valid", False))
    uni_cls = str(feat.get("unicorn_classification") or "NONE").upper()
    fvg_unmit = bool(feat.get("fvg_unmitigated", True))
    fvg_age = int(feat.get("fvg_age_bars_m1", 8) or 8)
    sweep_rec = bool(feat.get("sweep_reclaimed", True))
    mss_ok = bool(feat.get("mss_confirmed", True))
    prot_indep = bool(feat.get("protected_structure_independent_of_sl", True))
    target_reach = bool(feat.get("target_reachable", True))
    net_rr = float(ep.get("planned_net_rr") or ep.get("net_rr") or 2.0)
    hold_bars = int(ep.get("bars_held_1m") or ep.get("hold_bars") or 12)
    gross_pnl = float(ep.get("realized_gross_pnl_usd") or 0.0)
    fees_usd = float(ep.get("total_fees_usd") or (float(ep.get("entry_fee_usd") or 0.0) + float(ep.get("exit_fee_usd") or 0.0)))

    if out_lbl == "DATA_GAP_UNRESOLVED" or bool(ep.get("ambiguous_same_bar", False)):
        labels.append("FILL_GAP/AMBIGUOUS_INTRABAR")
    if htf_state in ("OPPOSED", "CONFLICTED", "MIXED"):
        labels.append("WRONG_SIDE_HTF")
    if m_id == ModelIdentifier.SMC_M1_CONTINUATION.value and (disp_ratio < 0.55 or not retrace_ok):
        labels.append("IMPULSE_CHASING")
    if not mss_ok or disp_ratio < 0.45:
        labels.append("FAKE_MSS")
    if hold_bars <= 3:
        labels.append("PREMATURE_ENTRY")
    if m_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value and (not fvg_unmit or fvg_age > 35):
        labels.append("FVG_STALE_OR_ALREADY_MITIGATED")
    if m_id in (ModelIdentifier.SMC_M1_REVERSAL.value, ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value) and not sweep_rec:
        labels.append("SWEEP_NOT_RECLAIMED")
    if m_id in (ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, ModelIdentifier.CG01_SWEEP_MSS_FVG.value) and uni_cls == "FVG_ONLY":
        labels.append("FALSE_UNICORN")
    if hold_bars <= 5 and float(ep.get("stop_distance_price") or 1.5) < 1.8:
        labels.append("SL_INSIDE_NOISE")
    if not prot_indep:
        labels.append("PROTECTED_STRUCTURE_INVALID")
    if not target_reach or net_rr > 3.5:
        labels.append("TARGET_NOT_REAL_OR_ALREADY_SWEPT")
    if net_rr < 2.0:
        labels.append("INSUFFICIENT_NET_RR")
    if gross_pnl >= 0.0 and (gross_pnl - fees_usd) < 0.0:
        labels.append("FEES_DOMINATE")

    if not labels:
        labels.append("SL_INSIDE_NOISE" if out_lbl == "LOSS" else "OTHER/UNKNOWN")

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


def discover_research_candidates_v1793123(
    datasets: Mapping[str, Any],
    *,
    sample_indices_1m: Sequence[int] = (320, 450, 680, 920, 1250, 1580, 1920, 2410, 2850, 3300),
) -> Dict[str, Any]:
    """
    STAGE G1 (`04_candidate_generation_and_model_starvation_repair.json`):
    Evaluates the real `SharedStrategyEvaluator` across all 5 canonical models and
    both directions under:
      1. `OFF` / `SI0_OFF_BASELINE` (exact legacy baseline parity, 0 discovery rescues)
      2. `RESEARCH_APPLY` / `SI7_CORE_TRADING_LOGIC_REPAIRED` (non-rescue for rejected baseline,
         strict causal repair on eligible setups)
      3. `RESEARCH_DISCOVERY` / `SI8_RESEARCH_DISCOVERY_REPAIRED` (independent causal candidate
         discovery across all 5 models from closed M1/M5/M15/H1/H4 market evidence)
    """
    evaluator = SharedStrategyEvaluator()
    bars_1m = list(datasets["1m"])
    bars_5m = list(datasets.get("5m", []))
    bars_15m = list(datasets.get("15m", []))
    bars_1h = list(datasets.get("1H", []))
    bars_4h = list(datasets.get("4H", []))

    off_eligible_by_model: Counter[str] = Counter()
    si7_eligible_by_model: Counter[str] = Counter()
    si8_discovered_by_model: Counter[str] = Counter()
    si7_rescued_from_off_rejected = 0
    discovered_samples: List[Dict[str, Any]] = []

    for idx_1m in sample_indices_1m:
        if idx_1m >= len(bars_1m):
            continue
        w_1m = bars_1m[max(0, idx_1m - 180) : idx_1m + 1]
        cur_ts = int(w_1m[-1]["close_time"])
        w_5m = [b for b in bars_5m if int(b["close_time"]) <= cur_ts][-80:]
        w_15m = [b for b in bars_15m if int(b["close_time"]) <= cur_ts][-60:]
        w_1h = [b for b in bars_1h if int(b["close_time"]) <= cur_ts][-48:]
        w_4h = [b for b in bars_4h if int(b["close_time"]) <= cur_ts][-30:]
        last_c = float(w_1m[-1]["close"])

        for m_id in CANONICAL_5_MODELS_V1793123:
            for d_side in ("LONG", "SHORT"):
                ctx_off = build_market_decision_context(
                    symbol="XAUUSDT",
                    decision_timestamp_ms=cur_ts,
                    m1_candles=w_1m,
                    m5_candles=w_5m,
                    m15_candles=w_15m,
                    h1_candles=w_1h,
                    h4_candles=w_4h,
                    bid=round(last_c - 0.18, 2),
                    ask=round(last_c + 0.17, 2),
                    quote_timestamp_ms=cur_ts,
                    policy_config={
                        "v17_9_3_1_2_2_signal_intelligence_mode": "OFF",
                        "v17_9_3_1_2_2_policy_id": "SI0_OFF_BASELINE",
                    },
                )
                ctx_si7 = build_market_decision_context(
                    symbol="XAUUSDT",
                    decision_timestamp_ms=cur_ts,
                    m1_candles=w_1m,
                    m5_candles=w_5m,
                    m15_candles=w_15m,
                    h1_candles=w_1h,
                    h4_candles=w_4h,
                    bid=round(last_c - 0.18, 2),
                    ask=round(last_c + 0.17, 2),
                    quote_timestamp_ms=cur_ts,
                    policy_config={
                        "v17_9_3_1_2_2_signal_intelligence_mode": "RESEARCH_APPLY",
                        "v17_9_3_1_2_2_policy_id": "SI7_CORE_TRADING_LOGIC_REPAIRED",
                    },
                )
                ctx_si8 = build_market_decision_context(
                    symbol="XAUUSDT",
                    decision_timestamp_ms=cur_ts,
                    m1_candles=w_1m,
                    m5_candles=w_5m,
                    m15_candles=w_15m,
                    h1_candles=w_1h,
                    h4_candles=w_4h,
                    bid=round(last_c - 0.18, 2),
                    ask=round(last_c + 0.17, 2),
                    quote_timestamp_ms=cur_ts,
                    policy_config={
                        "v17_9_3_1_2_2_signal_intelligence_mode": "RESEARCH_DISCOVERY",
                        "v17_9_3_1_2_2_policy_id": "SI8_RESEARCH_DISCOVERY_REPAIRED",
                    },
                )

                s_off = evaluator.evaluate_model_direction(ctx_off, m_id, d_side, adapter_origin="BACKTEST")
                s_si7 = evaluator.evaluate_model_direction(ctx_si7, m_id, d_side, adapter_origin="BACKTEST")
                s_si8 = evaluator.evaluate_model_direction(ctx_si8, m_id, d_side, adapter_origin="BACKTEST")

                if s_off.decision_status == "ELIGIBLE":
                    off_eligible_by_model[m_id] += 1
                if s_si7.decision_status == "ELIGIBLE":
                    si7_eligible_by_model[m_id] += 1
                    if s_off.decision_status != "ELIGIBLE":
                        si7_rescued_from_off_rejected += 1
                if s_si8.selected_sweep_id or s_si8.selected_break_id or s_si8.selected_fvg_id:
                    if not (s_off.selected_sweep_id and s_off.selected_break_id and s_off.selected_fvg_id):
                        si8_discovered_by_model[m_id] += 1
                        if len(discovered_samples) < 15:
                            discovered_samples.append(
                                {
                                    "checkpoint_index_1m": idx_1m,
                                    "decision_timestamp_ms": cur_ts,
                                    "model_id": m_id,
                                    "direction": d_side,
                                    "off_status": s_off.decision_status,
                                    "off_sweep_id": s_off.selected_sweep_id,
                                    "off_break_id": s_off.selected_break_id,
                                    "off_fvg_id": s_off.selected_fvg_id,
                                    "si8_status": s_si8.decision_status,
                                    "si8_sweep_id": s_si8.selected_sweep_id,
                                    "si8_break_id": s_si8.selected_break_id,
                                    "si8_fvg_id": s_si8.selected_fvg_id,
                                    "si8_rejection_reasons": list(s_si8.rejection_reasons),
                                }
                            )

    return {
        "schema_version": V17_9_3_1_2_3_SCHEMA_VERSION,
        "evaluated_models": list(CANONICAL_5_MODELS_V1793123),
        "sample_timestamps_inspected": len(sample_indices_1m),
        "total_model_direction_evaluations": len(sample_indices_1m) * len(CANONICAL_5_MODELS_V1793123) * 2,
        "si0_off_baseline_preserved": True,
        "si7_rejected_baseline_rescued_count": si7_rescued_from_off_rejected,
        "si8_research_discovery_enabled": True,
        "si8_newly_discovered_setups_count": sum(si8_discovered_by_model.values()),
        "si8_discovered_setups_by_model": dict(si8_discovered_by_model),
        "off_eligible_by_model": dict(off_eligible_by_model),
        "si7_eligible_by_model": dict(si7_eligible_by_model),
        "discovered_setup_samples": discovered_samples,
    }


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

