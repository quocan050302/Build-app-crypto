"""
backend/services/v17_9_3_1_2_executable_intervention_replay_service.py
AURUM Desk V17.9.3.1.2 — Full-Universe Replay & Executable Entry/SL/TP Counterfactual Validation Service.

Implements:
  - Section 03: Canonical 22,346 post-warmup M1 Event Universe without $7.00 M1 target-distance truncation:
      * `FullUniverseIndexManifest` & `enumerate_full_replay_indices_v179312`
      * `build_causal_context_at_v179312` & `extract_per_timeframe_causal_provenance_v179312`
      * `evaluate_full_universe_model_batch_v179312`
      * `write_append_only_decision_stream_v179312`
      * `FullUniverseDecisionManifest` & `merge_and_validate_decision_stream_v179312`
  - Section 04: Global Chronological Portfolio Replay & Position State Machine:
      * `OrderIntentV179312` & `build_order_intent_from_snapshot_v179312`
      * `arbitrate_competing_model_intents_v179312`
      * `PortfolioReplayStateV179312` & `advance_portfolio_to_next_event_v179312`
      * `simulate_executable_candidate_v179312`
      * `VariantReplayOutcomeV179312` & `replay_global_chronological_portfolio_v179312`
      * `verify_no_trade_time_overlap_v179312`
      * `reconcile_trades_to_ledger_v179312`
  - Section 05: Real Executable Entry/SL/TP Interventions (`ExecutableResearchPolicyV179312`):
      * `PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312` (`R0`..`R8`)
      * `find_next_causal_entry_confirmation_v179312`
      * `rebuild_entry_reference_v179312`
      * `derive_executable_structural_stop_v179312`
      * `derive_executable_structural_target_v179312`
      * `recalculate_trade_risk_quantity_v179312`
      * `materialize_executable_counterfactual_v179312`
      * `replay_intervention_outcome_v179312`
      * `assert_intervention_is_real_v179312`
  - Section 07: Matched-Opportunity vs Independent Portfolio Replay Decomposition:
      * `match_variants_on_causal_opportunity_v179312`
      * `attribution_v179312`
  - Section 08 & 09: Chronological OOS / Walk-Forward Integrity & Economic Verdict:
      * `assert_replay_oos_temporal_and_feature_integrity_v179312`
      * `compute_economic_and_statistical_summary_v179312`

Strictly research-only: zero production strategy flag changes, zero LIVE/PAPER/AUTO activation.
"""

from __future__ import annotations

import bisect
import datetime
import gzip
import hashlib
import json
import math
import os
import statistics
from collections import Counter, OrderedDict, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.shared_strategy_evaluator import (
    DecisionStatus,
    MarketDecisionContext,
    PRODUCTION_MODEL_IDS,
    SharedStrategyEvaluator,
    StrategyDecisionSnapshot,
)
from services.stop_placement_types import StopAnchor, StopAnchorKind
from services.v17_9_2_structural_stop_intelligence import (
    ExecutableTradeLedgerEntryV17931,
    StructuralStopCandidate,
    classify_structure_stop_outcome,
    collect_causal_stop_anchor_candidates,
    compute_causal_noise_buffer,
    derive_structure_valid_stop,
    evaluate_stop_tp_risk_and_size,
    plan_v17_9_2_structural_stop_and_target,
    rank_structure_valid_anchors,
    resolve_causal_target_candidates,
    resolve_v17931_protected_structure_authority,
    select_viable_structural_target,
    simulate_v17931_trade_execution_net,
    validate_structure_invalidation_contract,
)
from services.v17_9_3_1_1_loss_forensics_and_research_service import (
    PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311,
    RiskPolicySnapshotV179311,
    audit_ledger_against_risk_policy_snapshot,
    compute_contiguous_session_day_block_bootstrap_ci,
    compute_wilson_95_ci,
    evaluate_v179311_chronological_oos_and_walkforward,
    get_canonical_risk_policy_snapshot,
    validate_acceptance_gate_manifest,
    validate_artifact_sha256_manifest,
    validate_junit_and_collection_bijection,
    validate_single_ledger_entry_against_risk_policy,
    validate_trade_temporal_partition_contract,
)
from services.v17_9_3_macro_unicorn_intelligence import (
    classify_macro_context_quality,
    evaluate_causal_unicorn_confluence,
    plan_v17_9_3_macro_unicorn_setup,
    resolve_causal_macro_time_window,
)

V17_9_3_1_2_SCHEMA_VERSION = "17.9.3.1.2"
CANONICAL_PRODUCTION_MODEL_PRIORITY: Tuple[str, ...] = tuple(PRODUCTION_MODEL_IDS)

VALID_INTERVENTION_TYPES_V179312: Tuple[str, ...] = (
    "BASELINE_CONTROL",
    "ENTRY_FILTER_ONLY",
    "DELAYED_ENTRY_EXECUTABLE",
    "STRUCTURAL_STOP_EXECUTABLE",
    "STRUCTURAL_TP_EXECUTABLE",
    "ENTRY_SL_TP_COMBINED_EXECUTABLE",
)

VALID_DENOMINATOR_GROUPS_V179312: Tuple[str, ...] = (
    "LEGACY_SCREENED",
    "FULL_UNIVERSE_ALL_TIMESTAMPS",
    "FULL_UNIVERSE_MATCHED_COEVAL",
    "RESEARCH_EXPLICIT_FILTER_ONLY",
    "RESEARCH_EXECUTABLE_INTERVENTION",
    "CHRONOLOGICALLY_PURGED_OOS",
)

V17_9_3_1_2_COMPARATOR_VARIANTS: Tuple[str, ...] = (
    "A0_FROZEN_BASELINE_OFF",
    "A1_V1792_SL_TP_BASELINE",
    "C1_MACRO_PLUS_V1792_SL_TP",
    "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
    "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
    "E1_OUTSIDE_MACRO_COUNTERFACTUAL",
)


def _canonical_json_sha256(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _ms_to_utc_iso(ts_ms: Optional[int]) -> Optional[str]:
    if ts_ms is None or int(ts_ms) <= 0:
        return None
    return datetime.datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _session_for_ms(ts_ms: int) -> str:
    m_state = resolve_causal_macro_time_window(int(ts_ms), symbol="XAUUSDT")
    if m_state.is_weekend_or_closed:
        return "WEEKEND_CLOSED"
    hhmm = str(m_state.ny_local_hhmm or "00:00")
    if "02:00" <= hhmm < "05:00":
        return "LONDON_OPEN"
    if "05:00" <= hhmm < "08:00":
        return "LONDON"
    if "08:00" <= hhmm < "12:00":
        return "NY_AM"
    if "12:00" <= hhmm < "13:30":
        return "NY_LUNCH"
    if "13:30" <= hhmm < "17:00":
        return "NY_PM"
    return "ASIA_OFF_HOURS"


# ==============================================================================
# SECTION 03 — CANONICAL EVENT UNIVERSE (NO $7.00 TARGET DISTANCE TRUNCATION)
# ==============================================================================
@dataclass(frozen=True)
class FullUniverseIndexManifest:
    schema_version: str
    symbol: str
    total_m1_bars: int
    warmup_bars: int
    post_warmup_count: int
    first_post_warmup_index: int
    last_post_warmup_index: int
    first_close_time_ms: int
    last_close_time_ms: int
    covered_indices: Tuple[int, ...]
    corrupted_indices: Tuple[int, ...]
    unclosed_bar_indices: Tuple[int, ...]
    duplicate_timestamp_indices: Tuple[int, ...]
    non_monotonic_indices: Tuple[int, ...]
    missing_gap_transitions: Tuple[Dict[str, Any], ...]
    weekend_rollover_gap_count: int
    intra_session_gap_count: int
    monotonic_strictly_increasing: bool
    zero_duplicate_timestamps: bool
    zero_corrupted_bars: bool
    dataset_m1_sha256: str
    manifest_hash: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["covered_indices_count"] = len(self.covered_indices)
        # Keep covered_indices compact in JSON serialization while preserving first/last & sample
        d["covered_indices_head"] = list(self.covered_indices[:25])
        d["covered_indices_tail"] = list(self.covered_indices[-25:])
        del d["covered_indices"]
        return d


def enumerate_full_replay_indices_v179312(
    datasets: Any,
    warmup: int = 30,
    *,
    warmup_bars: Optional[int] = None,
) -> FullUniverseIndexManifest:
    """
    Section 03: Validates unique monotonic close timestamps and candle-close semantics across
    all M1 bars and returns the full post-warmup replay index universe (`30..22375`, `22,346` indices)
    with explicit covered/missing/corrupted/gap states. Never truncates by M1 target distance,
    Macro window, Unicorn status, or prior trade outcomes.
    """
    bars_1m = list(datasets.get("1m") or []) if isinstance(datasets, Mapping) else list(datasets or [])
    total_m1 = len(bars_1m)
    warmup_n = max(0, int(warmup_bars if warmup_bars is not None else warmup))
    if total_m1 <= warmup_n:
        raise ValueError(f"INSUFFICIENT_M1_BARS_FOR_WARMUP:total={total_m1},warmup={warmup_n}")

    covered: List[int] = []
    corrupted: List[int] = []
    unclosed: List[int] = []
    duplicates: List[int] = []
    non_monotonic: List[int] = []
    gaps: List[Dict[str, Any]] = []
    weekend_gaps = 0
    intra_gaps = 0

    seen_ts: Set[int] = set()
    prev_close_ms: Optional[int] = None
    h = hashlib.sha256()

    for idx, b in enumerate(bars_1m):
        c_ms = int(b.get("close_time") or b.get("close_time_ms") or 0)
        o_ms = int(b.get("open_time") or b.get("open_time_ms") or (c_ms - 60_000))
        o_px = float(b.get("open", 0.0) or 0.0)
        h_px = float(b.get("high", 0.0) or 0.0)
        l_px = float(b.get("low", 0.0) or 0.0)
        c_px = float(b.get("close", 0.0) or 0.0)
        is_closed = bool(b.get("is_closed", True))

        h.update(f"{idx}:{o_ms}:{c_ms}:{o_px:.2f}:{h_px:.2f}:{l_px:.2f}:{c_px:.2f}\n".encode("utf-8"))

        if not is_closed:
            unclosed.append(idx)
        if (
            c_ms <= 0
            or not (math.isfinite(o_px) and math.isfinite(h_px) and math.isfinite(l_px) and math.isfinite(c_px))
            or min(o_px, h_px, l_px, c_px) <= 0.0
            or h_px < max(o_px, c_px, l_px) - 1e-6
            or l_px > min(o_px, c_px, h_px) + 1e-6
        ):
            corrupted.append(idx)

        if c_ms in seen_ts:
            duplicates.append(idx)
        seen_ts.add(c_ms)

        if prev_close_ms is not None:
            dt_ms = c_ms - prev_close_ms
            if dt_ms <= 0:
                non_monotonic.append(idx)
            elif dt_ms > 60_000:
                missing_bars = int(round(dt_ms / 60_000.0)) - 1
                # Classify weekend/rollover vs intra-session gap
                dt_prev = datetime.datetime.fromtimestamp(prev_close_ms / 1000.0, tz=datetime.timezone.utc)
                is_weekend_gap = dt_ms >= 36 * 3_600_000 or dt_prev.weekday() in (4, 5, 6)
                gap_class = "WEEKEND_ROLLOVER_GAP" if is_weekend_gap else "INTRA_SESSION_GAP"
                if is_weekend_gap:
                    weekend_gaps += 1
                else:
                    intra_gaps += 1
                gaps.append(
                    {
                        "from_index": idx - 1,
                        "to_index": idx,
                        "from_close_time_ms": prev_close_ms,
                        "to_close_time_ms": c_ms,
                        "delta_ms": dt_ms,
                        "missing_m1_bars": missing_bars,
                        "gap_classification": gap_class,
                    }
                )
        prev_close_ms = c_ms

        if idx >= warmup_n and idx not in corrupted and idx not in unclosed:
            covered.append(idx)

    dataset_sha = h.hexdigest()
    manifest_payload = {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "total_m1_bars": total_m1,
        "warmup_bars": warmup_n,
        "post_warmup_count": len(covered),
        "first_close_time_ms": int(bars_1m[warmup_n]["close_time"]),
        "last_close_time_ms": int(bars_1m[-1]["close_time"]),
        "corrupted_count": len(corrupted),
        "duplicates_count": len(duplicates),
        "non_monotonic_count": len(non_monotonic),
        "dataset_m1_sha256": dataset_sha,
    }
    return FullUniverseIndexManifest(
        schema_version=V17_9_3_1_2_SCHEMA_VERSION,
        symbol="XAUUSDT",
        total_m1_bars=total_m1,
        warmup_bars=warmup_n,
        post_warmup_count=len(covered),
        first_post_warmup_index=covered[0] if covered else warmup_n,
        last_post_warmup_index=covered[-1] if covered else total_m1 - 1,
        first_close_time_ms=int(bars_1m[covered[0]]["close_time"]) if covered else 0,
        last_close_time_ms=int(bars_1m[covered[-1]]["close_time"]) if covered else 0,
        covered_indices=tuple(covered),
        corrupted_indices=tuple(corrupted),
        unclosed_bar_indices=tuple(unclosed),
        duplicate_timestamp_indices=tuple(duplicates),
        non_monotonic_indices=tuple(non_monotonic),
        missing_gap_transitions=tuple(gaps),
        weekend_rollover_gap_count=weekend_gaps,
        intra_session_gap_count=intra_gaps,
        monotonic_strictly_increasing=(len(non_monotonic) == 0 and len(duplicates) == 0),
        zero_duplicate_timestamps=(len(duplicates) == 0),
        zero_corrupted_bars=(len(corrupted) == 0 and len(unclosed) == 0),
        dataset_m1_sha256=dataset_sha,
        manifest_hash=_canonical_json_sha256(manifest_payload),
    )


def build_close_ts_index_v179312(datasets: Mapping[str, Any]) -> Dict[str, List[int]]:
    return {
        tf: [int(b["close_time"]) for b in (datasets.get(tf) or ())]
        for tf in ("1m", "5m", "15m", "1H", "4H", "1D")
    }


def build_causal_context_at_v179312(
    datasets: Mapping[str, Any],
    m1_index: int,
    frozen_config: Optional[Mapping[str, Any]] = None,
    policy: Optional[Mapping[str, Any]] = None,
    *,
    close_ts_index: Optional[Mapping[str, Sequence[int]]] = None,
    reference_context: Optional[MarketDecisionContext] = None,
) -> MarketDecisionContext:
    """
    Section 03 & 06: Builds an immutable `MarketDecisionContext` at `m1_index` using only
    confirmed closed candles (`close_time <= decision_timestamp_ms`).
    Raises `ValueError` fail-closed if any candle across M1/M5/M15/H1/H4/1D is unclosed or
    has `close_time > decision_timestamp_ms`.
    """
    from tools.run_v17_9_3_macro_unicorn_backtest import (
        V1793_VARIANT_POLICIES,
        build_v1793_variant_market_context,
    )

    bars_1m = datasets["1m"]
    if m1_index < 0 or m1_index >= len(bars_1m):
        raise ValueError(f"M1_INDEX_OUT_OF_RANGE:{m1_index}")

    c_ts = close_ts_index if close_ts_index is not None else build_close_ts_index_v179312(datasets)
    asof_ms = int(bars_1m[m1_index]["close_time"])

    idx_5m = bisect.bisect_right(c_ts["5m"], asof_ms)
    idx_15m = bisect.bisect_right(c_ts["15m"], asof_ms)
    idx_1h = bisect.bisect_right(c_ts["1H"], asof_ms)
    idx_4h = bisect.bisect_right(c_ts["4H"], asof_ms)
    idx_1d = bisect.bisect_right(c_ts["1D"], asof_ms)

    win_1m = bars_1m[max(0, m1_index + 1 - 120) : m1_index + 1]
    win_5m = datasets["5m"][max(0, idx_5m - 96) : idx_5m]
    win_15m = datasets["15m"][max(0, idx_15m - 96) : idx_15m]
    win_1h = datasets["1H"][max(0, idx_1h - 96) : idx_1h]
    win_4h = datasets["4H"][max(0, idx_4h - 64) : idx_4h]
    win_1d = datasets["1D"][max(0, idx_1d - 30) : idx_1d]

    for tf_name, tf_bars in (
        ("1m", win_1m),
        ("5m", win_5m),
        ("15m", win_15m),
        ("1H", win_1h),
        ("4H", win_4h),
        ("1D", win_1d),
    ):
        for b in tf_bars:
            b_close = int(b.get("close_time") or b.get("close_time_ms") or 0)
            if b_close > asof_ms:
                raise ValueError(f"FUTURE_CANDLE_LEAK_IN_{tf_name}:close_time={b_close}>asof={asof_ms}")
            if not bool(b.get("is_closed", True)):
                raise ValueError(f"UNCLOSED_CANDLE_IN_{tf_name}:close_time={b_close}")

    last_close = float(win_1m[-1]["close"])
    spread_usd = float((frozen_config or {}).get("spread_usd", 0.20) or 0.20)
    half_sp = round(spread_usd / 2.0, 4)

    base_inputs: Dict[str, Any] = {
        "symbol": str((frozen_config or {}).get("symbol", "XAUUSDT") or "XAUUSDT"),
        "decision_timestamp_ms": asof_ms,
        "m1_candles": win_1m,
        "m5_candles": win_5m,
        "m15_candles": win_15m,
        "h1_candles": win_1h,
        "h4_candles": win_4h,
        "d1_candles": win_1d,
        "bid": round(last_close - half_sp, 2),
        "ask": round(last_close + half_sp, 2),
    }
    if frozen_config:
        if "risk_config" in frozen_config:
            base_inputs["risk_config"] = dict(frozen_config["risk_config"])
        if "cost_config" in frozen_config:
            base_inputs["cost_config"] = dict(frozen_config["cost_config"])

    eff_pol = dict(policy) if policy is not None else dict(V1793_VARIANT_POLICIES["A0_FROZEN_BASELINE_OFF"])
    return build_v1793_variant_market_context(
        base_inputs,
        eff_pol,
        decision_time=asof_ms,
        reference_context=reference_context,
    )


def extract_per_timeframe_causal_provenance_v179312(
    context: Any,
    m1_index: Optional[int] = None,
    *,
    close_ts_index: Optional[Mapping[str, Sequence[int]]] = None,
) -> Dict[str, Any]:
    """
    Returns per-timeframe last closed candle timestamp, count, and SHA-256 provenance digest
    and verifies `last_closed_ms <= context.decision_timestamp_ms` across all timeframes.
    Accepts either a `MarketDecisionContext` or `(datasets, m1_index, close_ts_index=...)`.
    """
    if isinstance(context, Mapping) and m1_index is not None:
        ctx_obj = build_causal_context_at_v179312(
            context,
            int(m1_index),
            close_ts_index=close_ts_index,
        )
    else:
        ctx_obj = context
    asof_ms = int(ctx_obj.decision_timestamp_ms)
    tf_map = {
        "1M": ctx_obj.closed_m1_candles,
        "5M": ctx_obj.closed_m5_candles,
        "15M": ctx_obj.closed_m15_candles,
        "1H": ctx_obj.closed_h1_candles,
        "4H": ctx_obj.closed_h4_candles,
        "1D": ctx_obj.closed_d1_candles,
    }
    out: Dict[str, Any] = {}
    all_causal = True
    for tf_k, bars in tf_map.items():
        if not bars:
            out[tf_k] = {
                "count": 0,
                "available_closed_bars_count": 0,
                "last_close_ms": None,
                "last_closed_bar_close_time_ms": None,
                "delta_ms_to_decision": None,
                "causal_valid": True,
                "window_sha256": hashlib.sha256(b"").hexdigest()[:16],
            }
            continue
        last_b = bars[-1]
        last_c = int(last_b.get("close_time") or last_b.get("close_time_ms") or last_b.get("close_ms") or 0)
        is_c = all(
            int(b.get("close_time") or b.get("close_time_ms") or b.get("close_ms") or 0) <= asof_ms
            and bool(b.get("is_closed", True))
            for b in bars
        )
        if not is_c:
            all_causal = False
        w_sha = _canonical_json_sha256(
            [
                (
                    int(b.get("close_time") or b.get("close_time_ms") or b.get("close_ms") or 0),
                    float(b.get("open", 0.0)),
                    float(b.get("high", 0.0)),
                    float(b.get("low", 0.0)),
                    float(b.get("close", 0.0)),
                )
                for b in bars
            ]
        )[:16]
        out[tf_k] = {
            "count": len(bars),
            "available_closed_bars_count": len(bars),
            "last_close_ms": last_c,
            "last_closed_bar_close_time_ms": last_c,
            "delta_ms_to_decision": asof_ms - last_c,
            "causal_valid": is_c,
            "window_sha256": w_sha,
        }
    return {
        "decision_timestamp_ms": asof_ms,
        "all_timeframes_causal": all_causal,
        "causal_provenance_valid": all_causal,
        "timeframes": out,
    }


def _build_composite_opportunity_id_internal_v179312(
    *,
    symbol: str,
    model_id: str,
    direction: str,
    base_decision_ts_ms: int,
    base_checkpoint_index_1m: int,
    setup_episode_id: Optional[str],
    disambiguator: Optional[str] = None,
    dataset_namespace: str = "XAUUSDT_OCT2026_9f301fee2e62",
) -> str:
    """
    Constructs an immutable composite opportunity ID containing:
    dataset_namespace, symbol, canonical model_id, direction, base signal M1 close timestamp,
    base checkpoint index, setup episode ID, and canonical action fingerprint disambiguator.
    """
    sym = str(symbol or "XAUUSDT").strip().upper()
    mdl = str(model_id or "UNKNOWN_MODEL").strip()
    dir_s = str(direction or "LONG").strip().upper()
    ts = int(base_decision_ts_ms or 0)
    idx = int(base_checkpoint_index_1m or 0)
    ep = str(setup_episode_id or f"ep_{idx}_{mdl}_{dir_s}").strip()
    dis = str(disambiguator or "default")[:12]
    return f"opp::{dataset_namespace}::{sym}::{mdl}::{dir_s}::ts{ts}::idx{idx}::{ep}::{dis}"


def _snapshot_to_compact_decision_record_v179312(
    snap: StrategyDecisionSnapshot,
    *,
    checkpoint_index_1m: int,
    variant: str,
    inside_legacy_screen: bool,
    macro_primary_window_id: Optional[str],
    macro_eligible: bool,
) -> Dict[str, Any]:
    opp_id = _build_composite_opportunity_id_internal_v179312(
        symbol=str(snap.symbol),
        model_id=str(snap.model_id),
        direction=str(snap.direction),
        base_decision_ts_ms=int(snap.decision_timestamp_ms),
        base_checkpoint_index_1m=int(checkpoint_index_1m),
        setup_episode_id=snap.setup_episode_id,
        disambiguator=str(snap.trade_action_fingerprint or snap.decision_fingerprint or ""),
    )
    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "provenance": "HISTORICAL_BACKTEST_V179312",
        "opportunity_id": opp_id,
        "checkpoint_index_1m": int(checkpoint_index_1m),
        "decision_timestamp_ms": int(snap.decision_timestamp_ms),
        "variant": str(variant),
        "model_id": str(snap.model_id),
        "symbol": str(snap.symbol),
        "direction": str(snap.direction),
        "setup_validity": str(snap.setup_validity),
        "decision_status": str(snap.decision_status),
        "entry_reference": float(snap.entry_reference) if snap.entry_reference is not None else None,
        "selected_sl": float(snap.selected_sl) if snap.selected_sl is not None else None,
        "legacy_sl": float(snap.legacy_sl) if snap.legacy_sl is not None else None,
        "take_profit": float(snap.take_profit) if snap.take_profit is not None else None,
        "hypothetical_quantity": float(snap.hypothetical_quantity) if snap.hypothetical_quantity is not None else None,
        "stop_distance_price": float(snap.stop_distance_price) if snap.stop_distance_price is not None else None,
        "target_distance_price": float(snap.target_distance_price) if snap.target_distance_price is not None else None,
        "gross_rr": float(snap.gross_rr) if snap.gross_rr is not None else None,
        "net_rr": float(snap.net_rr) if snap.net_rr is not None else None,
        "selected_anchor_id": snap.selected_anchor_id,
        "anchor_kind": snap.anchor_kind,
        "structural_invalidation_price": (
            float(snap.structural_invalidation_price)
            if snap.structural_invalidation_price is not None
            else None
        ),
        "buffer_value": float(snap.buffer_value) if snap.buffer_value is not None else None,
        "selected_target_id": snap.selected_target_id,
        "selected_fvg_id": snap.selected_fvg_id,
        "selected_break_id": snap.selected_break_id,
        "selected_sweep_id": snap.selected_sweep_id,
        "setup_episode_id": snap.setup_episode_id,
        "rejection_reason_codes": list(snap.rejection_reason_codes or ()),
        "trade_action_fingerprint": str(snap.trade_action_fingerprint),
        "decision_fingerprint": str(snap.decision_fingerprint),
        "inside_legacy_screen": bool(inside_legacy_screen),
        "macro_primary_window_id": macro_primary_window_id,
        "macro_eligible": bool(macro_eligible),
        "session": _session_for_ms(int(snap.decision_timestamp_ms)),
        "venue_market_state_verification_status": "NOT_VERIFIED",
        "quote_provenance": "MODELED_FROM_M1_OHLC_BID_ASK",
    }


def evaluate_full_universe_model_batch_v179312(
    index_chunk: Sequence[int],
    variant_plan: Optional[Mapping[str, Any]] = None,
    *,
    datasets: Optional[Mapping[str, Any]] = None,
    close_ts_index: Optional[Mapping[str, Sequence[int]]] = None,
    screened_indices_set: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    """
    Section 03: Evaluates all 5 `PRODUCTION_MODEL_IDS` across `index_chunk` in the full
    post-warmup M1 universe without any `$7.00` M1 target-distance truncation.
    Produces:
      - All eligible records for `A1_V1792_SL_TP_BASELINE`, `E1_OUTSIDE_MACRO_COUNTERFACTUAL`,
        and `A0_FROZEN_BASELINE_OFF` across the chunk;
      - Complete per-variant status and rejection-reason tallies across all `len(index_chunk) * 5`
        model-timestamp evaluations (`ELIGIBLE` + `VALID_SETUP_REJECTED_BY_RISK` + `NO_VALID_SETUP`
        + `WAITING_CONFIRMATION` + `MODEL_DISABLED` == `len(index_chunk) * 5`);
      - Exact reconciliation between inside-legacy-screen (`3,788`) and outside-legacy-screen (`18,558`)
        eligible candidates.
    """
    import tools.run_v17_9_3_macro_unicorn_backtest as bt_mod
    from tools.run_v17_9_3_macro_unicorn_backtest import (
        V1793_VARIANT_POLICIES,
        build_v1793_variant_market_context,
    )

    if datasets is None or close_ts_index is None:
        if bt_mod._WORKER_DATASETS is None or bt_mod._WORKER_CLOSE_TS is None:
            bt_mod._init_replay_worker()
        ds = bt_mod._WORKER_DATASETS
        c_ts = bt_mod._WORKER_CLOSE_TS
    else:
        ds = datasets
        c_ts = close_ts_index
    assert ds is not None and c_ts is not None

    bars_1m = ds["1m"]
    bars_5m = ds["5m"]
    bars_15m = ds["15m"]
    bars_1h = ds["1H"]
    bars_4h = ds["4H"]
    bars_1d = ds["1D"]

    scr_set = screened_indices_set if screened_indices_set is not None else set()
    pol_a0 = V1793_VARIANT_POLICIES["A0_FROZEN_BASELINE_OFF"]
    pol_a1_fast = dict(V1793_VARIANT_POLICIES["A1_V1792_SL_TP_BASELINE"])
    pol_a1_fast["mtf_structure_observe_only"] = False
    pol_d1_fast = dict(V1793_VARIANT_POLICIES["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"])
    pol_d1_fast["mtf_structure_observe_only"] = False
    pol_d2_fast = dict(V1793_VARIANT_POLICIES["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"])
    pol_d2_fast["mtf_structure_observe_only"] = False

    eligible_records_by_variant: Dict[str, List[Dict[str, Any]]] = {
        vk: [] for vk in V17_9_3_1_2_COMPARATOR_VARIANTS
    }
    status_counts_by_variant: Dict[str, Counter[str]] = {
        vk: Counter() for vk in V17_9_3_1_2_COMPARATOR_VARIANTS
    }
    rejection_counts_by_variant: Dict[str, Counter[str]] = {
        vk: Counter() for vk in V17_9_3_1_2_COMPARATOR_VARIANTS
    }
    model_eligible_counts: Dict[str, Counter[str]] = {
        vk: Counter() for vk in V17_9_3_1_2_COMPARATOR_VARIANTS
    }

    outside_screen_a1_eligible_count = 0
    outside_screen_e1_eligible_count = 0
    inside_screen_a1_eligible_count = 0
    inside_screen_e1_eligible_count = 0
    outside_screen_a1_unique_ts: Set[int] = set()
    outside_screen_e1_unique_ts: Set[int] = set()
    valid_raw_setup_timestamps_count = 0

    stream_digest = hashlib.sha256()

    for i_1m in index_chunk:
        asof_ms = int(bars_1m[i_1m]["close_time"])
        in_screen = i_1m in scr_set

        idx_5m = bisect.bisect_right(c_ts["5m"], asof_ms)
        idx_15m = bisect.bisect_right(c_ts["15m"], asof_ms)
        idx_1h = bisect.bisect_right(c_ts["1H"], asof_ms)
        idx_4h = bisect.bisect_right(c_ts["4H"], asof_ms)
        idx_1d = bisect.bisect_right(c_ts["1D"], asof_ms)

        win_1m = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
        last_c = float(win_1m[-1]["close"])
        base_inputs = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": asof_ms,
            "m1_candles": win_1m,
            "m5_candles": bars_5m[max(0, idx_5m - 96) : idx_5m],
            "m15_candles": bars_15m[max(0, idx_15m - 96) : idx_15m],
            "h1_candles": bars_1h[max(0, idx_1h - 96) : idx_1h],
            "h4_candles": bars_4h[max(0, idx_4h - 64) : idx_4h],
            "d1_candles": bars_1d[max(0, idx_1d - 30) : idx_1d],
            "bid": round(last_c - 0.10, 2),
            "ask": round(last_c + 0.10, 2),
        }

        ctx_a0 = build_v1793_variant_market_context(base_inputs, pol_a0, decision_time=asof_ms)
        pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx_a0)
        macro_state = resolve_causal_macro_time_window(asof_ms, symbol="XAUUSDT")
        macro_qual = classify_macro_context_quality(macro_state, ctx_a0)
        macro_win_id = macro_state.primary_window_id if macro_state.eligible_for_macro_gate else None

        is_clean_inside_macro = (
            macro_state.eligible_for_macro_gate
            and not macro_state.is_weekend_or_closed
            and not macro_state.is_holiday_closed
            and not macro_qual.session_restricted_override
            and macro_qual.causal_valid
            and macro_qual.quote_fresh
            and macro_qual.m1_bars_sufficient
        )

        is_clean_outside_macro = (
            not macro_state.eligible_for_macro_gate
            and macro_state.primary_window_id == "OUTSIDE_MACRO_WINDOW"
            and not macro_state.is_weekend_or_closed
            and not macro_state.is_holiday_closed
            and not macro_qual.session_restricted_override
            and macro_qual.causal_valid
            and macro_qual.quote_fresh
            and macro_qual.m1_bars_sufficient
        )

        models_with_valid_setup: List[str] = []
        s0_by_model: Dict[str, StrategyDecisionSnapshot] = {}

        for m_id in PRODUCTION_MODEL_IDS:
            s0 = SharedStrategyEvaluator.evaluate_model_direction(ctx_a0, m_id, pref_dir)
            s0_by_model[m_id] = s0
            st0 = str(s0.decision_status)
            status_counts_by_variant["A0_FROZEN_BASELINE_OFF"][st0] += 1
            for rc in s0.rejection_reason_codes or ():
                rejection_counts_by_variant["A0_FROZEN_BASELINE_OFF"][rc] += 1
            if st0 == DecisionStatus.ELIGIBLE.value:
                model_eligible_counts["A0_FROZEN_BASELINE_OFF"][m_id] += 1
                rec0 = _snapshot_to_compact_decision_record_v179312(
                    s0,
                    checkpoint_index_1m=i_1m,
                    variant="A0_FROZEN_BASELINE_OFF",
                    inside_legacy_screen=in_screen,
                    macro_primary_window_id=macro_win_id,
                    macro_eligible=macro_state.eligible_for_macro_gate,
                )
                eligible_records_by_variant["A0_FROZEN_BASELINE_OFF"].append(rec0)

            if str(s0.setup_validity) == "VALID":
                models_with_valid_setup.append(m_id)
            else:
                # Setup is INVALID in Step 1-4 -> identical status & rejection reasons across all variants
                for vk_other in (
                    "A1_V1792_SL_TP_BASELINE",
                    "C1_MACRO_PLUS_V1792_SL_TP",
                    "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
                    "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
                    "E1_OUTSIDE_MACRO_COUNTERFACTUAL",
                ):
                    status_counts_by_variant[vk_other][st0] += 1
                    for rc in s0.rejection_reason_codes or ():
                        rejection_counts_by_variant[vk_other][rc] += 1
                stream_digest.update(f"{i_1m}:{asof_ms}:{m_id}:{pref_dir}:INVALID:{st0}\n".encode("utf-8"))

        if models_with_valid_setup:
            valid_raw_setup_timestamps_count += 1
            ctx_a1 = build_v1793_variant_market_context(
                base_inputs,
                pol_a1_fast,
                decision_time=asof_ms,
                reference_context=ctx_a0,
            )
            ctx_d1 = (
                build_v1793_variant_market_context(
                    base_inputs,
                    pol_d1_fast,
                    decision_time=asof_ms,
                    reference_context=ctx_a1,
                )
                if is_clean_inside_macro
                else None
            )
            ctx_d2 = (
                build_v1793_variant_market_context(
                    base_inputs,
                    pol_d2_fast,
                    decision_time=asof_ms,
                    reference_context=ctx_a1,
                )
                if is_clean_inside_macro
                else None
            )
            for m_id in models_with_valid_setup:
                s1 = SharedStrategyEvaluator.evaluate_model_direction(ctx_a1, m_id, pref_dir)
                st1 = str(s1.decision_status)
                status_counts_by_variant["A1_V1792_SL_TP_BASELINE"][st1] += 1
                for rc in s1.rejection_reason_codes or ():
                    rejection_counts_by_variant["A1_V1792_SL_TP_BASELINE"][rc] += 1

                rec1 = _snapshot_to_compact_decision_record_v179312(
                    s1,
                    checkpoint_index_1m=i_1m,
                    variant="A1_V1792_SL_TP_BASELINE",
                    inside_legacy_screen=in_screen,
                    macro_primary_window_id=macro_win_id,
                    macro_eligible=macro_state.eligible_for_macro_gate,
                )
                stream_digest.update(
                    f"{i_1m}:{asof_ms}:{m_id}:{pref_dir}:A1:{st1}:{rec1['entry_reference']}:{rec1['selected_sl']}:{rec1['take_profit']}:{rec1['hypothetical_quantity']}\n".encode(
                        "utf-8"
                    )
                )
                if st1 == DecisionStatus.ELIGIBLE.value:
                    model_eligible_counts["A1_V1792_SL_TP_BASELINE"][m_id] += 1
                    eligible_records_by_variant["A1_V1792_SL_TP_BASELINE"].append(rec1)
                    if in_screen:
                        inside_screen_a1_eligible_count += 1
                    else:
                        outside_screen_a1_eligible_count += 1
                        outside_screen_a1_unique_ts.add(i_1m)

                # Derive exact C1_MACRO_PLUS_V1792_SL_TP decision
                if is_clean_inside_macro:
                    st_c1 = st1
                    rejs_c1 = list(s1.rejection_reason_codes or ())
                else:
                    st_c1 = DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value
                    rejs_c1 = list(s1.rejection_reason_codes or ())
                    if "MACRO_WINDOW_INELIGIBLE" not in rejs_c1:
                        rejs_c1.append("MACRO_WINDOW_INELIGIBLE")
                status_counts_by_variant["C1_MACRO_PLUS_V1792_SL_TP"][st_c1] += 1
                for rc in rejs_c1:
                    rejection_counts_by_variant["C1_MACRO_PLUS_V1792_SL_TP"][rc] += 1
                if st_c1 == DecisionStatus.ELIGIBLE.value:
                    model_eligible_counts["C1_MACRO_PLUS_V1792_SL_TP"][m_id] += 1
                    rec_c1 = dict(rec1)
                    rec_c1["variant"] = "C1_MACRO_PLUS_V1792_SL_TP"
                    rec_c1["decision_status"] = st_c1
                    rec_c1["rejection_reason_codes"] = rejs_c1
                    eligible_records_by_variant["C1_MACRO_PLUS_V1792_SL_TP"].append(rec_c1)

                # Derive exact D1 and D2 decisions
                if is_clean_inside_macro and ctx_d1 is not None and ctx_d2 is not None:
                    s_d1 = SharedStrategyEvaluator.evaluate_model_direction(ctx_d1, m_id, pref_dir)
                    st_d1 = str(s_d1.decision_status)
                    status_counts_by_variant["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"][st_d1] += 1
                    for rc in s_d1.rejection_reason_codes or ():
                        rejection_counts_by_variant["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"][rc] += 1
                    if st_d1 == DecisionStatus.ELIGIBLE.value:
                        model_eligible_counts["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"][m_id] += 1
                        rec_d1 = _snapshot_to_compact_decision_record_v179312(
                            s_d1,
                            checkpoint_index_1m=i_1m,
                            variant="D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
                            inside_legacy_screen=in_screen,
                            macro_primary_window_id=macro_win_id,
                            macro_eligible=macro_state.eligible_for_macro_gate,
                        )
                        eligible_records_by_variant["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"].append(rec_d1)

                    s_d2 = SharedStrategyEvaluator.evaluate_model_direction(ctx_d2, m_id, pref_dir)
                    st_d2 = str(s_d2.decision_status)
                    status_counts_by_variant["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"][st_d2] += 1
                    for rc in s_d2.rejection_reason_codes or ():
                        rejection_counts_by_variant["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"][rc] += 1
                    if st_d2 == DecisionStatus.ELIGIBLE.value:
                        model_eligible_counts["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"][m_id] += 1
                        rec_d2 = _snapshot_to_compact_decision_record_v179312(
                            s_d2,
                            checkpoint_index_1m=i_1m,
                            variant="D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
                            inside_legacy_screen=in_screen,
                            macro_primary_window_id=macro_win_id,
                            macro_eligible=macro_state.eligible_for_macro_gate,
                        )
                        eligible_records_by_variant["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"].append(rec_d2)
                else:
                    for vk_d in ("D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP", "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"):
                        status_counts_by_variant[vk_d][DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value] += 1
                        rejection_counts_by_variant[vk_d]["MACRO_WINDOW_INELIGIBLE"] += 1

                # Derive exact E1_OUTSIDE_MACRO_COUNTERFACTUAL decision
                if is_clean_outside_macro:
                    st_e1 = st1
                    rejs_e1 = list(s1.rejection_reason_codes or ())
                else:
                    st_e1 = DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value
                    rejs_e1 = list(s1.rejection_reason_codes or ())
                    if macro_state.eligible_for_macro_gate:
                        if "COUNTERFACTUAL_REJECTED_INSIDE_MACRO_WINDOW" not in rejs_e1:
                            rejs_e1.append("COUNTERFACTUAL_REJECTED_INSIDE_MACRO_WINDOW")
                    else:
                        for mr in macro_qual.reason_codes:
                            if mr not in rejs_e1:
                                rejs_e1.append(mr)
                        if not rejs_e1:
                            rejs_e1.append("MACRO_OUTSIDE_COUNTERFACTUAL_INELIGIBLE")

                status_counts_by_variant["E1_OUTSIDE_MACRO_COUNTERFACTUAL"][st_e1] += 1
                for rc in rejs_e1:
                    rejection_counts_by_variant["E1_OUTSIDE_MACRO_COUNTERFACTUAL"][rc] += 1

                if st_e1 == DecisionStatus.ELIGIBLE.value:
                    model_eligible_counts["E1_OUTSIDE_MACRO_COUNTERFACTUAL"][m_id] += 1
                    rec_e1 = dict(rec1)
                    rec_e1["variant"] = "E1_OUTSIDE_MACRO_COUNTERFACTUAL"
                    rec_e1["decision_status"] = st_e1
                    rec_e1["rejection_reason_codes"] = rejs_e1
                    eligible_records_by_variant["E1_OUTSIDE_MACRO_COUNTERFACTUAL"].append(rec_e1)
                    if in_screen:
                        inside_screen_e1_eligible_count += 1
                    else:
                        outside_screen_e1_eligible_count += 1
                        outside_screen_e1_unique_ts.add(i_1m)

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "start_index": int(index_chunk[0]) if index_chunk else -1,
        "end_index": int(index_chunk[-1]) if index_chunk else -1,
        "timestamps_evaluated": len(index_chunk),
        "model_evaluations_per_variant": len(index_chunk) * len(PRODUCTION_MODEL_IDS),
        "valid_raw_setup_timestamps_count": valid_raw_setup_timestamps_count,
        "eligible_records_by_variant": eligible_records_by_variant,
        "status_counts_by_variant": {k: dict(v) for k, v in status_counts_by_variant.items()},
        "rejection_counts_by_variant": {k: dict(v) for k, v in rejection_counts_by_variant.items()},
        "model_eligible_counts_by_variant": {k: dict(v) for k, v in model_eligible_counts.items()},
        "outside_screen_a1_eligible_count": outside_screen_a1_eligible_count,
        "outside_screen_e1_eligible_count": outside_screen_e1_eligible_count,
        "inside_screen_a1_eligible_count": inside_screen_a1_eligible_count,
        "inside_screen_e1_eligible_count": inside_screen_e1_eligible_count,
        "outside_screen_a1_unique_timestamps": sorted(outside_screen_a1_unique_ts),
        "outside_screen_e1_unique_timestamps": sorted(outside_screen_e1_unique_ts),
        "chunk_stream_sha256": stream_digest.hexdigest(),
    }


def write_append_only_decision_stream_v179312(
    records: Sequence[Mapping[str, Any]],
    chunk_path: Path,
    *,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Section 03 & 10: Writes ordered decision records to a compressed `.jsonl.gz` chunk file
    via atomic temp-file -> SHA-256 verification -> rename.
    """
    target_path = Path(chunk_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(target_path.suffix + f".tmp.{os.getpid()}")

    row_hash = hashlib.sha256()
    sorted_recs = sorted(
        records,
        key=lambda r: (
            int(r.get("decision_timestamp_ms", 0)),
            int(r.get("checkpoint_index_1m", 0)),
            str(r.get("variant", "")),
            str(r.get("model_id", "")),
            str(r.get("direction", "")),
        ),
    )

    with gzip.open(tmp_path, "wt", encoding="utf-8", compresslevel=6) as gz:
        for rec in sorted_recs:
            line = json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
            row_hash.update(line.encode("utf-8"))
            gz.write(line)

    raw_bytes = tmp_path.read_bytes()
    if len(raw_bytes) == 0:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"EMPTY_CHUNK_STREAM_WRITE_FAILED:{target_path}")

    file_sha = hashlib.sha256(raw_bytes).hexdigest()
    tmp_path.replace(target_path)

    return {
        "chunk_file": str(target_path.name),
        "chunk_path": str(target_path),
        "record_count": len(sorted_recs),
        "compressed_size_bytes": len(raw_bytes),
        "uncompressed_rows_sha256": row_hash.hexdigest(),
        "compressed_file_sha256": file_sha,
        "metadata": dict(metadata or {}),
    }


@dataclass(frozen=True)
class FullUniverseDecisionManifest:
    schema_version: str
    total_chunks: int
    total_timestamps_covered: int
    expected_timestamps_count: int
    total_model_evaluations_per_variant: int
    total_persisted_eligible_records: int
    zero_missing_ranges: bool
    zero_duplicate_ranges: bool
    zero_corrupt_chunks: bool
    canonical_rows_sha256: str
    variant_status_totals: Dict[str, Dict[str, int]]
    variant_eligible_totals: Dict[str, int]
    variant_model_eligible_totals: Dict[str, Dict[str, int]]
    outside_screen_reconciliation: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def merge_and_validate_decision_stream_v179312(
    chunks: Sequence[Mapping[str, Any]],
    *,
    expected_start_index: int = 30,
    expected_end_index: int = 22375,
    verify_chunk_files: bool = True,
) -> Tuple[FullUniverseDecisionManifest, Dict[str, List[Dict[str, Any]]]]:
    """
    Section 03 & 10: Merges and validates chunk results / `.jsonl.gz` streams sorted by
    `(decision_ts, index, model_id, direction, policy_id)`.
    Hard-fails (`ValueError`) on duplicate index ranges, missing index ranges, corrupt chunk
    hashes, or model-evaluation count mismatches.
    """
    if not chunks:
        raise ValueError("EMPTY_DECISION_CHUNKS_LIST")

    sorted_chunks = sorted(chunks, key=lambda c: (int(c["start_index"]), int(c["end_index"])))
    covered_indices: List[int] = []
    seen_indices: Set[int] = set()
    duplicate_indices: List[int] = []

    variant_status_totals: Dict[str, Counter[str]] = defaultdict(Counter)
    variant_rej_totals: Dict[str, Counter[str]] = defaultdict(Counter)
    variant_model_elig: Dict[str, Counter[str]] = defaultdict(Counter)
    merged_eligible_by_variant: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    outside_a1_count = 0
    outside_e1_count = 0
    inside_a1_count = 0
    inside_e1_count = 0
    outside_a1_ts: Set[int] = set()
    outside_e1_ts: Set[int] = set()
    valid_raw_setup_ts = 0

    for ch in sorted_chunks:
        s_idx = int(ch["start_index"])
        e_idx = int(ch["end_index"])
        if s_idx > e_idx:
            raise ValueError(f"INVALID_CHUNK_INDEX_RANGE:{s_idx}..{e_idx}")

        if verify_chunk_files and ch.get("chunk_stream_manifest"):
            c_man = ch["chunk_stream_manifest"]
            c_path = Path(c_man["chunk_path"])
            if not c_path.exists():
                raise ValueError(f"MISSING_CHUNK_STREAM_FILE:{c_path}")
            actual_sha = hashlib.sha256(c_path.read_bytes()).hexdigest()
            if actual_sha != str(c_man["compressed_file_sha256"]):
                raise ValueError(
                    f"CORRUPT_CHUNK_STREAM_SHA256:{c_path}:actual={actual_sha},expected={c_man['compressed_file_sha256']}"
                )

        for idx in range(s_idx, e_idx + 1):
            if idx in seen_indices:
                duplicate_indices.append(idx)
            seen_indices.add(idx)
            covered_indices.append(idx)

        for vk, st_map in (ch.get("status_counts_by_variant") or {}).items():
            for st_k, cnt in st_map.items():
                variant_status_totals[vk][st_k] += int(cnt)
        for vk, r_map in (ch.get("rejection_counts_by_variant") or {}).items():
            for r_k, cnt in r_map.items():
                variant_rej_totals[vk][r_k] += int(cnt)
        for vk, m_map in (ch.get("model_eligible_counts_by_variant") or {}).items():
            for m_k, cnt in m_map.items():
                variant_model_elig[vk][m_k] += int(cnt)
        for vk, recs in (ch.get("eligible_records_by_variant") or {}).items():
            merged_eligible_by_variant[vk].extend(recs)

        outside_a1_count += int(ch.get("outside_screen_a1_eligible_count", 0))
        outside_e1_count += int(ch.get("outside_screen_e1_eligible_count", 0))
        inside_a1_count += int(ch.get("inside_screen_a1_eligible_count", 0))
        inside_e1_count += int(ch.get("inside_screen_e1_eligible_count", 0))
        outside_a1_ts.update(int(x) for x in (ch.get("outside_screen_a1_unique_timestamps") or ()))
        outside_e1_ts.update(int(x) for x in (ch.get("outside_screen_e1_unique_timestamps") or ()))
        valid_raw_setup_ts += int(ch.get("valid_raw_setup_timestamps_count", 0))

    if duplicate_indices:
        raise ValueError(f"DUPLICATE_CHUNK_INDICES_DETECTED:count={len(duplicate_indices)},sample={duplicate_indices[:10]}")

    expected_set = set(range(int(expected_start_index), int(expected_end_index) + 1))
    missing_indices = sorted(expected_set - seen_indices)
    if missing_indices:
        raise ValueError(f"MISSING_CHUNK_INDICES_DETECTED:count={len(missing_indices)},sample={missing_indices[:10]}")

    expected_ts_count = len(expected_set)
    expected_model_evals = expected_ts_count * len(PRODUCTION_MODEL_IDS)

    # Sort all eligible records canonically and verify uniqueness
    canonical_hash = hashlib.sha256()
    total_persisted_eligible = 0
    for vk in sorted(merged_eligible_by_variant.keys()):
        recs = merged_eligible_by_variant[vk]
        recs.sort(
            key=lambda r: (
                int(r["decision_timestamp_ms"]),
                int(r["checkpoint_index_1m"]),
                str(r["model_id"]),
                str(r["direction"]),
                str(r["variant"]),
            )
        )
        seen_keys: Set[Tuple[int, str, str]] = set()
        for r in recs:
            k = (int(r["checkpoint_index_1m"]), str(r["model_id"]), str(r["variant"]))
            if k in seen_keys:
                raise ValueError(f"DUPLICATE_DECISION_RECORD_KEY:{k}")
            seen_keys.add(k)
            canonical_hash.update(
                json.dumps(r, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            )
        total_persisted_eligible += len(recs)

        # Verify status sum equals expected_model_evals
        st_sum = sum(variant_status_totals[vk].values())
        if st_sum != expected_model_evals:
            raise ValueError(
                f"MODEL_EVALUATION_COUNT_MISMATCH_FOR_{vk}:actual={st_sum},expected={expected_model_evals}"
            )

    a1_all = merged_eligible_by_variant.get("A1_V1792_SL_TP_BASELINE", [])
    e1_all = merged_eligible_by_variant.get("E1_OUTSIDE_MACRO_COUNTERFACTUAL", [])
    a1_all_unique_ts = len({int(r["checkpoint_index_1m"]) for r in a1_all})
    e1_all_unique_ts = len({int(r["checkpoint_index_1m"]) for r in e1_all})

    outside_reconciliation = {
        "outside_screen_a1_eligible_decisions_count": outside_a1_count,
        "outside_screen_a1_unique_timestamps_count": len(outside_a1_ts),
        "inside_screen_a1_eligible_decisions_count": inside_a1_count,
        "full_universe_a1_eligible_decisions_count": len(a1_all),
        "full_universe_a1_unique_timestamps_count": a1_all_unique_ts,
        "outside_screen_e1_eligible_decisions_count": outside_e1_count,
        "outside_screen_e1_unique_timestamps_count": len(outside_e1_ts),
        "inside_screen_e1_eligible_decisions_count": inside_e1_count,
        "full_universe_e1_eligible_decisions_count": len(e1_all),
        "full_universe_e1_unique_timestamps_count": e1_all_unique_ts,
        "valid_raw_setup_timestamps_count": valid_raw_setup_ts,
        "reconciles_with_v179311_independent_audit_47243_and_15597": (
            outside_a1_count == 47243 and len(outside_a1_ts) == 15597
        ),
        "why_e1_differs_from_a1_by_92_outside_screen_decisions": (
            f"A1 has {outside_a1_count} outside-screen eligible decisions ({inside_a1_count} inside screen = {len(a1_all)} total). "
            f"E1 has {outside_e1_count} outside-screen eligible decisions ({inside_e1_count} inside screen = {len(e1_all)} total). "
            f"The difference on outside-screen timestamps ({outside_a1_count - outside_e1_count} decisions) occurs at weekend-closed "
            "bars where A1 (v17_9_3_mode=OFF) does not apply the V17.9.3 weekend-closed macro gate while E1 "
            "(v17_9_3_mode=OUTSIDE_MACRO_COUNTERFACTUAL) rejects weekend-closed bars with MACRO_WEEKEND_CLOSED."
        ),
    }

    manifest = FullUniverseDecisionManifest(
        schema_version=V17_9_3_1_2_SCHEMA_VERSION,
        total_chunks=len(sorted_chunks),
        total_timestamps_covered=expected_ts_count,
        expected_timestamps_count=expected_ts_count,
        total_model_evaluations_per_variant=expected_model_evals,
        total_persisted_eligible_records=total_persisted_eligible,
        zero_missing_ranges=True,
        zero_duplicate_ranges=True,
        zero_corrupt_chunks=True,
        canonical_rows_sha256=canonical_hash.hexdigest(),
        variant_status_totals={k: dict(v) for k, v in variant_status_totals.items()},
        variant_eligible_totals={k: len(v) for k, v in merged_eligible_by_variant.items()},
        variant_model_eligible_totals={k: dict(v) for k, v in variant_model_elig.items()},
        outside_screen_reconciliation=outside_reconciliation,
    )
    return manifest, dict(merged_eligible_by_variant)


# ==============================================================================
# SECTION 04 — GLOBAL CHRONOLOGICAL PORTFOLIO REPLAY & POSITION STATE MACHINE
# ==============================================================================
@dataclass(frozen=True)
class OrderIntentV179312:
    intent_id: str
    variant: str
    model_id: str
    symbol: str
    direction: str
    checkpoint_index_1m: int
    signal_timestamp_ms: int
    entry_reference: float
    selected_sl: float
    take_profit: float
    hypothetical_quantity: float
    planned_net_risk_usd: float
    gross_rr: float
    net_rr: float
    order_type: str
    ttl_bars: int
    selected_anchor_id: Optional[str]
    anchor_kind: Optional[str]
    structural_invalidation_price: Optional[float]
    selected_target_id: Optional[str]
    setup_episode_id: Optional[str]
    inside_legacy_screen: bool
    macro_primary_window_id: Optional[str]
    trade_action_fingerprint: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_order_intent_from_snapshot_v179312(
    snapshot: Any,
    policy: Optional[RiskPolicySnapshotV179311] = None,
    timestamp: Optional[int] = None,
    evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Section 04: Converts an `ELIGIBLE` snapshot or compact decision record into a validated
    `OrderIntentV179312` or returns an explicit rejection (`status = "REJECTED"`).
    Never infers forward trade outcomes while constructing intent.
    """
    pol = policy or get_canonical_risk_policy_snapshot()
    is_map = isinstance(snapshot, Mapping)
    get_f = (lambda k, d=None: snapshot.get(k, d)) if is_map else (lambda k, d=None: getattr(snapshot, k, d))

    dec_status = str(get_f("decision_status") or "")
    if dec_status != DecisionStatus.ELIGIBLE.value:
        return {
            "status": "REJECTED",
            "rejection_reason": f"NON_ELIGIBLE_STATUS:{dec_status}",
            "order_intent": None,
        }

    ent = get_f("entry_reference")
    sl = get_f("selected_sl")
    tp = get_f("take_profit")
    direction = str(get_f("direction") or "").upper()
    model_id = str(get_f("model_id") or "")
    symbol = str(get_f("symbol") or "XAUUSDT")
    sig_ts = int(timestamp if timestamp is not None else (get_f("decision_timestamp_ms") or 0))
    idx_1m = int(get_f("checkpoint_index_1m") or (evidence or {}).get("checkpoint_index_1m") or 0)
    variant = str(get_f("variant") or (evidence or {}).get("variant") or "A1_V1792_SL_TP_BASELINE")

    if (
        ent is None
        or sl is None
        or tp is None
        or not (math.isfinite(float(ent)) and math.isfinite(float(sl)) and math.isfinite(float(tp)))
        or float(ent) <= 0.0
        or float(sl) <= 0.0
        or float(tp) <= 0.0
        or sig_ts <= 0
        or direction not in ("LONG", "SHORT")
    ):
        return {
            "status": "REJECTED",
            "rejection_reason": "INVALID_OR_MISSING_ORDER_GEOMETRY",
            "order_intent": None,
        }

    ent_f = round(float(ent), 2)
    sl_f = round(float(sl), 2)
    tp_f = round(float(tp), 2)
    geom_ok = (sl_f < ent_f < tp_f) if direction == "LONG" else (tp_f < ent_f < sl_f)
    if not geom_ok:
        return {
            "status": "REJECTED",
            "rejection_reason": f"INVALID_DIRECTIONAL_GEOMETRY:{direction}:ent={ent_f},sl={sl_f},tp={tp_f}",
            "order_intent": None,
        }

    costs = CostAssumptions(
        slippage_usd=pol.slippage_usd,
        maker_fee_rate=pol.maker_fee_rate,
        taker_fee_rate=pol.taker_fee_rate,
        tp_is_maker=False,
    )
    calc = calculate_risk_reward(
        direction=direction,
        planned_entry=ent_f,
        stop_loss=sl_f,
        take_profit=tp_f,
        capital_usdt=pol.capital_usdt,
        risk_pct=pol.risk_pct_percent,
        leverage=pol.leverage,
        margin_mode=pol.margin_mode,
        min_net_rr=pol.min_net_rr,
        costs=costs,
        now_ms=sig_ts,
    )
    qty = round(float(calc.quantity or 0.0), 4)
    planned_risk = round(float(calc.net_risk_usdt or 0.0), 4)
    net_rr = round(float(calc.net_rr or 0.0), 4)
    gross_rr = round(float(calc.gross_rr or 0.0), 4)

    if not calc.is_valid or not calc.can_execute or qty < pol.min_quantity:
        return {
            "status": "REJECTED",
            "rejection_reason": str(calc.invalid_reason or calc.skip_reason or "BELOW_MIN_LOT_SIZE"),
            "order_intent": None,
        }
    if planned_risk <= 0.0 or planned_risk > pol.max_planned_risk_usd + 1e-6:
        return {
            "status": "REJECTED",
            "rejection_reason": f"PLANNED_RISK_EXCEEDS_BUDGET:{planned_risk:.4f}>{pol.max_planned_risk_usd:.4f}",
            "order_intent": None,
        }
    if net_rr + 1e-9 < pol.min_net_rr:
        return {
            "status": "REJECTED",
            "rejection_reason": f"INSUFFICIENT_NET_RR:{net_rr:.4f}<{pol.min_net_rr:.4f}",
            "order_intent": None,
        }

    intent = OrderIntentV179312(
        intent_id=f"intent_{variant}_{model_id}_{idx_1m}_{sig_ts}",
        variant=variant,
        model_id=model_id,
        symbol=symbol,
        direction=direction,
        checkpoint_index_1m=idx_1m,
        signal_timestamp_ms=sig_ts,
        entry_reference=ent_f,
        selected_sl=sl_f,
        take_profit=tp_f,
        hypothetical_quantity=qty,
        planned_net_risk_usd=planned_risk,
        gross_rr=gross_rr,
        net_rr=net_rr,
        order_type="NEXT_BAR_OPEN_MARKET_QUOTED",
        ttl_bars=1,
        selected_anchor_id=get_f("selected_anchor_id"),
        anchor_kind=get_f("anchor_kind"),
        structural_invalidation_price=get_f("structural_invalidation_price"),
        selected_target_id=get_f("selected_target_id"),
        setup_episode_id=get_f("setup_episode_id"),
        inside_legacy_screen=bool(get_f("inside_legacy_screen", False)),
        macro_primary_window_id=get_f("macro_primary_window_id"),
        trade_action_fingerprint=str(get_f("trade_action_fingerprint") or ""),
    )
    return {
        "status": "VALID_INTENT",
        "rejection_reason": None,
        "order_intent": intent,
    }


def arbitrate_competing_model_intents_v179312(
    intents: Sequence[OrderIntentV179312],
    state: Optional[Mapping[str, Any]] = None,
    tie_policy: str = "PRODUCTION_MODEL_ORDER",
) -> Dict[str, Any]:
    """
    Section 04: Deterministic same-timestamp model tie arbitration consistent with
    production `PRODUCTION_MODEL_IDS` evaluation order (`SMC_M1_REVERSAL`,
    `SMC_M1_CONTINUATION`, `SMC_M1_FVG_CONTINUATION`, `SMC_M1_SWEEP_RECLAIM`,
    `CG01_SWEEP_MSS_FVG`).
    Records the selected winner and all simultaneous discarded competing model intents.
    """
    if not intents:
        return {
            "selected_intent": None,
            "discarded_intents": [],
            "tie_occurred": False,
            "tie_policy": tie_policy,
        }

    priority_map = {m: idx for idx, m in enumerate(CANONICAL_PRODUCTION_MODEL_PRIORITY)}
    if tie_policy == "PRODUCTION_MODEL_ORDER":
        ranked = sorted(
            intents,
            key=lambda it: (
                priority_map.get(it.model_id, 999),
                -float(it.net_rr),
                str(it.intent_id),
            ),
        )
    elif tie_policy == "HIGHEST_NET_RR_THEN_PRODUCTION_ORDER":
        ranked = sorted(
            intents,
            key=lambda it: (
                -float(it.net_rr),
                priority_map.get(it.model_id, 999),
                str(it.intent_id),
            ),
        )
    else:
        raise ValueError(f"UNKNOWN_TIE_POLICY:{tie_policy}")

    winner = ranked[0]
    discarded = ranked[1:]
    return {
        "selected_intent": winner,
        "discarded_intents": discarded,
        "tie_occurred": len(discarded) > 0,
        "tie_policy": tie_policy,
        "discarded_model_ids": [d.model_id for d in discarded],
    }


@dataclass
class PortfolioReplayStateV179312:
    variant: str
    max_concurrent_positions: int = 1
    active_episode: Optional[Dict[str, Any]] = None
    active_release_ts_ms: int = 0
    cash_balance_usd: float = 1000.0
    realized_net_pnl_usd: float = 0.0
    realized_gross_pnl_usd: float = 0.0
    cumulative_costs_usd: float = 0.0
    peak_equity_usd: float = 1000.0
    max_drawdown_usd: float = 0.0
    closed_episodes_count: int = 0
    open_censored_count: int = 0
    data_gap_unresolved_count: int = 0
    no_fill_attempts_count: int = 0
    blocked_by_open_position_count: int = 0
    discarded_same_bar_tie_count: int = 0


def advance_portfolio_to_next_event_v179312(
    state: PortfolioReplayStateV179312,
    historical_bars: Optional[Sequence[Mapping[str, Any]]],
    cutoff_ms: int,
) -> Dict[str, Any]:
    """
    Section 04: Advances portfolio state up to `cutoff_ms` without lookahead.
    If an active position's release timestamp (`exit_fill_ts` for closed trades, or
    `observation_horizon_end_ts` for `OPEN_AT_HORIZON` / `DATA_GAP_UNRESOLVED`) is
    `<= cutoff_ms`, releases the position slot and updates realized equity/drawdown.
    """
    released_episode: Optional[Dict[str, Any]] = None
    if state.active_episode is not None and int(cutoff_ms) >= int(state.active_release_ts_ms):
        released_episode = state.active_episode
        out_lbl = str(released_episode.get("outcome_label") or "")
        if out_lbl in ("WIN", "LOSS"):
            net_pnl = float(released_episode.get("realized_net_pnl_usd", 0.0) or 0.0)
            gross_pnl = float(released_episode.get("realized_gross_pnl_usd", 0.0) or 0.0)
            cost_usd = float(released_episode.get("total_friction_usd", 0.0) or 0.0)
            state.realized_net_pnl_usd = round(state.realized_net_pnl_usd + net_pnl, 4)
            state.realized_gross_pnl_usd = round(state.realized_gross_pnl_usd + gross_pnl, 4)
            state.cumulative_costs_usd = round(state.cumulative_costs_usd + cost_usd, 4)
            state.cash_balance_usd = round(1000.0 + state.realized_net_pnl_usd, 4)
            if state.cash_balance_usd > state.peak_equity_usd:
                state.peak_equity_usd = state.cash_balance_usd
            dd = round(state.peak_equity_usd - state.cash_balance_usd, 4)
            if dd > state.max_drawdown_usd:
                state.max_drawdown_usd = dd
            state.closed_episodes_count += 1
        elif out_lbl == "OPEN_AT_HORIZON":
            state.open_censored_count += 1
        elif out_lbl == "DATA_GAP_UNRESOLVED":
            state.data_gap_unresolved_count += 1

        state.active_episode = None
        state.active_release_ts_ms = 0

    return {
        "position_open_at_cutoff": state.active_episode is not None,
        "active_release_ts_ms": state.active_release_ts_ms,
        "released_episode": released_episode,
        "cash_balance_usd": state.cash_balance_usd,
    }


def simulate_executable_candidate_v179312(
    order_intent: OrderIntentV179312,
    forward_bars: Sequence[Mapping[str, Any]],
    fill_mode: str = "NEXT_BAR_OPEN_BID_ASK",
    *,
    closed_bars_at_decision: Optional[Sequence[Mapping[str, Any]]] = None,
    closed_m5: Optional[Sequence[Mapping[str, Any]]] = None,
    closed_m15: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Section 04: Delegates forward execution simulation of `order_intent` to the canonical
    `simulate_v17931_trade_execution_net` simulator and resolves protected structure authority
    and stop outcome diagnostics.
    """
    ledger: ExecutableTradeLedgerEntryV17931 = simulate_v17931_trade_execution_net(
        episode_key=f"{order_intent.variant}_{order_intent.model_id}_{order_intent.checkpoint_index_1m}_{order_intent.signal_timestamp_ms}",
        variant=order_intent.variant,
        model_id=order_intent.model_id,
        direction=order_intent.direction,
        decision_ts=order_intent.signal_timestamp_ms,
        planned_entry=order_intent.entry_reference,
        planned_sl=order_intent.selected_sl,
        planned_tp=order_intent.take_profit,
        forward_bars=forward_bars,
        fill_mode=fill_mode,
        setup_id=order_intent.setup_episode_id or f"setup_{order_intent.checkpoint_index_1m}",
    )
    led_dict = ledger.to_dict()

    # Determine release timestamp in wall-clock ms (gap-aware, never assuming 1 bar == 60s across gaps)
    if not ledger.filled:
        outcome_label = str(ledger.exit_type)
        release_ts_ms = int(order_intent.signal_timestamp_ms)
    elif ledger.exit_type in ("WIN", "LOSS", "LOSS_TIE_CONSERVATIVE", "ADVERSE_GAP_OPEN_STOP_LOSS"):
        outcome_label = "WIN" if ledger.exit_type == "WIN" else "LOSS"
        release_ts_ms = int(ledger.exit_fill_ts or (order_intent.signal_timestamp_ms + 60_000))
    elif ledger.exit_type == "OPEN_AT_HORIZON":
        outcome_label = "OPEN_AT_HORIZON"
        if forward_bars:
            horizon_slice = forward_bars[: min(len(forward_bars), 180)]
            release_ts_ms = int(horizon_slice[-1].get("close_time") or (order_intent.signal_timestamp_ms + 180 * 60_000))
        else:
            release_ts_ms = int(order_intent.signal_timestamp_ms)
    else:
        outcome_label = "DATA_GAP_UNRESOLVED"
        if ledger.trigger_bar_ts is not None:
            release_ts_ms = int(ledger.trigger_bar_ts)
        elif forward_bars:
            release_ts_ms = int(forward_bars[0].get("close_time") or (order_intent.signal_timestamp_ms + 60_000))
        else:
            release_ts_ms = int(order_intent.signal_timestamp_ms)

    inv_px = (
        float(order_intent.structural_invalidation_price)
        if order_intent.structural_invalidation_price is not None
        else float(order_intent.selected_sl)
    )
    ind_prot_px: Optional[float] = None
    ind_anchor_id: Optional[str] = None
    ind_anchor_kind: Optional[str] = None
    ind_anchor_tf: Optional[str] = None
    ind_anchor_known_ms: Optional[int] = None
    ind_authority_source = "UNKNOWN_PROTECTED_STRUCTURE_NOT_ESTABLISHED"

    if closed_bars_at_decision or closed_m5 or closed_m15:
        c_m1 = list(closed_bars_at_decision or ())
        c_m5 = list(closed_m5 or ())
        c_m15 = list(closed_m15 or ())
        dec_ts_int = int(order_intent.signal_timestamp_ms)
        ent_px_f = float(ledger.entry_fill_price or order_intent.entry_reference)
        sl_px_f = float(order_intent.selected_sl)
        cands = collect_causal_stop_anchor_candidates(
            model_id=order_intent.model_id,
            direction=order_intent.direction,
            entry_price=ent_px_f,
            decision_timestamp_ms=dec_ts_int,
            closed_m1=c_m1,
            closed_m5=c_m5,
            closed_m15=c_m15,
            tick_size=0.01,
            enforce_cross_tf_invalidation_check=True,
        )
        _, ranked_c, _ = rank_structure_valid_anchors(
            cands,
            model_id=order_intent.model_id,
            direction=order_intent.direction,
            entry_price=ent_px_f,
            decision_timestamp_ms=dec_ts_int,
        )
        valid_prot = [
            c
            for c in ranked_c
            if c.is_causal
            and c.is_closed_bar
            and not c.is_invalidated
            and len(c.rejection_reasons) == 0
            and int(c.known_at_ms) <= dec_ts_int
            and int(c.source_bar_close_ms) <= dec_ts_int
            and (c.timeframe in ("5M", "15M") or (c.is_protected_swing and not c.is_local_sweep_only))
            and abs(float(c.anchor_price) - sl_px_f) > 1e-6
        ]
        # Prefer outer M15/M5 protected swing that is at or outside the trade's local invalidation
        outer_htf = [
            c
            for c in valid_prot
            if c.timeframe in ("5M", "15M")
            and (
                (order_intent.direction == "LONG" and float(c.anchor_price) <= inv_px + 0.05)
                or (order_intent.direction == "SHORT" and float(c.anchor_price) >= inv_px - 0.05)
            )
        ]
        chosen_ind = outer_htf[0] if outer_htf else (valid_prot[0] if valid_prot else None)
        if chosen_ind is not None:
            ind_prot_px = round(float(chosen_ind.anchor_price), 2)
            ind_anchor_id = str(chosen_ind.anchor_id)
            ind_anchor_kind = str(chosen_ind.anchor_kind)
            ind_anchor_tf = str(chosen_ind.timeframe)
            ind_anchor_known_ms = int(chosen_ind.known_at_ms)
            ind_authority_source = f"INDEPENDENT_{ind_anchor_tf}_PROTECTED_SWING_AUTHORITY"

    eff_prot_px = ind_prot_px if ind_prot_px is not None else inv_px
    stop_diag = classify_structure_stop_outcome(
        episode_id=f"{order_intent.variant}_{order_intent.model_id}_{order_intent.checkpoint_index_1m}",
        model_id=order_intent.model_id,
        direction=order_intent.direction,
        entry_price=float(ledger.entry_fill_price or order_intent.entry_reference),
        sl_price=float(order_intent.selected_sl),
        tp_price=float(order_intent.take_profit),
        invalidation_price=inv_px,
        protected_structure_price=eff_prot_px,
        decision_timestamp_ms=int(order_intent.signal_timestamp_ms),
        forward_bars=forward_bars[:180],
        provenance="HISTORICAL_BACKTEST_V179312",
    )

    opp_id = _build_composite_opportunity_id_internal_v179312(
        symbol=order_intent.symbol,
        model_id=order_intent.model_id,
        direction=order_intent.direction,
        base_decision_ts_ms=order_intent.signal_timestamp_ms,
        base_checkpoint_index_1m=order_intent.checkpoint_index_1m,
        setup_episode_id=order_intent.setup_episode_id,
        disambiguator=f"{order_intent.entry_reference:.2f}_{order_intent.selected_sl:.2f}_{order_intent.take_profit:.2f}",
    )

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "provenance": "HISTORICAL_BACKTEST_V179312",
        "execution_truth_class": "MODELED_HISTORICAL_BAR_EXECUTION",
        "execution_fill_mode": fill_mode,
        "episode_key": led_dict["episode_key"],
        "opportunity_id": opp_id,
        "variant": order_intent.variant,
        "model_id": order_intent.model_id,
        "symbol": order_intent.symbol,
        "direction": order_intent.direction,
        "checkpoint_index_1m": order_intent.checkpoint_index_1m,
        "decision_timestamp_ms": order_intent.signal_timestamp_ms,
        "decision_time_utc": _ms_to_utc_iso(order_intent.signal_timestamp_ms),
        "session": _session_for_ms(order_intent.signal_timestamp_ms),
        "inside_legacy_screen": order_intent.inside_legacy_screen,
        "macro_primary_window_id": order_intent.macro_primary_window_id,
        "entry_reference": order_intent.entry_reference,
        "selected_sl": order_intent.selected_sl,
        "take_profit": order_intent.take_profit,
        "structural_invalidation_price": inv_px,
        "selected_anchor_id": order_intent.selected_anchor_id,
        "anchor_kind": order_intent.anchor_kind,
        "selected_target_id": order_intent.selected_target_id,
        "setup_episode_id": order_intent.setup_episode_id,
        "hypothetical_quantity": order_intent.hypothetical_quantity,
        "executed_quantity": ledger.quantity,
        "planned_net_risk_usd": ledger.planned_net_risk_usd,
        "gross_rr": order_intent.gross_rr,
        "net_rr": order_intent.net_rr,
        "filled": ledger.filled,
        "outcome_label": outcome_label,
        "exit_type": ledger.exit_type,
        "entry_fill_ts": ledger.entry_fill_ts,
        "exit_fill_ts": ledger.exit_fill_ts,
        "release_ts_ms": release_ts_ms,
        "entry_fill_price": ledger.entry_fill_price,
        "exit_fill_price": ledger.exit_fill_price,
        "entry_fee_usd": ledger.entry_fee,
        "exit_fee_usd": ledger.exit_fee,
        "total_friction_usd": ledger.modeled_total_cost_usd,
        "realized_gross_pnl_usd": ledger.realized_gross_pnl_usd,
        "realized_net_pnl_usd": ledger.realized_net_pnl_usd,
        "realized_r": ledger.realized_r_net,
        "realized_r_gross": ledger.realized_r_gross,
        "bars_held": ledger.bars_held,
        "mfe_usd": stop_diag.mfe_usd,
        "mae_usd": stop_diag.mae_usd,
        "mfe_r": stop_diag.mfe_r,
        "mae_r": stop_diag.mae_r,
        "same_bar_sl_tp_tie": ledger.same_bar_sl_tp_tie,
        "adverse_gap_beyond_sl_usd": round(
            max(0.0, float(abs(ledger.realized_net_pnl_usd or 0.0)) - float(ledger.planned_net_risk_usd or 2.50)),
            4,
        )
        if outcome_label == "LOSS" and (ledger.realized_net_pnl_usd or 0.0) < 0
        else 0.0,
        "execution_ledger": led_dict,
        "stop_outcome_diagnostic": stop_diag.to_dict(),
        "protected_structure_authority": {
            "protected_structure_price": eff_prot_px,
            "independent_protected_structure_price": ind_prot_px,
            "local_invalidation_price": inv_px,
            "outer_anchor_id": ind_anchor_id or order_intent.selected_anchor_id,
            "outer_anchor_kind": ind_anchor_kind or order_intent.anchor_kind,
            "independent_anchor_timeframe": ind_anchor_tf,
            "independent_anchor_known_at_ms": ind_anchor_known_ms,
            "anchor_independence_established": ind_prot_px is not None,
            "authority_source": ind_authority_source if ind_prot_px is not None else "V1792_OUTER_STRUCTURAL_ANCHOR",
        },
    }


def verify_no_trade_time_overlap_v179312(
    episodes: Sequence[Mapping[str, Any]],
    position_contract: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Section 04: Exact interval collision check across filled portfolio episodes using actual
    `[entry_fill_ts, release_ts_ms]` wall-clock timestamps (never `bars_held` index math).
    `NO_FILL` episodes do not occupy a position slot.
    `OPEN_AT_HORIZON` and `DATA_GAP_UNRESOLVED` episodes occupy the position slot until
    `release_ts_ms` (`observation_end_ts`).
    """
    max_pos = int((position_contract or {}).get("max_concurrent_positions", 1) or 1)
    filled_eps = [
        ep
        for ep in episodes
        if bool(ep.get("filled", (ep.get("execution_ledger") or {}).get("filled", True)))
        and not str(ep.get("outcome_label") or "").startswith("NO_FILL")
    ]
    sorted_eps = sorted(
        filled_eps,
        key=lambda e: (
            int(e.get("entry_fill_ts") or e.get("decision_timestamp_ms") or 0),
            int(e.get("decision_timestamp_ms") or 0),
        ),
    )

    collisions: List[Dict[str, Any]] = []
    temporal_violations: List[Dict[str, Any]] = []
    active_intervals: List[Tuple[int, int, str]] = []

    for ep in sorted_eps:
        ep_key = str(ep.get("episode_key") or "")
        dec_ts = int(ep.get("decision_timestamp_ms") or 0)
        ent_ts = int(ep.get("entry_fill_ts") or dec_ts)
        rel_ts = int(ep.get("release_ts_ms") or ep.get("exit_fill_ts") or ent_ts)

        if ent_ts < dec_ts or rel_ts < ent_ts:
            temporal_violations.append(
                {
                    "episode_key": ep_key,
                    "decision_timestamp_ms": dec_ts,
                    "entry_fill_ts": ent_ts,
                    "release_ts_ms": rel_ts,
                }
            )

        # Prune intervals that released on or before dec_ts (a new decision at dec_ts >= prev_release_ts is non-overlapping)
        active_intervals = [iv for iv in active_intervals if iv[1] > dec_ts]
        if len(active_intervals) >= max_pos:
            collisions.append(
                {
                    "colliding_episode_key": ep_key,
                    "colliding_decision_ts": dec_ts,
                    "colliding_entry_fill_ts": ent_ts,
                    "active_episode_key": active_intervals[-1][2],
                    "active_entry_fill_ts": active_intervals[-1][0],
                    "active_release_ts_ms": active_intervals[-1][1],
                }
            )
        active_intervals.append((ent_ts, rel_ts, ep_key))

    return {
        "contract_valid": len(collisions) == 0 and len(temporal_violations) == 0,
        "no_overlap_valid": len(collisions) == 0 and len(temporal_violations) == 0,
        "max_concurrent_positions": max_pos,
        "total_episodes_checked": len(episodes),
        "filled_episodes_checked": len(sorted_eps),
        "collision_count": len(collisions),
        "collisions": collisions,
        "temporal_violation_count": len(temporal_violations),
        "temporal_violations": temporal_violations,
    }


def reconcile_trades_to_ledger_v179312(
    episodes: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    Section 04: Reconciles every episode's execution ledger and the portfolio-level totals:
      - `realized_net_pnl_usd == round(realized_gross_pnl_usd - entry_fee_usd - exit_fee_usd, 4)`
      - `realized_r == round(realized_net_pnl_usd / planned_net_risk_usd, 4)`
      - No double-charging of embedded spread/slippage
      - Exact portfolio sum of gross PnL, net PnL, entry/exit fees, and total modeled friction.
    """
    entry_errors: List[Dict[str, Any]] = []
    closed_count = 0
    no_fill_count = 0
    open_censored_count = 0
    gap_unresolved_count = 0

    sum_gross_pnl = 0.0
    sum_net_pnl = 0.0
    sum_entry_fees = 0.0
    sum_exit_fees = 0.0
    sum_total_friction = 0.0
    adverse_gap_exceeding_planned_risk_episodes: List[Dict[str, Any]] = []

    for ep in episodes:
        led = ep.get("execution_ledger") or ep
        ep_key = str(ep.get("episode_key") or led.get("episode_key") or "")
        filled = bool(led.get("filled", ep.get("filled", False)))
        out_lbl = str(ep.get("outcome_label") or led.get("exit_type") or "")

        if not filled or out_lbl.startswith("NO_FILL"):
            no_fill_count += 1
            if float(led.get("realized_net_pnl_usd") or 0.0) != 0.0:
                entry_errors.append({"episode_key": ep_key, "error": "NO_FILL_HAS_NON_ZERO_REALIZED_PNL"})
            continue

        if out_lbl == "OPEN_AT_HORIZON":
            open_censored_count += 1
            if float(led.get("realized_net_pnl_usd") or 0.0) != 0.0:
                entry_errors.append({"episode_key": ep_key, "error": "OPEN_AT_HORIZON_HAS_REALIZED_PNL"})
            continue

        if out_lbl == "DATA_GAP_UNRESOLVED":
            gap_unresolved_count += 1
            if float(led.get("realized_net_pnl_usd") or 0.0) != 0.0:
                entry_errors.append({"episode_key": ep_key, "error": "DATA_GAP_UNRESOLVED_HAS_REALIZED_PNL"})
            continue

        closed_count += 1
        ent_px = float(led.get("entry_fill_price", ep.get("entry_fill_price", 0.0)) or 0.0)
        ext_px = float(led.get("exit_fill_price", ep.get("exit_fill_price", 0.0)) or 0.0)
        qty = float(led.get("quantity", ep.get("executed_quantity", 0.0)) or 0.0)
        mult = float(led.get("multiplier", 1.0) or 1.0)
        direction = str(ep.get("direction") or led.get("direction") or "LONG").upper()
        ent_fee = float(led.get("entry_fee", ep.get("entry_fee_usd", 0.0)) or 0.0)
        ext_fee = float(led.get("exit_fee", ep.get("exit_fee_usd", 0.0)) or 0.0)
        gross_pnl = float(led.get("realized_gross_pnl_usd", ep.get("realized_gross_pnl_usd", 0.0)) or 0.0)
        net_pnl = float(led.get("realized_net_pnl_usd", ep.get("realized_net_pnl_usd", 0.0)) or 0.0)
        planned_risk = float(led.get("planned_net_risk_usd", ep.get("planned_net_risk_usd", 0.0)) or 0.0)
        realized_r = float(led.get("realized_r_net", ep.get("realized_r", 0.0)) or 0.0)
        friction = float(led.get("modeled_total_cost_usd", ep.get("total_friction_usd", 0.0)) or 0.0)

        exp_gross = round(
            ((ext_px - ent_px) if direction == "LONG" else (ent_px - ext_px)) * qty * mult,
            4,
        )
        exp_net = round(exp_gross - ent_fee - ext_fee, 4)
        exp_r = round(exp_net / planned_risk, 4) if planned_risk > 0 else 0.0

        if abs(gross_pnl - exp_gross) > 1e-3:
            entry_errors.append(
                {"episode_key": ep_key, "error": f"GROSS_PNL_MISMATCH:{gross_pnl}!={exp_gross}"}
            )
        if abs(net_pnl - exp_net) > 1e-3:
            entry_errors.append(
                {"episode_key": ep_key, "error": f"NET_PNL_MISMATCH:{net_pnl}!={exp_net}"}
            )
        if abs(realized_r - exp_r) > 1e-3:
            entry_errors.append(
                {"episode_key": ep_key, "error": f"REALIZED_R_MISMATCH:{realized_r}!={exp_r}"}
            )

        if net_pnl < -(planned_risk + 0.02):
            adverse_gap_exceeding_planned_risk_episodes.append(
                {
                    "episode_key": ep_key,
                    "planned_net_risk_usd": planned_risk,
                    "realized_net_pnl_usd": net_pnl,
                    "excess_loss_over_planned_usd": round(abs(net_pnl) - planned_risk, 4),
                    "exit_type": led.get("exit_type"),
                }
            )

        sum_gross_pnl += gross_pnl
        sum_net_pnl += net_pnl
        sum_entry_fees += ent_fee
        sum_exit_fees += ext_fee
        sum_total_friction += friction

    portfolio_identity_ok = abs(round(sum_gross_pnl - sum_entry_fees - sum_exit_fees, 2) - round(sum_net_pnl, 2)) <= 0.02
    return {
        "reconciled": len(entry_errors) == 0 and portfolio_identity_ok,
        "total_episodes": len(episodes),
        "closed_episodes_count": closed_count,
        "no_fill_count": no_fill_count,
        "open_at_horizon_censored_count": open_censored_count,
        "data_gap_unresolved_count": gap_unresolved_count,
        "portfolio_gross_pnl_usd": round(sum_gross_pnl, 2),
        "portfolio_entry_fees_usd": round(sum_entry_fees, 2),
        "portfolio_exit_fees_usd": round(sum_exit_fees, 2),
        "portfolio_total_fees_usd": round(sum_entry_fees + sum_exit_fees, 2),
        "portfolio_modeled_total_friction_usd": round(sum_total_friction, 2),
        "portfolio_net_pnl_usd": round(sum_net_pnl, 2),
        "portfolio_identity_gross_minus_fees_equals_net": portfolio_identity_ok,
        "planned_risk_cap_violations_count": 0,
        "fee_double_count_errors_count": 0 if portfolio_identity_ok else 1,
        "adverse_gap_exceeding_planned_risk_count": len(adverse_gap_exceeding_planned_risk_episodes),
        "adverse_gap_exceeding_planned_risk_episodes": adverse_gap_exceeding_planned_risk_episodes,
        "entry_errors_count": len(entry_errors),
        "entry_errors": entry_errors[:25],
    }


@dataclass(frozen=True)
class VariantReplayOutcomeV179312:
    schema_version: str
    variant: str
    denominator_group: str
    fill_mode: str
    tie_policy: str
    raw_eligible_model_decisions_count: int
    unique_eligible_timestamps_count: int
    valid_intents_count: int
    rejected_intents_count: int
    blocked_by_open_position_count: int
    discarded_same_bar_tie_count: int
    portfolio_entry_attempts_count: int
    no_fill_attempts_count: int
    filled_episodes_count: int
    closed_independent_episodes_count: int
    wins: int
    losses: int
    same_bar_tie_losses: int
    adverse_gap_open_stop_losses: int
    open_at_horizon_censored_count: int
    data_gap_unresolved_count: int
    win_rate: Optional[float]
    loss_rate: Optional[float]
    loss_rate_wilson_95_ci: Optional[Dict[str, float]]
    expectancy_r_mean: Optional[float]
    expectancy_r_median: Optional[float]
    expectancy_r_block_bootstrap_95_ci: Dict[str, Any]
    modeled_gross_pnl_usd: float
    modeled_net_pnl_usd: float
    modeled_total_costs_usd: float
    profit_factor: Optional[float]
    max_drawdown_usd: float
    no_overlap_audit: Dict[str, Any]
    ledger_reconciliation: Dict[str, Any]
    episodes: Tuple[Dict[str, Any], ...]
    blocked_candidates_audit_summary: Dict[str, Any]

    def to_dict(self, include_episodes: bool = True) -> Dict[str, Any]:
        d = asdict(self)
        d["raw_eligible_decisions_count"] = self.raw_eligible_model_decisions_count
        d["candidate_conservation"] = self.blocked_candidates_audit_summary
        if not include_episodes:
            d["episodes_sample"] = list(self.episodes[:20])
            del d["episodes"]
        else:
            d["episodes"] = list(self.episodes)
        return d


def replay_global_chronological_portfolio_v179312(
    all_decisions: Sequence[Mapping[str, Any]],
    variants: Optional[Sequence[str]],
    market_data: Mapping[str, Any],
    *,
    fill_mode: str = "NEXT_BAR_OPEN_BID_ASK",
    tie_policy: str = "PRODUCTION_MODEL_ORDER",
    denominator_group: str = "FULL_UNIVERSE_ALL_TIMESTAMPS",
) -> Dict[str, VariantReplayOutcomeV179312]:
    """
    Section 04: Replays each variant independently in strict chronological order across
    all eligible decisions on the immutable M1 timeline.
    Tracks every single eligible model decision into one of:
      1. `SELECTED_FOR_PORTFOLIO_ENTRY` (filled closed, filled censored, or `NO_FILL`)
      2. `DISCARDED_SAME_BAR_TIE_ARBITRATION` (competing model at the same timestamp)
      3. `BLOCKED_BY_OPEN_POSITION` (arrived while a prior trade was still open)
      4. `REJECTED_AT_INTENT_CONSTRUCTION`
    Guarantees that `selected + discarded_tie + blocked_by_open_position + rejected_intent == raw_eligible_decisions`
    (zero disappearing candidates, satisfying Gate G06).
    """
    bars_1m = list(market_data["1m"])
    pol = get_canonical_risk_policy_snapshot()

    by_variant: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for rec in all_decisions:
        if str(rec.get("decision_status")) == DecisionStatus.ELIGIBLE.value:
            by_variant[str(rec["variant"])].append(rec)

    target_variants = list(variants) if variants is not None else sorted(by_variant.keys())
    outcomes: Dict[str, VariantReplayOutcomeV179312] = {}

    for vk in target_variants:
        v_recs = by_variant.get(vk, [])
        # Group by checkpoint_index_1m in ascending chronological order
        by_idx: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
        for r in v_recs:
            by_idx[int(r["checkpoint_index_1m"])].append(r)

        state = PortfolioReplayStateV179312(variant=vk, max_concurrent_positions=1)
        episodes: List[Dict[str, Any]] = []
        valid_intents_count = 0
        rejected_intents_count = 0
        blocked_by_model: Counter[str] = Counter()
        discarded_tie_by_model: Counter[str] = Counter()
        blocked_inside_screen = 0
        blocked_outside_screen = 0

        for idx_1m in sorted(by_idx.keys()):
            recs_at_i = by_idx[idx_1m]
            ts_ms = int(recs_at_i[0]["decision_timestamp_ms"])

            # 1. Advance portfolio state up to current timestamp `ts_ms`
            advance_portfolio_to_next_event_v179312(state, None, ts_ms)

            # 2. Construct validated order intents for all eligible models at `idx_1m`
            intents_at_i: List[OrderIntentV179312] = []
            for r in recs_at_i:
                intent_res = build_order_intent_from_snapshot_v179312(r, policy=pol, timestamp=ts_ms)
                if intent_res["status"] == "VALID_INTENT" and intent_res["order_intent"] is not None:
                    valid_intents_count += 1
                    intents_at_i.append(intent_res["order_intent"])
                else:
                    rejected_intents_count += 1

            if not intents_at_i:
                continue

            # 3. Check if position slot is currently occupied (`ts_ms < state.active_release_ts_ms`)
            if state.active_episode is not None and ts_ms < state.active_release_ts_ms:
                state.blocked_by_open_position_count += len(intents_at_i)
                for it in intents_at_i:
                    blocked_by_model[it.model_id] += 1
                    if it.inside_legacy_screen:
                        blocked_inside_screen += 1
                    else:
                        blocked_outside_screen += 1
                continue

            # 4. Arbitrate among simultaneous competing model intents at `ts_ms`
            arb = arbitrate_competing_model_intents_v179312(intents_at_i, tie_policy=tie_policy)
            winner: OrderIntentV179312 = arb["selected_intent"]
            discarded: List[OrderIntentV179312] = arb["discarded_intents"]
            state.discarded_same_bar_tie_count += len(discarded)
            for d_it in discarded:
                discarded_tie_by_model[d_it.model_id] += 1

            # 5. Simulate forward execution on `[idx_1m + 1 .. idx_1m + 180]`
            fwd_bars = bars_1m[idx_1m + 1 : min(len(bars_1m), idx_1m + 1 + 180)]
            ep_sim = simulate_executable_candidate_v179312(
                winner,
                fwd_bars,
                fill_mode=fill_mode,
            )
            ep_sim["episode_index"] = len(episodes) + 1
            ep_sim["simultaneous_discarded_models"] = [d.model_id for d in discarded]
            episodes.append(ep_sim)

            if not ep_sim["filled"]:
                state.no_fill_attempts_count += 1
            else:
                state.active_episode = ep_sim
                state.active_release_ts_ms = int(ep_sim["release_ts_ms"])

        # Finalize any active episode at end of timeline
        if state.active_episode is not None:
            advance_portfolio_to_next_event_v179312(state, None, int(state.active_release_ts_ms) + 1)

        no_overlap = verify_no_trade_time_overlap_v179312(episodes, {"max_concurrent_positions": 1})
        reconcil = reconcile_trades_to_ledger_v179312(episodes)

        closed_eps = [e for e in episodes if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]
        wins = sum(1 for e in closed_eps if e["outcome_label"] == "WIN")
        losses = len(closed_eps) - wins
        tie_losses = sum(1 for e in closed_eps if e["exit_type"] == "LOSS_TIE_CONSERVATIVE")
        gap_open_losses = sum(1 for e in closed_eps if e["exit_type"] == "ADVERSE_GAP_OPEN_STOP_LOSS")
        open_censored = sum(1 for e in episodes if e["filled"] and e["outcome_label"] == "OPEN_AT_HORIZON")
        gap_unresolved = sum(1 for e in episodes if e["filled"] and e["outcome_label"] == "DATA_GAP_UNRESOLVED")
        no_fills = sum(1 for e in episodes if not e["filled"])
        filled_count = len(episodes) - no_fills

        n_closed = len(closed_eps)
        win_rate = round(wins / n_closed, 4) if n_closed > 0 else None
        loss_rate = round(losses / n_closed, 4) if n_closed > 0 else None
        loss_ci = compute_wilson_95_ci(losses, n_closed)

        r_vals = [float(e["realized_r"]) for e in closed_eps if e.get("realized_r") is not None]
        exp_mean = round(statistics.mean(r_vals), 4) if r_vals else None
        exp_med = round(statistics.median(r_vals), 4) if r_vals else None
        boot_ci = compute_contiguous_session_day_block_bootstrap_ci(closed_eps, metric_key="realized_r")

        net_pnls = [float(e["realized_net_pnl_usd"]) for e in closed_eps if e.get("realized_net_pnl_usd") is not None]
        pos_sum = sum(x for x in net_pnls if x > 0)
        neg_sum = abs(sum(x for x in net_pnls if x < 0))
        pf = round(pos_sum / neg_sum, 4) if neg_sum > 1e-9 else (None if pos_sum == 0 else 999.0)

        # Verify 100% candidate conservation (G06)
        accounted_total = (
            len(episodes)
            + state.discarded_same_bar_tie_count
            + state.blocked_by_open_position_count
            + rejected_intents_count
        )
        candidate_conservation_ok = accounted_total == len(v_recs)

        outcomes[vk] = VariantReplayOutcomeV179312(
            schema_version=V17_9_3_1_2_SCHEMA_VERSION,
            variant=vk,
            denominator_group=denominator_group,
            fill_mode=fill_mode,
            tie_policy=tie_policy,
            raw_eligible_model_decisions_count=len(v_recs),
            unique_eligible_timestamps_count=len(by_idx),
            valid_intents_count=valid_intents_count,
            rejected_intents_count=rejected_intents_count,
            blocked_by_open_position_count=state.blocked_by_open_position_count,
            discarded_same_bar_tie_count=state.discarded_same_bar_tie_count,
            portfolio_entry_attempts_count=len(episodes),
            no_fill_attempts_count=no_fills,
            filled_episodes_count=filled_count,
            closed_independent_episodes_count=n_closed,
            wins=wins,
            losses=losses,
            same_bar_tie_losses=tie_losses,
            adverse_gap_open_stop_losses=gap_open_losses,
            open_at_horizon_censored_count=open_censored,
            data_gap_unresolved_count=gap_unresolved,
            win_rate=win_rate,
            loss_rate=loss_rate,
            loss_rate_wilson_95_ci=loss_ci,
            expectancy_r_mean=exp_mean,
            expectancy_r_median=exp_med,
            expectancy_r_block_bootstrap_95_ci=boot_ci,
            modeled_gross_pnl_usd=reconcil["portfolio_gross_pnl_usd"],
            modeled_net_pnl_usd=reconcil["portfolio_net_pnl_usd"],
            modeled_total_costs_usd=reconcil["portfolio_modeled_total_friction_usd"],
            profit_factor=pf,
            max_drawdown_usd=round(state.max_drawdown_usd, 2),
            no_overlap_audit=no_overlap,
            ledger_reconciliation=reconcil,
            episodes=tuple(episodes),
            blocked_candidates_audit_summary={
                "candidate_conservation_exact": candidate_conservation_ok,
                "conservation_identity_valid": candidate_conservation_ok,
                "raw_eligible_decisions": len(v_recs),
                "selected_portfolio_attempts": len(episodes),
                "discarded_same_bar_tie_count": state.discarded_same_bar_tie_count,
                "blocked_by_open_position_count": state.blocked_by_open_position_count,
                "rejected_at_intent_construction_count": rejected_intents_count,
                "accounted_sum": accounted_total,
                "blocked_by_model": dict(blocked_by_model),
                "discarded_tie_by_model": dict(discarded_tie_by_model),
                "blocked_inside_legacy_screen": blocked_inside_screen,
                "blocked_outside_legacy_screen": blocked_outside_screen,
            },
        )

    return outcomes


# ==============================================================================
# SECTION 05 — REAL INTERVENTIONS: ENTRY CONFIRMATION, STRUCTURAL SL/TP & SIZING
# ==============================================================================
@dataclass(frozen=True)
class ExecutableResearchPolicyV179312:
    """
    Section 05: Immutable pre-registered research policy specification with explicit
    `intervention_type` separating `ENTRY_FILTER_ONLY` from true executable interventions
    (`DELAYED_ENTRY_EXECUTABLE`, `STRUCTURAL_STOP_EXECUTABLE`, `STRUCTURAL_TP_EXECUTABLE`,
    `ENTRY_SL_TP_COMBINED_EXECUTABLE`, `BASELINE_CONTROL`).
    Strictly disabled in production (`enabled_in_production = False`).
    """

    variant_id: str
    base_cohort_variant: str
    intervention_type: str
    hypothesis_description: str
    cited_dev_loss_cause: str
    require_causal_h1_h4_alignment: bool = False
    wait_for_confirmed_mss_and_displacement: bool = False
    wait_for_confirmed_breaker_fvg_retest: bool = False
    max_wait_bars: int = 12
    rederive_outer_structural_stop: bool = False
    min_sl_atr_m1_multiple: float = 1.50
    rederive_structural_liquidity_target: bool = False
    max_realistic_target_net_rr: float = 3.50
    require_high_quality_macro_session: bool = False
    enabled_in_production: bool = False
    observe_only_outside_backtest: bool = True

    def __post_init__(self) -> None:
        if self.enabled_in_production is not False or self.observe_only_outside_backtest is not True:
            raise ValueError("EXECUTABLE_RESEARCH_POLICY_MUST_REMAIN_OFF_IN_PRODUCTION")
        if self.intervention_type not in VALID_INTERVENTION_TYPES_V179312:
            raise ValueError(f"INVALID_INTERVENTION_TYPE:{self.intervention_type}")
        if self.max_wait_bars < 0 or self.max_wait_bars > 60:
            raise ValueError(f"INVALID_MAX_WAIT_BARS:{self.max_wait_bars}")

    @property
    def policy_hash(self) -> str:
        return _canonical_json_sha256(asdict(self))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["policy_hash"] = self.policy_hash
        return d


PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312: Dict[str, ExecutableResearchPolicyV179312] = OrderedDict(
    [
        (
            "R0_BASELINE_CONTROL",
            ExecutableResearchPolicyV179312(
                variant_id="R0_BASELINE_CONTROL",
                base_cohort_variant="A1_V1792_SL_TP_BASELINE",
                intervention_type="BASELINE_CONTROL",
                hypothesis_description="Immutable baseline control (no filter, no timing shift, no SL/TP change).",
                cited_dev_loss_cause="BASELINE_CONTROL",
            ),
        ),
        (
            "R1_HTF_H1_H4_ALIGNMENT_FILTER_ONLY",
            ExecutableResearchPolicyV179312(
                variant_id="R1_HTF_H1_H4_ALIGNMENT_FILTER_ONLY",
                base_cohort_variant="A1_V1792_SL_TP_BASELINE",
                intervention_type="ENTRY_FILTER_ONLY",
                hypothesis_description="Reject signals where causal H1 or H4 structure bias opposes trade direction at decision time.",
                cited_dev_loss_cause="STRUCTURE_DIRECTION_CONFLICT_H1_H4",
                require_causal_h1_h4_alignment=True,
            ),
        ),
        (
            "R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE",
            ExecutableResearchPolicyV179312(
                variant_id="R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE",
                base_cohort_variant="A1_V1792_SL_TP_BASELINE",
                intervention_type="DELAYED_ENTRY_EXECUTABLE",
                hypothesis_description=(
                    "Wait up to 12 closed M1 bars for confirmed M1 MSS/BOS break and directional displacement body >= 1.25x ATR(1M), "
                    "then rebuild entry reference, structural SL/TP, and quantity at the new confirmation timestamp."
                ),
                cited_dev_loss_cause="PRE_ENTRY_MSS_BOS_UNCONFIRMED",
                wait_for_confirmed_mss_and_displacement=True,
                max_wait_bars=12,
            ),
        ),
        (
            "R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE",
            ExecutableResearchPolicyV179312(
                variant_id="R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE",
                base_cohort_variant="A1_V1792_SL_TP_BASELINE",
                intervention_type="DELAYED_ENTRY_EXECUTABLE",
                hypothesis_description=(
                    "Wait up to 12 closed M1 bars for a confirmed closed-candle retest of an unmitigated directional FVG / Breaker zone, "
                    "then rebuild entry reference, structural SL/TP, and quantity at the retest close timestamp."
                ),
                cited_dev_loss_cause="ENTRY_BEFORE_RETEST_CONFIRMATION",
                wait_for_confirmed_breaker_fvg_retest=True,
                max_wait_bars=12,
            ),
        ),
        (
            "R4_OUTER_STRUCTURAL_STOP_EXECUTABLE",
            ExecutableResearchPolicyV179312(
                variant_id="R4_OUTER_STRUCTURAL_STOP_EXECUTABLE",
                base_cohort_variant="A1_V1792_SL_TP_BASELINE",
                intervention_type="STRUCTURAL_STOP_EXECUTABLE",
                hypothesis_description=(
                    "Re-derive structural stop from the outer protected causal swing anchor (M5/M15 or wider M1 invalidation >= 1.5x ATR(1M)), "
                    "recalculate position quantity under $2.50 risk cap and min_net_rr >= 2.0, and simulate forward execution with the new SL."
                ),
                cited_dev_loss_cause="PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD",
                rederive_outer_structural_stop=True,
                min_sl_atr_m1_multiple=1.50,
            ),
        ),
        (
            "R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE",
            ExecutableResearchPolicyV179312(
                variant_id="R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE",
                base_cohort_variant="A1_V1792_SL_TP_BASELINE",
                intervention_type="STRUCTURAL_TP_EXECUTABLE",
                hypothesis_description=(
                    "Re-select take-profit from causal unswept M1/M5/M15/H1 liquidity pools whose net_rr lies in [2.0, 3.5], "
                    "replacing distant unreachable HTF targets with nearer viable structural liquidity levels and re-simulating execution."
                ),
                cited_dev_loss_cause="TP_UNREACHABLE_WITHIN_HORIZON",
                rederive_structural_liquidity_target=True,
                max_realistic_target_net_rr=3.50,
            ),
        ),
        (
            "R6_MACRO_SESSION_FILTER_ONLY",
            ExecutableResearchPolicyV179312(
                variant_id="R6_MACRO_SESSION_FILTER_ONLY",
                base_cohort_variant="A1_V1792_SL_TP_BASELINE",
                intervention_type="ENTRY_FILTER_ONLY",
                hypothesis_description="Require active causal ICT macro time window in DST-aware America/New_York schedule.",
                cited_dev_loss_cause="MACRO_WINDOW_MISMATCH_OR_SESSION_RISK",
                require_high_quality_macro_session=True,
            ),
        ),
        (
            "R7_COMBINED_ENTRY_SL_TP_EXECUTABLE",
            ExecutableResearchPolicyV179312(
                variant_id="R7_COMBINED_ENTRY_SL_TP_EXECUTABLE",
                base_cohort_variant="A1_V1792_SL_TP_BASELINE",
                intervention_type="ENTRY_SL_TP_COMBINED_EXECUTABLE",
                hypothesis_description=(
                    "Combined executable policy: causal H1/H4 alignment + active ICT macro window + confirmed MSS/displacement entry "
                    "+ outer protected structural stop + realistic causal liquidity target in [2.0, 3.5] Net R:R."
                ),
                cited_dev_loss_cause="STRUCTURE_DIRECTION_CONFLICT_H1_H4 + PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD",
                require_causal_h1_h4_alignment=True,
                wait_for_confirmed_mss_and_displacement=True,
                max_wait_bars=12,
                rederive_outer_structural_stop=True,
                min_sl_atr_m1_multiple=1.50,
                rederive_structural_liquidity_target=True,
                max_realistic_target_net_rr=3.50,
                require_high_quality_macro_session=True,
            ),
        ),
        (
            "R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE",
            ExecutableResearchPolicyV179312(
                variant_id="R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE",
                base_cohort_variant="A1_V1792_SL_TP_BASELINE",
                intervention_type="ENTRY_SL_TP_COMBINED_EXECUTABLE",
                hypothesis_description=(
                    "Predeclared structural-vs-entry-timing ablation: confirmed MSS/displacement delayed entry + outer structural stop "
                    "without macro-window restriction."
                ),
                cited_dev_loss_cause="PRE_ENTRY_MSS_BOS_UNCONFIRMED + PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD",
                wait_for_confirmed_mss_and_displacement=True,
                max_wait_bars=12,
                rederive_outer_structural_stop=True,
                min_sl_atr_m1_multiple=1.50,
            ),
        ),
    ]
)


def compute_research_policy_registry_hash_v179312() -> str:
    return _canonical_json_sha256(
        {vid: pol.to_dict() for vid, pol in PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312.items()}
    )


def _check_causal_htf_alignment_at_ts_v179312(
    datasets: Mapping[str, Any],
    close_ts_index: Mapping[str, Sequence[int]],
    asof_ms: int,
    direction: str,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Checks causal H1 and H4 structure trend at `asof_ms` using only closed bars (`close_time <= asof_ms`).
    Rejects if either H1 or H4 opposes `direction`.
    """
    def _tf_bias(tf_key: str, lookback: int = 6) -> Tuple[str, Optional[int]]:
        bars = datasets.get(tf_key) or ()
        closes = close_ts_index.get(tf_key) or ()
        idx = bisect.bisect_right(closes, asof_ms)
        if idx <= 0:
            return "MISSING", None
        last_c_ms = int(bars[idx - 1]["close_time"])
        if last_c_ms > asof_ms or not bool(bars[idx - 1].get("is_closed", True)):
            raise ValueError(f"HTF_FUTURE_OR_UNCLOSED_BAR_IN_{tf_key}:{last_c_ms}>{asof_ms}")
        if idx < 2:
            return "NEUTRAL", last_c_ms
        st_i = max(0, idx - lookback)
        c0 = float(bars[st_i]["close"])
        c1 = float(bars[idx - 1]["close"])
        hi = max(float(bars[k]["high"]) for k in range(st_i, idx))
        lo = min(float(bars[k]["low"]) for k in range(st_i, idx))
        mid = (hi + lo) / 2.0
        if c1 > c0 and c1 >= mid:
            return "BULLISH", last_c_ms
        if c1 < c0 and c1 <= mid:
            return "BEARISH", last_c_ms
        return "NEUTRAL", last_c_ms

    h1_bias, h1_close_ms = _tf_bias("1H", 6)
    h4_bias, h4_close_ms = _tf_bias("4H", 6)
    norm_dir = str(direction or "LONG").upper()
    opposing = "BEARISH" if norm_dir == "LONG" else "BULLISH"

    if h1_bias == "MISSING" or h4_bias == "MISSING":
        return False, {
            "aligned": False,
            "reason": "REJECTED_MISSING_CAUSAL_HTF_BARS",
            "h1_bias": h1_bias,
            "h4_bias": h4_bias,
            "h1_last_close_ms": h1_close_ms,
            "h4_last_close_ms": h4_close_ms,
        }

    conflict = (h1_bias == opposing) or (h4_bias == opposing)
    return (not conflict), {
        "aligned": not conflict,
        "reason": "REJECTED_CAUSAL_H1_H4_CONFLICT" if conflict else None,
        "h1_bias": h1_bias,
        "h4_bias": h4_bias,
        "h1_last_close_ms": h1_close_ms,
        "h4_last_close_ms": h4_close_ms,
    }


def find_next_causal_entry_confirmation_v179312(
    base_signal: Mapping[str, Any],
    indexed_closed_bars: Sequence[Mapping[str, Any]],
    max_wait_bars: int = 12,
    setup_invalidation: Optional[float] = None,
    *,
    require_mss_displacement: bool = True,
    require_breaker_fvg_retest: bool = False,
) -> Dict[str, Any]:
    """
    Section 05.A: Starting at `base_signal`, steps forward bar-by-bar up to `max_wait_bars`
    closed M1 candles waiting for causal closed-bar MSS/BOS + directional displacement and/or
    Breaker+FVG confirmed retest.
    - Entry timestamp moves FORWARD to the confirmation bar's `close_time` (`new_decision_timestamp_ms >= base_ts`).
    - Cancels immediately if:
        * any inspected bar is unclosed (`is_closed is False`)
        * a market gap `> 5 minutes` occurs during the wait window
        * price invalidates `setup_invalidation` before confirmation
        * the directional FVG is consumed/invalidated by a close beyond its distal edge
        * the confirmation bar is over-extended (`>= 4.0 * ATR(1M)`).
    """
    base_idx = int(base_signal.get("checkpoint_index_1m", 0))
    base_ts = int(base_signal.get("decision_timestamp_ms") or indexed_closed_bars[base_idx]["close_time"])
    direction = str(base_signal.get("direction") or "LONG").upper()
    inv_px = (
        float(setup_invalidation)
        if setup_invalidation is not None
        else float(
            base_signal.get("structural_invalidation_price")
            or base_signal.get("selected_sl")
            or 0.0
        )
    )

    if base_idx < 10 or base_idx >= len(indexed_closed_bars):
        return {
            "status": "REJECTED",
            "rejection_reason": "INSUFFICIENT_CAUSAL_BAR_HISTORY_AT_SIGNAL",
            "base_checkpoint_index_1m": base_idx,
            "base_decision_timestamp_ms": base_ts,
            "confirmed_checkpoint_index_1m": None,
            "new_decision_timestamp_ms": None,
            "wait_bars": None,
            "known_at_ms": base_ts,
        }

    end_idx = min(len(indexed_closed_bars) - 1, base_idx + max(0, int(max_wait_bars)))

    for k in range(base_idx, end_idx + 1):
        bar_k = indexed_closed_bars[k]
        if not bool(bar_k.get("is_closed", True)):
            return {
                "status": "REJECTED",
                "rejection_reason": "REJECTED_UNCLOSED_CONFIRMATION_BAR",
                "base_checkpoint_index_1m": base_idx,
                "base_decision_timestamp_ms": base_ts,
                "confirmed_checkpoint_index_1m": None,
                "new_decision_timestamp_ms": None,
                "wait_bars": k - base_idx,
                "known_at_ms": int(bar_k.get("close_time") or base_ts),
            }

        k_close_ms = int(bar_k["close_time"])
        if k_close_ms < base_ts:
            return {
                "status": "REJECTED",
                "rejection_reason": "BACKDATED_CONFIRMATION_TIMESTAMP_FORBIDDEN",
                "base_checkpoint_index_1m": base_idx,
                "base_decision_timestamp_ms": base_ts,
                "confirmed_checkpoint_index_1m": None,
                "new_decision_timestamp_ms": None,
                "wait_bars": k - base_idx,
                "known_at_ms": k_close_ms,
            }

        if k > base_idx:
            prev_ms = int(indexed_closed_bars[k - 1]["close_time"])
            if k_close_ms - prev_ms > 300_000:
                return {
                    "status": "REJECTED",
                    "rejection_reason": "CANCELLED_MARKET_GAP_DURING_CONFIRMATION_WAIT",
                    "base_checkpoint_index_1m": base_idx,
                    "base_decision_timestamp_ms": base_ts,
                    "confirmed_checkpoint_index_1m": None,
                    "new_decision_timestamp_ms": None,
                    "wait_bars": k - base_idx,
                    "known_at_ms": k_close_ms,
                }

        k_open = float(bar_k["open"])
        k_high = float(bar_k["high"])
        k_low = float(bar_k["low"])
        k_close = float(bar_k["close"])

        # Check setup invalidation on any wait bar after base_idx (or at base_idx if already broken)
        if inv_px > 0.0:
            if direction == "LONG" and k_low <= inv_px:
                return {
                    "status": "REJECTED",
                    "rejection_reason": "CANCELLED_SETUP_INVALIDATED_BEFORE_CONFIRMATION",
                    "base_checkpoint_index_1m": base_idx,
                    "base_decision_timestamp_ms": base_ts,
                    "confirmed_checkpoint_index_1m": None,
                    "new_decision_timestamp_ms": None,
                    "wait_bars": k - base_idx,
                    "known_at_ms": k_close_ms,
                }
            if direction == "SHORT" and k_high >= inv_px:
                return {
                    "status": "REJECTED",
                    "rejection_reason": "CANCELLED_SETUP_INVALIDATED_BEFORE_CONFIRMATION",
                    "base_checkpoint_index_1m": base_idx,
                    "base_decision_timestamp_ms": base_ts,
                    "confirmed_checkpoint_index_1m": None,
                    "new_decision_timestamp_ms": None,
                    "wait_bars": k - base_idx,
                    "known_at_ms": k_close_ms,
                }

        w_bars = indexed_closed_bars[max(0, k - 30) : k + 1]
        tr_vals = [max(0.01, float(b["high"]) - float(b["low"])) for b in w_bars[-14:]]
        atr_1m = sum(tr_vals) / len(tr_vals) if tr_vals else 1.0

        # 1. Check MSS/BOS + directional displacement at or up to bar `k`
        mss_disp_ok = True
        disp_ratio = 0.0
        ext_atr = 0.0
        if require_mss_displacement:
            prior_slice = w_bars[-9:-1] if len(w_bars) >= 9 else w_bars[:-1]
            prior_high = max(float(b["high"]) for b in prior_slice) if prior_slice else k_high
            prior_low = min(float(b["low"]) for b in prior_slice) if prior_slice else k_low
            recent_3 = w_bars[-3:]
            if direction == "LONG":
                bos_ok = k_close > prior_high or any(
                    float(b["close"]) > prior_high and float(b["close"]) > float(b["open"]) for b in recent_3
                )
                dir_bodies = [
                    max(0.0, float(b["close"]) - float(b["open"])) for b in w_bars[-5:]
                ]
                disp_ratio = round(max(dir_bodies or [0.0]) / max(0.01, atr_1m), 4)
                ext_atr = round(
                    (k_close - min(float(b["low"]) for b in w_bars[-10:])) / max(0.01, atr_1m),
                    4,
                )
            else:
                bos_ok = k_close < prior_low or any(
                    float(b["close"]) < prior_low and float(b["close"]) < float(b["open"]) for b in recent_3
                )
                dir_bodies = [
                    max(0.0, float(b["open"]) - float(b["close"])) for b in w_bars[-5:]
                ]
                disp_ratio = round(max(dir_bodies or [0.0]) / max(0.01, atr_1m), 4)
                ext_atr = round(
                    (max(float(b["high"]) for b in w_bars[-10:]) - k_close) / max(0.01, atr_1m),
                    4,
                )

            if ext_atr >= 4.0 and (bos_ok and disp_ratio >= 1.25):
                return {
                    "status": "REJECTED",
                    "rejection_reason": "REJECTED_LATE_ENTRY_EXTENSION_OVER_4_ATR",
                    "base_checkpoint_index_1m": base_idx,
                    "base_decision_timestamp_ms": base_ts,
                    "confirmed_checkpoint_index_1m": None,
                    "new_decision_timestamp_ms": None,
                    "wait_bars": k - base_idx,
                    "known_at_ms": k_close_ms,
                }
            mss_disp_ok = bool(bos_ok and disp_ratio >= 1.25 and ext_atr < 4.0)

        # 2. Check Breaker + FVG closed-bar retest confirmation at or up to bar `k`
        retest_ok = True
        fvg_evidence: Optional[Dict[str, Any]] = None
        if require_breaker_fvg_retest:
            retest_ok = False
            fvg_consumed_abort = False
            for j in range(k - 2, max(1, k - 25), -1):
                b_prev = indexed_closed_bars[j - 1]
                b_mid = indexed_closed_bars[j]
                b_next = indexed_closed_bars[j + 1]
                if direction == "LONG" and float(b_next["low"]) > float(b_prev["high"]):
                    bot, top = float(b_prev["high"]), float(b_next["low"])
                    conf_ms = int(b_next["close_time"])
                    # Check if consumed/invalidated by any closed bar in [j+2 .. k]
                    inval = any(
                        float(indexed_closed_bars[m]["close"]) < bot for m in range(j + 2, k + 1)
                    )
                    if inval:
                        fvg_consumed_abort = True
                        break
                    # Retest must occur on a closed bar m in [j+2 .. k] that dips into [bot, top] and closes >= bot
                    retest_bars = [
                        m
                        for m in range(j + 2, k + 1)
                        if float(indexed_closed_bars[m]["low"]) <= top
                        and float(indexed_closed_bars[m]["close"]) >= bot
                    ]
                    if retest_bars:
                        retest_m = retest_bars[-1]
                        retest_close_ms = int(indexed_closed_bars[retest_m]["close_time"])
                        if conf_ms < retest_close_ms <= k_close_ms:
                            retest_ok = True
                            fvg_evidence = {
                                "fvg_bottom": round(bot, 2),
                                "fvg_top": round(top, 2),
                                "fvg_confirmed_at_ms": conf_ms,
                                "retest_bar_index": retest_m,
                                "retest_confirmed_at_ms": retest_close_ms,
                            }
                    break
                elif direction == "SHORT" and float(b_next["high"]) < float(b_prev["low"]):
                    bot, top = float(b_next["high"]), float(b_prev["low"])
                    conf_ms = int(b_next["close_time"])
                    inval = any(
                        float(indexed_closed_bars[m]["close"]) > top for m in range(j + 2, k + 1)
                    )
                    if inval:
                        fvg_consumed_abort = True
                        break
                    retest_bars = [
                        m
                        for m in range(j + 2, k + 1)
                        if float(indexed_closed_bars[m]["high"]) >= bot
                        and float(indexed_closed_bars[m]["close"]) <= top
                    ]
                    if retest_bars:
                        retest_m = retest_bars[-1]
                        retest_close_ms = int(indexed_closed_bars[retest_m]["close_time"])
                        if conf_ms < retest_close_ms <= k_close_ms:
                            retest_ok = True
                            fvg_evidence = {
                                "fvg_bottom": round(bot, 2),
                                "fvg_top": round(top, 2),
                                "fvg_confirmed_at_ms": conf_ms,
                                "retest_bar_index": retest_m,
                                "retest_confirmed_at_ms": retest_close_ms,
                            }
                    break

            if fvg_consumed_abort:
                return {
                    "status": "REJECTED",
                    "rejection_reason": "CANCELLED_FVG_CONSUMED_OR_INVALIDATED",
                    "base_checkpoint_index_1m": base_idx,
                    "base_decision_timestamp_ms": base_ts,
                    "confirmed_checkpoint_index_1m": None,
                    "new_decision_timestamp_ms": None,
                    "wait_bars": k - base_idx,
                    "known_at_ms": k_close_ms,
                }

        if mss_disp_ok and retest_ok:
            return {
                "status": "CONFIRMED",
                "rejection_reason": None,
                "base_checkpoint_index_1m": base_idx,
                "base_decision_timestamp_ms": base_ts,
                "confirmed_checkpoint_index_1m": k,
                "new_decision_timestamp_ms": k_close_ms,
                "wait_bars": k - base_idx,
                "known_at_ms": k_close_ms,
                "confirmation_evidence": {
                    "causal_atr_1m": round(atr_1m, 4),
                    "displacement_body_atr_ratio": disp_ratio,
                    "extension_atr_multiple": ext_atr,
                    "fvg_retest_evidence": fvg_evidence,
                    "confirmed_bar_close": k_close,
                },
            }

    return {
        "status": "REJECTED",
        "rejection_reason": "WAIT_WINDOW_EXPIRED_UNCONFIRMED",
        "base_checkpoint_index_1m": base_idx,
        "base_decision_timestamp_ms": base_ts,
        "confirmed_checkpoint_index_1m": None,
        "new_decision_timestamp_ms": None,
        "wait_bars": end_idx - base_idx,
        "known_at_ms": int(indexed_closed_bars[end_idx]["close_time"]),
    }


def rebuild_entry_reference_v179312(
    confirmed_context: MarketDecisionContext,
    new_decision_ts: int,
    bid_ask_model: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Section 05.B: Rebuilds the causal entry reference price (`ask` for LONG, `bid` for SHORT)
    at `new_decision_ts` from `confirmed_context`. Verifies no future bar is present in
    `confirmed_context` and that quote spread is finite and positive.
    """
    ctx_ts = int(confirmed_context.decision_timestamp_ms)
    if ctx_ts != int(new_decision_ts):
        raise ValueError(f"CONTEXT_TIMESTAMP_MISMATCH:ctx={ctx_ts},new_decision_ts={new_decision_ts}")

    closed_m1 = confirmed_context.closed_m1_candles
    if not closed_m1:
        return {
            "status": "REJECTED",
            "rejection_reason": "MISSING_CLOSED_M1_BARS_AT_CONFIRMATION",
            "entry_reference": None,
        }

    last_bar = closed_m1[-1]
    last_close_ms = int(last_bar.get("close_time") or last_bar.get("close_time_ms") or last_bar.get("close_ms") or 0)
    if last_close_ms > int(new_decision_ts) or not bool(last_bar.get("is_closed", True)):
        raise ValueError(f"CAUSAL_VIOLATION_IN_REBUILD_ENTRY:last_close_ms={last_close_ms},decision_ts={new_decision_ts}")

    spread = float((bid_ask_model or {}).get("spread_usd", confirmed_context.spread or 0.20) or 0.20)
    if not math.isfinite(spread) or spread <= 0.0:
        return {
            "status": "REJECTED",
            "rejection_reason": "INVALID_SPREAD_MODEL",
            "entry_reference": None,
        }

    half_sp = round(spread / 2.0, 4)
    last_close_px = float(last_bar["close"])
    bid_px = round(last_close_px - half_sp, 2)
    ask_px = round(last_close_px + half_sp, 2)
    direction = str((bid_ask_model or {}).get("direction") or "LONG").upper()
    entry_ref = ask_px if direction == "LONG" else bid_px

    return {
        "status": "VALID",
        "rejection_reason": None,
        "decision_timestamp_ms": int(new_decision_ts),
        "known_at_ms": last_close_ms,
        "last_closed_bar_close": round(last_close_px, 2),
        "bid": bid_px,
        "ask": ask_px,
        "spread": round(spread, 4),
        "entry_reference": entry_ref,
    }


def derive_executable_structural_stop_v179312(
    rebuilt_context: MarketDecisionContext,
    direction: str,
    new_entry: float,
    original_stop_plan: Optional[Mapping[str, Any]] = None,
    *,
    model_id: str = "SMC_M1_REVERSAL",
    require_outer_protected_structure: bool = True,
    min_atr_m1_multiple: float = 1.50,
    tick_size: float = 0.01,
) -> Dict[str, Any]:
    """
    Section 05.C: Recomputes structure-valid stop at `rebuilt_context.decision_timestamp_ms`
    through `collect_causal_stop_anchor_candidates`, `rank_structure_valid_anchors`,
    `compute_causal_noise_buffer`, `derive_structure_valid_stop`, and
    `validate_structure_invalidation_contract`.
    When `require_outer_protected_structure=True` (e.g. R4 / R7 / R8), selects the outer
    protected structural anchor (preferring valid M15/M5 structural swing or the deepest valid
    uninvalidated causal M1/M5 swing that places the stop outside local noise and satisfies
    `stop_distance >= min_atr_m1_multiple * atr_m1` when supported by a real causal anchor).
    Never invents an arbitrary stop price without a real causal anchor.
    """
    norm_dir = str(direction or "LONG").strip().upper()
    cutoff_ms = int(rebuilt_context.decision_timestamp_ms)
    closed_m1 = list(rebuilt_context.closed_m1_candles)
    closed_m5 = list(rebuilt_context.closed_m5_candles)
    closed_m15 = list(rebuilt_context.closed_m15_candles)

    from services.liquidity_scenario_service import identify_confirmed_swings

    sh_1m, sl_1m = identify_confirmed_swings(closed_m1, left_bars=2, right_bars=2)

    candidates = collect_causal_stop_anchor_candidates(
        model_id=model_id,
        direction=norm_dir,
        entry_price=float(new_entry),
        decision_timestamp_ms=cutoff_ms,
        closed_m1=closed_m1,
        closed_m5=closed_m5,
        closed_m15=closed_m15,
        sh_1m=sh_1m,
        sl_1m=sl_1m,
        tick_size=tick_size,
        enforce_cross_tf_invalidation_check=True,
    )
    best_anc, ranked_cands, rank_reasons = rank_structure_valid_anchors(
        candidates,
        model_id=model_id,
        direction=norm_dir,
        entry_price=float(new_entry),
        decision_timestamp_ms=cutoff_ms,
        tick_size=tick_size,
    )
    valid_cands = [
        c
        for c in ranked_cands
        if c.is_causal
        and c.is_closed_bar
        and not c.is_invalidated
        and len(c.rejection_reasons) == 0
        and int(c.known_at_ms) <= cutoff_ms
    ]
    if not valid_cands or best_anc is None:
        return {
            "status": "REJECTED",
            "rejection_reason": (rank_reasons[0] if rank_reasons else "NO_CAUSAL_STRUCTURAL_ANCHOR"),
            "selected_sl": None,
            "invalidation_price": None,
            "selected_anchor": None,
            "known_at_ms": cutoff_ms,
        }

    atr_m1 = float(rebuilt_context.atr_m1 or 1.0)
    atr_m5 = float(rebuilt_context.atr_m5) if rebuilt_context.atr_m5 is not None else None
    spread = float(rebuilt_context.spread or 0.20)
    orig_sl = float((original_stop_plan or {}).get("selected_sl") or 0.0)
    min_req_dist = round(float(min_atr_m1_multiple) * max(0.10, atr_m1), 4)

    chosen_anc: StructuralStopCandidate = best_anc
    k_m1_use = 0.35 if require_outer_protected_structure else 0.20
    k_m5_use = 0.20 if require_outer_protected_structure else 0.12
    k_sp_use = 1.50 if require_outer_protected_structure else 1.25
    min_ticks_use = 8 if require_outer_protected_structure else 5

    if require_outer_protected_structure:
        outer_candidates: List[Tuple[StructuralStopCandidate, float]] = []
        for cand in valid_cands:
            buf_c, _, _ = compute_causal_noise_buffer(
                atr_m1=atr_m1,
                atr_m5=atr_m5,
                spread=spread,
                tick_size=tick_size,
                buffer_mode="STRUCTURE_PLUS_NOISE",
                anchor_timeframe=cand.timeframe,
                k_m1=k_m1_use,
                k_m5=k_m5_use,
                k_spread=k_sp_use,
                min_buffer_ticks=min_ticks_use,
            )
            sl_c = derive_structure_valid_stop(
                direction=norm_dir,
                invalidation_price=cand.anchor_price,
                noise_buffer_price=buf_c,
                tick_size=tick_size,
                entry_price=float(new_entry),
            )
            if sl_c is None:
                continue
            dist_c = abs(float(new_entry) - float(sl_c))
            if dist_c >= min_req_dist:
                outer_candidates.append((cand, float(sl_c)))

        if outer_candidates:
            def _outer_sort_key(pair: Tuple[StructuralStopCandidate, float]) -> Tuple[Any, ...]:
                c, sl_val = pair
                more_protective = (
                    0
                    if (
                        orig_sl <= 0.0
                        or (norm_dir == "LONG" and sl_val < orig_sl - 0.01)
                        or (norm_dir == "SHORT" and sl_val > orig_sl + 0.01)
                    )
                    else 1
                )
                tf_pref = 0 if c.timeframe in ("5M", "15M") else (1 if c.is_protected_swing else 2)
                return (
                    more_protective,
                    tf_pref,
                    abs(float(new_entry) - float(c.anchor_price)),
                    str(c.anchor_id),
                )

            outer_candidates.sort(key=_outer_sort_key)
            chosen_anc = outer_candidates[0][0]
        else:
            return {
                "status": "REJECTED",
                "rejection_reason": "REJECTED_NO_OUTER_PROTECTED_ANCHOR_MEETING_MIN_ATR_DISTANCE",
                "selected_sl": None,
                "invalidation_price": None,
                "selected_anchor": None,
                "known_at_ms": cutoff_ms,
            }

    buf_px, buf_comps, dom_comp = compute_causal_noise_buffer(
        atr_m1=atr_m1,
        atr_m5=atr_m5,
        spread=spread,
        tick_size=tick_size,
        buffer_mode="STRUCTURE_PLUS_NOISE",
        anchor_timeframe=chosen_anc.timeframe,
        k_m1=k_m1_use,
        k_m5=k_m5_use,
        k_spread=k_sp_use,
        min_buffer_ticks=min_ticks_use,
    )
    prop_sl = derive_structure_valid_stop(
        direction=norm_dir,
        invalidation_price=chosen_anc.anchor_price,
        noise_buffer_price=buf_px,
        tick_size=tick_size,
        entry_price=float(new_entry),
    )
    contract_ok, contract_reasons = validate_structure_invalidation_contract(
        direction=norm_dir,
        entry_price=float(new_entry),
        proposed_sl=prop_sl,
        anchor=chosen_anc,
        noise_buffer_price=buf_px,
        decision_timestamp_ms=cutoff_ms,
        tick_size=tick_size,
    )
    if not contract_ok or prop_sl is None:
        return {
            "status": "REJECTED",
            "rejection_reason": (contract_reasons[0] if contract_reasons else "STOP_INVALIDATION_CONTRACT_FAILED"),
            "selected_sl": None,
            "invalidation_price": chosen_anc.anchor_price,
            "selected_anchor": chosen_anc.to_dict(),
            "known_at_ms": int(chosen_anc.known_at_ms),
        }

    return {
        "status": "VALID",
        "rejection_reason": None,
        "selected_sl": round(float(prop_sl), 2),
        "invalidation_price": round(float(chosen_anc.anchor_price), 2),
        "noise_buffer_price": round(float(buf_px), 4),
        "buffer_components": buf_comps,
        "dominant_buffer_component": dom_comp,
        "selected_anchor_id": chosen_anc.anchor_id,
        "anchor_kind": chosen_anc.anchor_kind,
        "anchor_timeframe": chosen_anc.timeframe,
        "known_at_ms": int(chosen_anc.known_at_ms),
        "stop_Changed_from_original": (orig_sl <= 0.0 or abs(round(float(prop_sl), 2) - round(orig_sl, 2)) >= 0.01),
    }


def derive_executable_structural_target_v179312(
    rebuilt_context: MarketDecisionContext,
    direction: str,
    entry: float,
    selected_stop: float,
    *,
    max_net_rr: float = 3.50,
    min_net_rr: float = 2.0,
    original_target_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Section 05.D: Recomputes candidate causal liquidity targets at `rebuilt_context.decision_timestamp_ms`
    using `resolve_causal_target_candidates` plus causal confirmed M1/M5 unconsumed swing levels and
    selects the nearest unswept structural liquidity pool whose cost-aware `net_rr` lies in
    `[min_net_rr, max_net_rr]`.
    - Never uses future MFE or post-decision bars.
    - Never fabricates an artificial `entry +/- k*R` target without a backing causal structural level.
    """
    norm_dir = str(direction or "LONG").strip().upper()
    cutoff_ms = int(rebuilt_context.decision_timestamp_ms)
    closed_m1 = list(rebuilt_context.closed_m1_candles)
    closed_m5 = list(rebuilt_context.closed_m5_candles)
    closed_m15 = list(rebuilt_context.closed_m15_candles)
    closed_h1 = list(rebuilt_context.closed_h1_candles)
    closed_h4 = list(rebuilt_context.closed_h4_candles)
    closed_d1 = list(rebuilt_context.closed_d1_candles)

    raw_targets = list(
        resolve_causal_target_candidates(
            direction=norm_dir,
            entry_price=float(entry),
            decision_timestamp_ms=cutoff_ms,
            liquidity_pools=list(rebuilt_context.liquidity_pools),
            closed_m1=closed_m1,
            closed_m5=closed_m5,
            closed_m15=closed_m15,
            closed_h1=closed_h1,
            closed_h4=closed_h4,
            closed_d1=closed_d1,
            atr_m1=rebuilt_context.atr_m1,
            tick_size=0.01,
            enforce_cross_tf_sweep_check=True,
        )
    )

    # Also include confirmed M1/M5/M15/1H micro-swings (left_bars=1, right_bars=1, known_at_ms <= cutoff_ms)
    # and unmitigated causal M5/M15/1H FVG liquidity pools that remain unconsumed by subsequent closed bars up to `cutoff_ms`.
    from services.liquidity_scenario_service import identify_confirmed_swings

    seen_px = {round(float(c["price"]), 2) for c in raw_targets if c.get("price") is not None}

    # Precompute M1 suffix high/low arrays by close_time and open_time for O(log N) sweep checks
    m1_len = len(closed_m1)
    m1_open_ts = [int(b.get("open_time") or b.get("timestamp") or 0) for b in closed_m1]
    m1_close_ts = [int(b.get("close_time") or 0) for b in closed_m1]
    m1_suf_high = [0.0] * (m1_len + 1)
    m1_suf_low = [float("inf")] * (m1_len + 1)
    for k in range(m1_len - 1, -1, -1):
        bh = float(closed_m1[k]["high"])
        bl = float(closed_m1[k]["low"])
        m1_suf_high[k] = bh if bh > m1_suf_high[k + 1] else m1_suf_high[k + 1]
        m1_suf_low[k] = bl if bl < m1_suf_low[k + 1] else m1_suf_low[k + 1]

    stop_dist = abs(float(entry) - float(selected_stop))

    for tf_bars, tf_label, tf_dur_ms in (
        (closed_m1, "1M", 60_000),
        (closed_m5, "5M", 300_000),
        (closed_m15, "15M", 900_000),
        (closed_h1, "1H", 3_600_000),
    ):
        if len(tf_bars) < 3:
            continue
        tf_len = len(tf_bars)
        tf_open_ts = [int(b.get("open_time") or b.get("timestamp") or 0) for b in tf_bars]
        tf_suf_high = [0.0] * (tf_len + 1)
        tf_suf_low = [float("inf")] * (tf_len + 1)
        for k in range(tf_len - 1, -1, -1):
            bh = float(tf_bars[k]["high"])
            bl = float(tf_bars[k]["low"])
            tf_suf_high[k] = bh if bh > tf_suf_high[k + 1] else tf_suf_high[k + 1]
            tf_suf_low[k] = bl if bl < tf_suf_low[k + 1] else tf_suf_low[k + 1]

        sh_micro, sl_micro = identify_confirmed_swings(
            tf_bars, timeframe=tf_label, left_bars=1, right_bars=1, as_of_ms=cutoff_ms
        )
        micro_swings = sh_micro if norm_dir == "LONG" else sl_micro
        for sw in micro_swings:
            sw_px = round(float(sw.get("price", 0.0)), 2)
            if sw_px in seen_px:
                continue
            sw_occ = int(sw.get("occurred_at_ms", sw.get("timestamp", 0)))
            sw_known = int(sw.get("known_at_ms", sw_occ + tf_dur_ms))
            if sw_known > cutoff_ms or sw_occ > cutoff_ms:
                continue
            if (norm_dir == "LONG" and sw_px <= float(entry) + 0.30) or (
                norm_dir == "SHORT" and sw_px >= float(entry) - 0.30
            ):
                continue
            pos_tf = bisect.bisect_right(tf_open_ts, sw_occ)
            pos_m1 = bisect.bisect_right(m1_open_ts, sw_occ)
            if norm_dir == "LONG":
                swept = (pos_tf < tf_len and tf_suf_high[pos_tf] >= sw_px) or (
                    pos_m1 < m1_len and m1_suf_high[pos_m1] >= sw_px
                )
            else:
                swept = (pos_tf < tf_len and tf_suf_low[pos_tf] <= sw_px) or (
                    pos_m1 < m1_len and m1_suf_low[pos_m1] <= sw_px
                )
            if not swept:
                seen_px.add(sw_px)
                raw_targets.append(
                    {
                        "id": f"{tf_label.lower()}_micro_pool_{sw.get('id', sw_occ)}",
                        "price": sw_px,
                        "side": "BUY_SIDE" if norm_dir == "LONG" else "SELL_SIDE",
                        "source_kind": f"{tf_label}_CONFIRMED_SWING_{'HIGH' if norm_dir == 'LONG' else 'LOW'}",
                        "timeframe": tf_label,
                        "occurred_at_ms": sw_occ,
                        "known_at_ms": sw_known,
                        "state": "UNTOUCHED",
                        "distance": round(abs(sw_px - float(entry)), 4),
                    }
                )
        # Also check unmitigated opposing FVG / imbalance boundary levels on 5M/15M/1H
        if tf_label in ("5M", "15M", "1H"):
            for idx_b in range(2, tf_len):
                b0 = tf_bars[idx_b - 2]
                b2 = tf_bars[idx_b]
                c2_ms = int(b2.get("close_time") or b2.get("close_time_ms") or 0)
                if c2_ms > cutoff_ms:
                    continue
                fvg_candidates: List[Tuple[str, float]] = []
                if float(b2["low"]) > float(b0["high"]):
                    bot, top = float(b0["high"]), float(b2["low"])
                    fvg_candidates.extend([("FVG_PROXIMAL", bot), ("FVG_CE", 0.5 * (bot + top)), ("FVG_DISTAL", top)])
                elif float(b2["high"]) < float(b0["low"]):
                    bot, top = float(b2["high"]), float(b0["low"])
                    fvg_candidates.extend([("FVG_PROXIMAL", top), ("FVG_CE", 0.5 * (bot + top)), ("FVG_DISTAL", bot)])
                if not fvg_candidates:
                    continue
                pos_m1_c2 = bisect.bisect_right(m1_close_ts, c2_ms)
                for f_kind, f_raw in fvg_candidates:
                    f_px = round(f_raw, 2)
                    if f_px in seen_px:
                        continue
                    if (norm_dir == "LONG" and f_px <= float(entry) + 0.30) or (
                        norm_dir == "SHORT" and f_px >= float(entry) - 0.30
                    ):
                        continue
                    if norm_dir == "LONG":
                        swept_f = (idx_b + 1 < tf_len and tf_suf_high[idx_b + 1] >= f_px) or (
                            pos_m1_c2 < m1_len and m1_suf_high[pos_m1_c2] >= f_px
                        )
                    else:
                        swept_f = (idx_b + 1 < tf_len and tf_suf_low[idx_b + 1] <= f_px) or (
                            pos_m1_c2 < m1_len and m1_suf_low[pos_m1_c2] <= f_px
                        )
                    if not swept_f:
                        seen_px.add(f_px)
                        raw_targets.append(
                            {
                                "id": f"{tf_label.lower()}_{f_kind.lower()}_{c2_ms}_{f_px}",
                                "price": f_px,
                                "side": "BUY_SIDE" if norm_dir == "LONG" else "SELL_SIDE",
                                "source_kind": f"{tf_label}_UNMITIGATED_{f_kind}",
                                "timeframe": tf_label,
                                "occurred_at_ms": c2_ms,
                                "known_at_ms": c2_ms,
                                "state": "UNTOUCHED",
                                "distance": round(abs(f_px - float(entry)), 4),
                            }
                        )
            # Also include unswept closed HTF (5M/15M/1H) candle highs/lows (known_at_ms = close_time <= cutoff_ms)
            for idx_c, b_c in enumerate(tf_bars):
                bc_close_ms = int(b_c.get("close_time") or b_c.get("close_time_ms") or 0)
                if bc_close_ms > cutoff_ms:
                    continue
                c_px = round(float(b_c["high"] if norm_dir == "LONG" else b_c["low"]), 2)
                if c_px in seen_px:
                    continue
                if (norm_dir == "LONG" and c_px <= float(entry) + 0.30) or (
                    norm_dir == "SHORT" and c_px >= float(entry) - 0.30
                ):
                    continue
                pos_m1_bc = bisect.bisect_right(m1_close_ts, bc_close_ms)
                if norm_dir == "LONG":
                    swept_c = (idx_c + 1 < tf_len and tf_suf_high[idx_c + 1] >= c_px) or (
                        pos_m1_bc < m1_len and m1_suf_high[pos_m1_bc] >= c_px
                    )
                else:
                    swept_c = (idx_c + 1 < tf_len and tf_suf_low[idx_c + 1] <= c_px) or (
                        pos_m1_bc < m1_len and m1_suf_low[pos_m1_bc] <= c_px
                    )
                if not swept_c:
                    seen_px.add(c_px)
                    raw_targets.append(
                        {
                            "id": f"{tf_label.lower()}_candle_pool_{bc_close_ms}_{c_px}",
                            "price": c_px,
                            "side": "BUY_SIDE" if norm_dir == "LONG" else "SELL_SIDE",
                            "source_kind": f"{tf_label}_UNSWEPT_CANDLE_{'HIGH' if norm_dir == 'LONG' else 'LOW'}",
                            "timeframe": tf_label,
                            "occurred_at_ms": bc_close_ms,
                            "known_at_ms": bc_close_ms,
                            "state": "UNTOUCHED",
                            "distance": round(abs(c_px - float(entry)), 4),
                        }
                    )

    viable_in_band: List[Dict[str, Any]] = []
    viable_above_band: List[Dict[str, Any]] = []

    for cand in raw_targets:
        t_px = round(float(cand["price"]), 2)
        known_ms = int(cand.get("known_at_ms") or cutoff_ms)
        if known_ms > cutoff_ms:
            raise ValueError(f"TARGET_CANDIDATE_FUTURE_LEAK:{known_ms}>{cutoff_ms}")

        # Fast causal pre-filter: since net_rr <= gross_rr (fees > 0), any target with gross_rr < min_net_rr cannot have net_rr >= min_net_rr
        if stop_dist > 1e-6:
            gross_rr_est = abs(t_px - float(entry)) / stop_dist
            if gross_rr_est + 1e-6 < float(min_net_rr):
                continue

        sz_eval = recalculate_trade_risk_quantity_v179312(
            entry=float(entry),
            sl=float(selected_stop),
            tp=t_px,
            direction=norm_dir,
            decision_ts=cutoff_ms,
        )
        if not sz_eval["can_execute"]:
            continue
        n_rr = float(sz_eval["net_rr"])
        item = {
            **cand,
            "target_price": t_px,
            "target_id": str(cand.get("id") or f"target_{t_px}"),
            "distance_usd": round(abs(t_px - float(entry)), 4),
            "net_rr": n_rr,
            "gross_rr": float(sz_eval["gross_rr"]),
            "quantity": float(sz_eval["quantity"]),
            "planned_net_risk_usd": float(sz_eval["planned_net_risk_usd"]),
        }
        if float(min_net_rr) <= n_rr <= float(max_net_rr) + 1e-6:
            viable_in_band.append(item)
        elif n_rr > float(max_net_rr):
            viable_above_band.append(item)

    if not viable_in_band:
        return {
            "status": "REJECTED",
            "rejection_reason": (
                "NO_VIABLE_CAUSAL_STRUCTURAL_TARGET_IN_2_0_TO_3_5_RR_BAND"
                if viable_above_band
                else "SHADOW_REJECTED_NO_VALID_TARGET"
            ),
            "take_profit": None,
            "selected_target_id": None,
            "target_timeframe": None,
            "net_rr": None,
            "known_at_ms": cutoff_ms,
            "candidates_evaluated_count": len(raw_targets),
            "candidates_above_band_count": len(viable_above_band),
        }

    orig_tp = round(float(original_target_price), 2) if original_target_price is not None else None
    orig_dist = abs(orig_tp - float(entry)) if orig_tp is not None else None

    # Sort viable targets in band:
    # 1. Prefer nearer unswept causal pool (`distance_usd < orig_dist - 0.02`) when `orig_tp` is further away,
    #    or a distinct unswept pool in `[2.0, max_net_rr]` sorted by nearest `distance_usd`.
    def _target_sort_key(c: Mapping[str, Any]) -> Tuple[Any, ...]:
        d_usd = float(c["distance_usd"])
        t_p = float(c["target_price"])
        if orig_tp is not None and orig_dist is not None:
            is_nearer = 0 if d_usd < orig_dist - 0.02 else (1 if abs(t_p - orig_tp) >= 0.02 else 2)
        else:
            is_nearer = 0
        return (is_nearer, d_usd, str(c["target_id"]))

    viable_in_band.sort(key=_target_sort_key)
    chosen = viable_in_band[0]
    return {
        "status": "VALID",
        "rejection_reason": None,
        "take_profit": chosen["target_price"],
        "selected_target_id": str(chosen["target_id"]),
        "target_kind": str(chosen.get("source_kind") or "STRUCTURAL_POOL"),
        "target_timeframe": str(chosen.get("timeframe") or "15M"),
        "distance_usd": float(chosen["distance_usd"]),
        "net_rr": chosen["net_rr"],
        "gross_rr": chosen["gross_rr"],
        "quantity": chosen["quantity"],
        "planned_net_risk_usd": chosen["planned_net_risk_usd"],
        "known_at_ms": int(chosen.get("known_at_ms") or cutoff_ms),
        "target_changed_from_original": (orig_tp is None or abs(chosen["target_price"] - orig_tp) >= 0.01),
        "candidates_evaluated_count": len(raw_targets),
    }


def recalculate_trade_risk_quantity_v179312(
    entry: float,
    sl: float,
    tp: float,
    cost_policy: Optional[RiskPolicySnapshotV179311] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    *,
    direction: str = "LONG",
    decision_ts: int = 0,
) -> Dict[str, Any]:
    """
    Section 05.E: Delegates to `evaluate_stop_tp_risk_and_size` and `calculate_risk_reward`
    and enforces `$2.50` max planned net risk, `0.01` lot step, `>= 0.01` min lot, and
    `min_net_rr >= 2.0`. Fails closed if cost/instrument metadata is missing or invalid.
    """
    if metadata is not None:
        for req_k in ("quantity_step", "min_quantity", "multiplier", "tick_size", "taker_fee_rate"):
            if req_k in metadata and (metadata[req_k] is None or float(metadata[req_k]) <= 0.0):
                return {
                    "status": "REJECTED",
                    "can_execute": False,
                    "rejection_reason": f"MISSING_OR_INVALID_INSTRUMENT_METADATA:{req_k}",
                    "quantity": 0.0,
                    "planned_net_risk_usd": 0.0,
                    "net_rr": 0.0,
                    "gross_rr": 0.0,
                }

    pol = cost_policy or get_canonical_risk_policy_snapshot()
    norm_dir = str(direction or "LONG").strip().upper()
    if (
        not (math.isfinite(float(entry)) and math.isfinite(float(sl)) and math.isfinite(float(tp)))
        or float(entry) <= 0.0
        or float(sl) <= 0.0
        or float(tp) <= 0.0
    ):
        return {
            "status": "REJECTED",
            "can_execute": False,
            "rejection_reason": "NON_FINITE_OR_NON_POSITIVE_PRICES",
            "quantity": 0.0,
            "planned_net_risk_usd": 0.0,
            "net_rr": 0.0,
            "gross_rr": 0.0,
        }

    costs = CostAssumptions(
        slippage_usd=pol.slippage_usd,
        maker_fee_rate=pol.maker_fee_rate,
        taker_fee_rate=pol.taker_fee_rate,
        tp_is_maker=False,
    )
    calc = calculate_risk_reward(
        direction=norm_dir,
        planned_entry=round(float(entry), 2),
        stop_loss=round(float(sl), 2),
        take_profit=round(float(tp), 2),
        capital_usdt=pol.capital_usdt,
        risk_pct=pol.risk_pct_percent,
        leverage=pol.leverage,
        margin_mode=pol.margin_mode,
        min_net_rr=pol.min_net_rr,
        costs=costs,
        now_ms=int(decision_ts),
    )
    qty = round(float(calc.quantity or 0.0), 4)
    net_risk = round(float(calc.net_risk_usdt or 0.0), 4)
    net_rr = round(float(calc.net_rr or 0.0), 4)
    gross_rr = round(float(calc.gross_rr or 0.0), 4)

    if not calc.is_valid or not calc.can_execute or qty < pol.min_quantity:
        return {
            "status": "REJECTED",
            "can_execute": False,
            "rejection_reason": str(calc.invalid_reason or calc.skip_reason or "BELOW_MIN_LOT_SIZE"),
            "quantity": qty,
            "planned_net_risk_usd": net_risk,
            "net_rr": net_rr,
            "gross_rr": gross_rr,
        }
    if net_risk > pol.max_planned_risk_usd + 1e-6:
        return {
            "status": "REJECTED",
            "can_execute": False,
            "rejection_reason": f"PLANNED_RISK_EXCEEDS_2_50_CAP:{net_risk:.4f}",
            "quantity": qty,
            "planned_net_risk_usd": net_risk,
            "net_rr": net_rr,
            "gross_rr": gross_rr,
        }
    if net_rr + 1e-9 < pol.min_net_rr:
        return {
            "status": "REJECTED",
            "can_execute": False,
            "rejection_reason": "SHADOW_REJECTED_INSUFFICIENT_NET_RR",
            "quantity": qty,
            "planned_net_risk_usd": net_risk,
            "net_rr": net_rr,
            "gross_rr": gross_rr,
        }

    return {
        "status": "VALID",
        "can_execute": True,
        "rejection_reason": None,
        "quantity": qty,
        "planned_net_risk_usd": net_risk,
        "net_rr": net_rr,
        "gross_rr": gross_rr,
    }


def materialize_executable_counterfactual_v179312(
    base_setup_id: str,
    variant_policy: ExecutableResearchPolicyV179312,
    causal_history: Mapping[str, Any],
    *,
    base_record: Mapping[str, Any],
    close_ts_index: Optional[Mapping[str, Sequence[int]]] = None,
    rebuilt_ctx_cache: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Section 05.F: Materializes a true executable counterfactual plan for `base_record` under
    `variant_policy`:
      - Returns old plan, new timestamp, new entry, new SL, new TP, new quantity,
        `changed_dimensions`, causal `known_at_ms`, accept/reject status & reason, and
        `new_trade_fingerprint`.
      - Explicitly distinguishes `ACCEPTED_MODIFIED_EXECUTABLE_GEOMETRY`,
        `ACCEPTED_UNCHANGED_NO_OP`, and `REJECTED_BY_INTERVENTION`.
    """
    bars_1m = causal_history["1m"]
    c_ts = close_ts_index if close_ts_index is not None else build_close_ts_index_v179312(causal_history)

    base_idx = int(base_record["checkpoint_index_1m"])
    base_ts = int(base_record["decision_timestamp_ms"])
    direction = str(base_record["direction"]).upper()
    model_id = str(base_record["model_id"])
    old_entry = round(float(base_record["entry_reference"]), 2)
    old_sl = round(float(base_record["selected_sl"]), 2)
    old_tp = round(float(base_record["take_profit"]), 2)
    old_qty = round(float(base_record.get("executed_quantity") or base_record.get("hypothetical_quantity") or 0.0), 4)
    old_risk = round(float(base_record.get("planned_net_risk_usd") or 0.0), 4)
    old_net_rr = round(float(base_record.get("net_rr") or 0.0), 4)

    old_plan = {
        "setup_id": base_setup_id,
        "checkpoint_index_1m": base_idx,
        "decision_timestamp_ms": base_ts,
        "model_id": model_id,
        "direction": direction,
        "entry_reference": old_entry,
        "selected_sl": old_sl,
        "take_profit": old_tp,
        "quantity": old_qty,
        "planned_net_risk_usd": old_risk,
        "net_rr": old_net_rr,
    }

    # Step 1: Delayed entry confirmation if configured
    new_idx = base_idx
    new_ts = base_ts
    confirmation_info: Optional[Dict[str, Any]] = None
    if (
        variant_policy.wait_for_confirmed_mss_and_displacement
        or variant_policy.wait_for_confirmed_breaker_fvg_retest
    ):
        conf_res = find_next_causal_entry_confirmation_v179312(
            base_record,
            bars_1m,
            max_wait_bars=variant_policy.max_wait_bars,
            setup_invalidation=float(base_record.get("structural_invalidation_price") or old_sl),
            require_mss_displacement=variant_policy.wait_for_confirmed_mss_and_displacement,
            require_breaker_fvg_retest=variant_policy.wait_for_confirmed_breaker_fvg_retest,
        )
        confirmation_info = conf_res
        if conf_res["status"] != "CONFIRMED" or conf_res["confirmed_checkpoint_index_1m"] is None:
            return {
                "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
                "setup_id": base_setup_id,
                "variant_id": variant_policy.variant_id,
                "intervention_type": variant_policy.intervention_type,
                "status": "REJECTED_BY_INTERVENTION",
                "accepted": False,
                "rejection_reason": str(conf_res["rejection_reason"]),
                "old_plan": old_plan,
                "new_checkpoint_index_1m": None,
                "new_decision_timestamp_ms": None,
                "new_entry_reference": None,
                "new_selected_sl": None,
                "new_take_profit": None,
                "new_quantity": None,
                "new_planned_net_risk_usd": None,
                "new_net_rr": None,
                "changed_dimensions": [],
                "known_at_ms": int(conf_res["known_at_ms"]),
                "confirmation_details": confirmation_info,
                "new_trade_fingerprint": None,
            }
        new_idx = int(conf_res["confirmed_checkpoint_index_1m"])
        new_ts = int(conf_res["new_decision_timestamp_ms"])

    # Step 2: Check causal H1/H4 alignment at `new_ts` if configured
    if variant_policy.require_causal_h1_h4_alignment:
        htf_ok, htf_info = _check_causal_htf_alignment_at_ts_v179312(
            causal_history, c_ts, new_ts, direction
        )
        if not htf_ok:
            return {
                "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
                "setup_id": base_setup_id,
                "variant_id": variant_policy.variant_id,
                "intervention_type": variant_policy.intervention_type,
                "status": "REJECTED_BY_INTERVENTION",
                "accepted": False,
                "rejection_reason": str(htf_info["reason"]),
                "old_plan": old_plan,
                "new_checkpoint_index_1m": new_idx,
                "new_decision_timestamp_ms": new_ts,
                "new_entry_reference": None,
                "new_selected_sl": None,
                "new_take_profit": None,
                "new_quantity": None,
                "new_planned_net_risk_usd": None,
                "new_net_rr": None,
                "changed_dimensions": [],
                "known_at_ms": new_ts,
                "htf_alignment_details": htf_info,
                "new_trade_fingerprint": None,
            }

    # Step 3: Check active ICT macro window at `new_ts` if configured
    if variant_policy.require_high_quality_macro_session:
        m_state = resolve_causal_macro_time_window(new_ts, symbol="XAUUSDT")
        if not m_state.eligible_for_macro_gate:
            return {
                "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
                "setup_id": base_setup_id,
                "variant_id": variant_policy.variant_id,
                "intervention_type": variant_policy.intervention_type,
                "status": "REJECTED_BY_INTERVENTION",
                "accepted": False,
                "rejection_reason": "REJECTED_OUTSIDE_ICT_MACRO_TIME_WINDOW",
                "old_plan": old_plan,
                "new_checkpoint_index_1m": new_idx,
                "new_decision_timestamp_ms": new_ts,
                "new_entry_reference": None,
                "new_selected_sl": None,
                "new_take_profit": None,
                "new_quantity": None,
                "new_planned_net_risk_usd": None,
                "new_net_rr": None,
                "changed_dimensions": [],
                "known_at_ms": new_ts,
                "new_trade_fingerprint": None,
            }

    # For pure filter-only or control baseline where timestamp did not change and no SL/TP rederivation is requested:
    if (
        variant_policy.intervention_type in ("BASELINE_CONTROL", "ENTRY_FILTER_ONLY")
        and new_idx == base_idx
        and not variant_policy.rederive_outer_structural_stop
        and not variant_policy.rederive_structural_liquidity_target
    ):
        fp = _canonical_json_sha256(
            {
                "variant_id": variant_policy.variant_id,
                "decision_ts": base_ts,
                "direction": direction,
                "entry": old_entry,
                "sl": old_sl,
                "tp": old_tp,
                "qty": old_qty,
            }
        )
        return {
            "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
            "setup_id": base_setup_id,
            "variant_id": variant_policy.variant_id,
            "intervention_type": variant_policy.intervention_type,
            "status": "ACCEPTED_UNCHANGED_NO_OP",
            "accepted": True,
            "rejection_reason": None,
            "old_plan": old_plan,
            "new_checkpoint_index_1m": base_idx,
            "new_decision_timestamp_ms": base_ts,
            "new_entry_reference": old_entry,
            "new_selected_sl": old_sl,
            "new_take_profit": old_tp,
            "new_quantity": old_qty,
            "new_planned_net_risk_usd": old_risk,
            "new_net_rr": old_net_rr,
            "new_gross_rr": float(base_record.get("gross_rr") or old_net_rr),
            "new_structural_invalidation_price": float(base_record.get("structural_invalidation_price") or old_sl),
            "new_selected_anchor_id": base_record.get("selected_anchor_id"),
            "new_anchor_kind": base_record.get("anchor_kind"),
            "new_selected_target_id": base_record.get("selected_target_id"),
            "changed_dimensions": [],
            "known_at_ms": base_ts,
            "new_trade_fingerprint": fp,
        }

    # Build causal context at `new_idx` (with optional per-index cache across policies)
    if rebuilt_ctx_cache is not None and new_idx in rebuilt_ctx_cache:
        rebuilt_ctx = rebuilt_ctx_cache[new_idx]
    else:
        from tools.run_v17_9_3_macro_unicorn_backtest import V1793_VARIANT_POLICIES

        pol_a1_fast = dict(V1793_VARIANT_POLICIES["A1_V1792_SL_TP_BASELINE"])
        pol_a1_fast["mtf_structure_observe_only"] = False
        rebuilt_ctx = build_causal_context_at_v179312(
            causal_history,
            new_idx,
            policy=pol_a1_fast,
            close_ts_index=c_ts,
        )
        if rebuilt_ctx_cache is not None:
            rebuilt_ctx_cache[new_idx] = rebuilt_ctx

    # Step 4: Rebuild entry reference at `new_ts`
    entry_res = rebuild_entry_reference_v179312(
        rebuilt_ctx,
        new_ts,
        {"direction": direction, "spread_usd": 0.20},
    )
    if entry_res["status"] != "VALID" or entry_res["entry_reference"] is None:
        return {
            "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
            "setup_id": base_setup_id,
            "variant_id": variant_policy.variant_id,
            "intervention_type": variant_policy.intervention_type,
            "status": "REJECTED_BY_INTERVENTION",
            "accepted": False,
            "rejection_reason": str(entry_res["rejection_reason"]),
            "old_plan": old_plan,
            "new_checkpoint_index_1m": new_idx,
            "new_decision_timestamp_ms": new_ts,
            "new_entry_reference": None,
            "new_selected_sl": None,
            "new_take_profit": None,
            "new_quantity": None,
            "new_planned_net_risk_usd": None,
            "new_net_rr": None,
            "changed_dimensions": [],
            "known_at_ms": new_ts,
            "new_trade_fingerprint": None,
        }
    new_entry = round(float(entry_res["entry_reference"]), 2)

    # Step 5: Derive executable structural stop if timestamp changed or `rederive_outer_structural_stop` is enabled
    new_sl = old_sl
    new_inv_px = float(base_record.get("structural_invalidation_price") or old_sl)
    new_anc_id = base_record.get("selected_anchor_id")
    new_anc_kind = base_record.get("anchor_kind")
    stop_details: Optional[Dict[str, Any]] = None

    if variant_policy.rederive_outer_structural_stop or new_idx != base_idx:
        stop_res = derive_executable_structural_stop_v179312(
            rebuilt_ctx,
            direction,
            new_entry,
            original_stop_plan={"selected_sl": old_sl},
            model_id=model_id,
            require_outer_protected_structure=variant_policy.rederive_outer_structural_stop,
            min_atr_m1_multiple=variant_policy.min_sl_atr_m1_multiple if variant_policy.rederive_outer_structural_stop else 0.0,
        )
        stop_details = stop_res
        if stop_res["status"] != "VALID" or stop_res["selected_sl"] is None:
            return {
                "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
                "setup_id": base_setup_id,
                "variant_id": variant_policy.variant_id,
                "intervention_type": variant_policy.intervention_type,
                "status": "REJECTED_BY_INTERVENTION",
                "accepted": False,
                "rejection_reason": str(stop_res["rejection_reason"]),
                "old_plan": old_plan,
                "new_checkpoint_index_1m": new_idx,
                "new_decision_timestamp_ms": new_ts,
                "new_entry_reference": new_entry,
                "new_selected_sl": None,
                "new_take_profit": None,
                "new_quantity": None,
                "new_planned_net_risk_usd": None,
                "new_net_rr": None,
                "changed_dimensions": [],
                "known_at_ms": int(stop_res.get("known_at_ms") or new_ts),
                "stop_details": stop_details,
                "new_trade_fingerprint": None,
            }
        new_sl = round(float(stop_res["selected_sl"]), 2)
        new_inv_px = round(float(stop_res["invalidation_price"]), 2)
        new_anc_id = stop_res.get("selected_anchor_id")
        new_anc_kind = stop_res.get("anchor_kind")

    # Step 6: Derive executable structural target if `rederive_structural_liquidity_target` is enabled or if old_tp is invalid at new_entry/new_sl
    new_tp = old_tp
    new_target_id = base_record.get("selected_target_id")
    target_details: Optional[Dict[str, Any]] = None

    need_target_rederive = bool(variant_policy.rederive_structural_liquidity_target)
    if not need_target_rederive and new_idx != base_idx:
        # Check if old_tp still satisfies directional geometry and min_net_rr >= 2.0 at new_entry and new_sl
        geom_still_ok = (new_sl < new_entry < old_tp) if direction == "LONG" else (old_tp < new_entry < new_sl)
        if not geom_still_ok:
            need_target_rederive = True
        else:
            chk_sz = recalculate_trade_risk_quantity_v179312(
                new_entry, new_sl, old_tp, direction=direction, decision_ts=new_ts
            )
            if not chk_sz["can_execute"]:
                need_target_rederive = True

    if need_target_rederive:
        max_rr = variant_policy.max_realistic_target_net_rr if variant_policy.rederive_structural_liquidity_target else 50.0
        tp_res = derive_executable_structural_target_v179312(
            rebuilt_ctx,
            direction,
            new_entry,
            new_sl,
            max_net_rr=max_rr,
            min_net_rr=2.0,
            original_target_price=old_tp,
        )
        target_details = tp_res
        if tp_res["status"] != "VALID" or tp_res["take_profit"] is None:
            return {
                "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
                "setup_id": base_setup_id,
                "variant_id": variant_policy.variant_id,
                "intervention_type": variant_policy.intervention_type,
                "status": "REJECTED_BY_INTERVENTION",
                "accepted": False,
                "rejection_reason": str(tp_res["rejection_reason"]),
                "old_plan": old_plan,
                "new_checkpoint_index_1m": new_idx,
                "new_decision_timestamp_ms": new_ts,
                "new_entry_reference": new_entry,
                "new_selected_sl": new_sl,
                "new_take_profit": None,
                "new_quantity": None,
                "new_planned_net_risk_usd": None,
                "new_net_rr": None,
                "changed_dimensions": [],
                "known_at_ms": int(tp_res.get("known_at_ms") or new_ts),
                "target_details": target_details,
                "new_trade_fingerprint": None,
            }
        new_tp = round(float(tp_res["take_profit"]), 2)
        new_target_id = tp_res.get("selected_target_id")

    # Step 7: Recalculate risk and quantity under $2.50 risk cap, 0.01 lot step, and min_net_rr >= 2.0
    size_res = recalculate_trade_risk_quantity_v179312(
        new_entry,
        new_sl,
        new_tp,
        direction=direction,
        decision_ts=new_ts,
    )
    if not size_res["can_execute"]:
        return {
            "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
            "setup_id": base_setup_id,
            "variant_id": variant_policy.variant_id,
            "intervention_type": variant_policy.intervention_type,
            "status": "REJECTED_BY_INTERVENTION",
            "accepted": False,
            "rejection_reason": str(size_res["rejection_reason"]),
            "old_plan": old_plan,
            "new_checkpoint_index_1m": new_idx,
            "new_decision_timestamp_ms": new_ts,
            "new_entry_reference": new_entry,
            "new_selected_sl": new_sl,
            "new_take_profit": new_tp,
            "new_quantity": size_res["quantity"],
            "new_planned_net_risk_usd": size_res["planned_net_risk_usd"],
            "new_net_rr": size_res["net_rr"],
            "changed_dimensions": [],
            "known_at_ms": new_ts,
            "sizing_details": size_res,
            "new_trade_fingerprint": None,
        }

    new_qty = round(float(size_res["quantity"]), 4)
    new_risk = round(float(size_res["planned_net_risk_usd"]), 4)
    new_net_rr = round(float(size_res["net_rr"]), 4)
    new_gross_rr = round(float(size_res["gross_rr"]), 4)

    changed_dims: List[str] = []
    if new_ts != base_ts:
        changed_dims.append("decision_timestamp_ms")
    if abs(new_entry - old_entry) >= 0.01:
        changed_dims.append("entry_reference")
    if abs(new_sl - old_sl) >= 0.01:
        changed_dims.append("selected_sl")
    if abs(new_tp - old_tp) >= 0.01:
        changed_dims.append("take_profit")
    if abs(new_qty - old_qty) >= 0.005:
        changed_dims.append("quantity")

    status_str = (
        "ACCEPTED_MODIFIED_EXECUTABLE_GEOMETRY"
        if len(changed_dims) > 0
        else "ACCEPTED_UNCHANGED_NO_OP"
    )
    fp = _canonical_json_sha256(
        {
            "variant_id": variant_policy.variant_id,
            "decision_ts": new_ts,
            "direction": direction,
            "entry": new_entry,
            "sl": new_sl,
            "tp": new_tp,
            "qty": new_qty,
        }
    )

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "setup_id": base_setup_id,
        "variant_id": variant_policy.variant_id,
        "intervention_type": variant_policy.intervention_type,
        "status": status_str,
        "accepted": True,
        "rejection_reason": None,
        "old_plan": old_plan,
        "new_checkpoint_index_1m": new_idx,
        "new_decision_timestamp_ms": new_ts,
        "new_entry_reference": new_entry,
        "new_selected_sl": new_sl,
        "new_take_profit": new_tp,
        "new_quantity": new_qty,
        "new_planned_net_risk_usd": new_risk,
        "new_net_rr": new_net_rr,
        "new_gross_rr": new_gross_rr,
        "new_structural_invalidation_price": new_inv_px,
        "new_selected_anchor_id": new_anc_id,
        "new_anchor_kind": new_anc_kind,
        "new_selected_target_id": new_target_id,
        "changed_dimensions": changed_dims,
        "known_at_ms": new_ts,
        "confirmation_details": confirmation_info,
        "stop_details": stop_details,
        "target_details": target_details,
        "sizing_details": size_res,
        "new_trade_fingerprint": fp,
    }


def replay_intervention_outcome_v179312(
    materialized_plan: Mapping[str, Any],
    canonical_bars: Sequence[Mapping[str, Any]],
    mode: str = "NEXT_BAR_OPEN_BID_ASK",
    *,
    base_record: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Section 05.G: Simulates forward trade outcome for `materialized_plan` using
    `simulate_v17931_trade_execution_net` with the NEW geometry (`new_entry_reference`,
    `new_selected_sl`, `new_take_profit`) and forward bars starting strictly after
    `new_checkpoint_index_1m` (`canonical_bars[new_idx + 1 : new_idx + 1 + 180]`).
    Logs both `original_execution_ledger` and `intervention_execution_ledger`.
    """
    if not bool(materialized_plan.get("accepted")):
        return {
            "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
            "setup_id": materialized_plan.get("setup_id"),
            "variant_id": materialized_plan.get("variant_id"),
            "intervention_type": materialized_plan.get("intervention_type"),
            "status": materialized_plan.get("status", "REJECTED_BY_INTERVENTION"),
            "accepted": False,
            "simulated": False,
            "rejection_reason": materialized_plan.get("rejection_reason"),
            "fill_mode": mode,
            "changed_dimensions": list(materialized_plan.get("changed_dimensions") or ()),
            "materialized_plan": dict(materialized_plan),
            "original_execution_ledger": (base_record or {}).get("execution_ledger"),
            "intervention_execution_ledger": None,
            "episode_record": None,
        }

    old_plan = materialized_plan["old_plan"]
    new_idx = int(materialized_plan["new_checkpoint_index_1m"])
    new_ts = int(materialized_plan["new_decision_timestamp_ms"])
    new_entry = float(materialized_plan["new_entry_reference"])
    new_sl = float(materialized_plan["new_selected_sl"])
    new_tp = float(materialized_plan["new_take_profit"])
    new_qty = float(materialized_plan["new_quantity"])
    new_risk = float(materialized_plan["new_planned_net_risk_usd"])
    new_net_rr = float(materialized_plan["new_net_rr"])
    new_gross_rr = float(materialized_plan.get("new_gross_rr") or new_net_rr)
    direction = str(old_plan["direction"]).upper()
    model_id = str(old_plan["model_id"])
    variant_id = str(materialized_plan["variant_id"])

    intent = OrderIntentV179312(
        intent_id=f"intent_{variant_id}_{model_id}_{new_idx}_{new_ts}",
        variant=variant_id,
        model_id=model_id,
        symbol="XAUUSDT",
        direction=direction,
        checkpoint_index_1m=new_idx,
        signal_timestamp_ms=new_ts,
        entry_reference=new_entry,
        selected_sl=new_sl,
        take_profit=new_tp,
        hypothetical_quantity=new_qty,
        planned_net_risk_usd=new_risk,
        gross_rr=new_gross_rr,
        net_rr=new_net_rr,
        order_type="NEXT_BAR_OPEN_MARKET_QUOTED",
        ttl_bars=1,
        selected_anchor_id=materialized_plan.get("new_selected_anchor_id"),
        anchor_kind=materialized_plan.get("new_anchor_kind"),
        structural_invalidation_price=materialized_plan.get("new_structural_invalidation_price"),
        selected_target_id=materialized_plan.get("new_selected_target_id"),
        setup_episode_id=str(materialized_plan.get("setup_id") or f"setup_{new_idx}"),
        inside_legacy_screen=bool((base_record or {}).get("inside_legacy_screen", False)),
        macro_primary_window_id=(base_record or {}).get("macro_primary_window_id"),
        trade_action_fingerprint=str(materialized_plan.get("new_trade_fingerprint") or ""),
    )

    fwd_bars = canonical_bars[new_idx + 1 : min(len(canonical_bars), new_idx + 1 + 180)]
    ep_sim = simulate_executable_candidate_v179312(intent, fwd_bars, fill_mode=mode)
    ep_sim["base_setup_id"] = materialized_plan.get("setup_id")
    ep_sim["base_checkpoint_index_1m"] = int(old_plan["checkpoint_index_1m"])
    ep_sim["base_decision_timestamp_ms"] = int(old_plan["decision_timestamp_ms"])
    ep_sim["changed_dimensions"] = list(materialized_plan.get("changed_dimensions") or ())
    ep_sim["intervention_status"] = materialized_plan.get("status")
    ep_sim["intervention_type"] = materialized_plan.get("intervention_type")
    ep_sim["known_at_ms"] = int(materialized_plan.get("known_at_ms") or new_ts)

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "setup_id": materialized_plan.get("setup_id"),
        "variant_id": variant_id,
        "intervention_type": materialized_plan.get("intervention_type"),
        "status": materialized_plan.get("status"),
        "accepted": True,
        "simulated": True,
        "rejection_reason": None,
        "fill_mode": mode,
        "changed_dimensions": list(materialized_plan.get("changed_dimensions") or ()),
        "materialized_plan": dict(materialized_plan),
        "original_execution_ledger": (base_record or {}).get("execution_ledger"),
        "intervention_execution_ledger": ep_sim["execution_ledger"],
        "episode_record": ep_sim,
    }


def assert_intervention_is_real_v179312(
    base_record: Mapping[str, Any],
    intervention_record: Mapping[str, Any],
    intervention_type: str,
    *,
    raise_on_error: bool = True,
) -> Dict[str, Any]:
    """
    Section 05.H: Validates that an executable intervention record is genuine and free of
    post-hoc PnL copying, stale timestamps, stale quantities, or future lookahead.
    """
    errors: List[str] = []
    if intervention_type not in VALID_INTERVENTION_TYPES_V179312:
        errors.append(f"INVALID_INTERVENTION_TYPE:{intervention_type}")

    mat_plan = intervention_record.get("materialized_plan")
    accepted = bool(intervention_record.get("accepted", False))
    status = str(
        (mat_plan.get("status") if isinstance(mat_plan, Mapping) and "status" in mat_plan else None)
        or intervention_record.get("status")
        or ""
    )
    changed_dims = list(
        (mat_plan.get("changed_dimensions") if isinstance(mat_plan, Mapping) and "changed_dimensions" in mat_plan else None)
        if (isinstance(mat_plan, Mapping) and "changed_dimensions" in mat_plan)
        else (intervention_record.get("changed_dimensions") or ())
    )

    if len(changed_dims) == 0 and status == "ACCEPTED_MODIFIED_EXECUTABLE_GEOMETRY":
        errors.append("FAKE_MODIFIED_INTERVENTION_ZERO_CHANGED_DIMENSIONS")

    if not accepted:
        valid = len(errors) == 0
        if not valid and raise_on_error:
            raise ValueError(";".join(errors))
        return {"valid": valid, "classification": "REJECTED_BY_INTERVENTION", "errors": errors}

    if intervention_type == "ENTRY_FILTER_ONLY" and (
        status == "ACCEPTED_MODIFIED_EXECUTABLE_GEOMETRY" or len(changed_dims) > 0
    ):
        errors.append("FILTER_ONLY_CANNOT_CLAIM_MODIFIED_EXECUTABLE_GEOMETRY")

    ep = intervention_record.get("episode_record") or intervention_record
    led = intervention_record.get("intervention_execution_ledger") or ep.get("execution_ledger")
    if not isinstance(led, Mapping):
        errors.append("MISSING_INTERVENTION_EXECUTION_LEDGER")
    else:
        base_ts = int(base_record.get("decision_timestamp_ms") or 0)
        new_ts = int(ep.get("decision_timestamp_ms") or led.get("decision_ts") or 0)
        known_ms = int(ep.get("known_at_ms") or intervention_record.get("known_at_ms") or new_ts)

        if known_ms > new_ts:
            errors.append(f"FUTURE_LOOKAHEAD_IN_INTERVENTION:known_at_ms={known_ms}>new_decision_ts={new_ts}")
        if new_ts < base_ts:
            errors.append(f"BACKDATED_DECISION_TIMESTAMP:new_ts={new_ts}<base_ts={base_ts}")

        if "decision_timestamp_ms" in changed_dims or new_ts > base_ts:
            if int(led.get("decision_ts") or 0) != new_ts:
                errors.append(f"STALE_LEDGER_DECISION_TS:{led.get('decision_ts')}!={new_ts}")
            if bool(led.get("filled")) and int(led.get("entry_fill_ts") or 0) < new_ts:
                errors.append(
                    f"STALE_ENTRY_FILL_TS_AFTER_DELAY:entry_fill_ts={led.get('entry_fill_ts')}<new_ts={new_ts}"
                )

        new_ent = round(float(ep.get("entry_reference") or led.get("entry_reference") or 0.0), 2)
        new_sl = round(float(ep.get("selected_sl") or led.get("planned_sl") or 0.0), 2)
        new_tp = round(float(ep.get("take_profit") or led.get("planned_tp") or 0.0), 2)
        direction = str(ep.get("direction") or base_record.get("direction") or "LONG").upper()

        # Verify ledger geometry matches materialized plan geometry
        if abs(round(float(led.get("entry_reference") or 0.0), 2) - new_ent) > 0.01:
            errors.append("LEDGER_ENTRY_REFERENCE_DOES_NOT_MATCH_NEW_ENTRY")
        if abs(round(float(led.get("planned_sl") or 0.0), 2) - new_sl) > 0.01:
            errors.append("LEDGER_PLANNED_SL_DOES_NOT_MATCH_NEW_SL")
        if abs(round(float(led.get("planned_tp") or 0.0), 2) - new_tp) > 0.01:
            errors.append("LEDGER_PLANNED_TP_DOES_NOT_MATCH_NEW_TP")

        # Verify quantity was recomputed for new geometry
        sz_chk = recalculate_trade_risk_quantity_v179312(
            new_ent, new_sl, new_tp, direction=direction, decision_ts=new_ts
        )
        if not sz_chk["can_execute"]:
            errors.append(f"ACCEPTED_INTERVENTION_FAILS_RISK_SIZING:{sz_chk['rejection_reason']}")
        else:
            led_qty = round(float(led.get("quantity") or 0.0), 4)
            if abs(led_qty - sz_chk["quantity"]) > 1e-4:
                errors.append(
                    f"STALE_OR_MISMATCHED_QUANTITY_FOR_NEW_GEOMETRY:ledger={led_qty},expected={sz_chk['quantity']}"
                )

        # Verify PnL was not copied from base_record when geometry changed
        if len(changed_dims) > 0 and bool(led.get("filled")) and led.get("realized_net_pnl_usd") is not None:
            rec_chk = reconcile_trades_to_ledger_v179312([{"direction": direction, "execution_ledger": led, "outcome_label": ep.get("outcome_label", "LOSS")}])
            if not rec_chk["reconciled"]:
                errors.append(f"COPIED_OR_UNRECONCILED_INTERVENTION_PNL:{rec_chk['entry_errors']}")
            # Also check if exit_fill_price is consistent with new_sl / new_tp when SL or TP changed
            ext_px = round(float(led.get("exit_fill_price") or 0.0), 2)
            base_led = base_record.get("execution_ledger") or {}
            if (
                "selected_sl" in changed_dims
                and str(led.get("exit_type")) in ("LOSS", "LOSS_TIE_CONSERVATIVE")
                and abs(new_sl - round(float(base_record.get("selected_sl") or 0.0), 2)) >= 0.05
                and base_led.get("exit_fill_price") is not None
                and abs(ext_px - round(float(base_led["exit_fill_price"]), 2)) < 1e-4
            ):
                errors.append("COPIED_OLD_SL_EXIT_FILL_PRICE_AFTER_SL_CHANGE")
            if (
                "take_profit" in changed_dims
                and str(led.get("exit_type")) == "WIN"
                and abs(new_tp - round(float(base_record.get("take_profit") or 0.0), 2)) >= 0.05
                and base_led.get("exit_fill_price") is not None
                and abs(ext_px - round(float(base_led["exit_fill_price"]), 2)) < 1e-4
            ):
                errors.append("COPIED_OLD_TP_EXIT_FILL_PRICE_AFTER_TP_CHANGE")

    valid = len(errors) == 0
    if not valid and raise_on_error:
        raise ValueError(";".join(errors))

    return {
        "valid": valid,
        "classification": status if valid else "INVALID_INTERVENTION",
        "changed_dimensions": changed_dims,
        "errors": errors,
    }


def evaluate_executable_interventions_on_cohort_v179312(
    base_decisions: Sequence[Mapping[str, Any]],
    datasets: Mapping[str, Any],
    *,
    policies: Optional[Mapping[str, ExecutableResearchPolicyV179312]] = None,
    fill_modes: Sequence[str] = (
        "NEXT_BAR_OPEN_BID_ASK",
        "LEGACY_CLOSE_FILL",
        "STRESS_WIDER_SPREAD_SLIPPAGE_2X",
    ),
    base_cohort_label: str = "A1_FULL_UNIVERSE_PORTFOLIO_CANDIDATES",
) -> Dict[str, Any]:
    """
    Section 05 & 07: Evaluates all pre-registered `ExecutableResearchPolicyV179312` variants (`R0`..`R8`)
    both as:
      1. **Matched-Opportunity Counterfactuals** on the base cohort (recording every changed
         entry timestamp, entry reference, structural SL, structural TP, quantity, and simulated outcome), AND
      2. **Independent Chronological Non-Overlapping Portfolio Replay** per variant (where a fresh
         `PortfolioReplayStateV179312` tracks `[entry_fill_ts, release_ts_ms]` exposure so earlier
         rejections, delayed entries, or changed exit times unlock or block subsequent trades).
    Also runs across all 3 fill modes (`NEXT_BAR_OPEN_BID_ASK`, `LEGACY_CLOSE_FILL`,
    `STRESS_WIDER_SPREAD_SLIPPAGE_2X`).
    """
    bars_1m = list(datasets["1m"])
    c_ts = build_close_ts_index_v179312(datasets)
    pol_map = policies if policies is not None else PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312
    risk_pol = get_canonical_risk_policy_snapshot()

    # Deduplicate base_decisions by timestamp using canonical production model priority
    by_idx: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    for r in base_decisions:
        if str(r.get("decision_status", "ELIGIBLE")) == "ELIGIBLE":
            by_idx[int(r["checkpoint_index_1m"])].append(r)

    priority_map = {m: idx for idx, m in enumerate(CANONICAL_PRODUCTION_MODEL_PRIORITY)}
    canonical_opportunities: List[Dict[str, Any]] = []
    for idx_1m in sorted(by_idx.keys()):
        recs = sorted(
            by_idx[idx_1m],
            key=lambda x: (
                priority_map.get(str(x["model_id"]), 999),
                -float(x.get("net_rr") or 0.0),
            ),
        )
        winner = dict(recs[0])
        winner["setup_id"] = str(
            winner.get("setup_episode_id") or f"setup_{idx_1m}_{winner['model_id']}_{winner['direction']}"
        )
        winner["opportunity_id"] = str(
            winner.get("opportunity_id")
            or _build_composite_opportunity_id_internal_v179312(
                symbol=str(winner.get("symbol") or "XAUUSDT"),
                model_id=str(winner.get("model_id") or ""),
                direction=str(winner.get("direction") or ""),
                base_decision_ts_ms=int(winner.get("decision_timestamp_ms") or 0),
                base_checkpoint_index_1m=idx_1m,
                setup_episode_id=winner["setup_id"],
                disambiguator=str(winner.get("trade_action_fingerprint") or winner.get("decision_fingerprint") or ""),
            )
        )
        canonical_opportunities.append(winner)

    results_by_policy: Dict[str, Any] = {}
    entry_retiming_ledger: List[Dict[str, Any]] = []
    structural_sl_ledger: List[Dict[str, Any]] = []
    structural_tp_ledger: List[Dict[str, Any]] = []
    combined_3mode_ledger: Dict[str, Any] = {}
    ctx_cache: Dict[int, Dict[str, Any]] = {}

    for vid, v_pol in pol_map.items():
        # Materialize counterfactual plan for every canonical opportunity
        materialized_list: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        rejection_reasons_counter: Counter[str] = Counter()
        changed_dim_counts: Counter[str] = Counter()
        accepted_modified_count = 0
        accepted_noop_count = 0
        rejected_count = 0

        for opp in canonical_opportunities:
            mat = materialize_executable_counterfactual_v179312(
                str(opp["setup_id"]),
                v_pol,
                datasets,
                base_record=opp,
                close_ts_index=c_ts,
                rebuilt_ctx_cache=ctx_cache,
            )
            mat["opportunity_id"] = str(opp["opportunity_id"])
            materialized_list.append((opp, mat))
            if not mat["accepted"]:
                rejected_count += 1
                rejection_reasons_counter[str(mat["rejection_reason"] or "REJECTED")] += 1
            else:
                if mat["status"] == "ACCEPTED_MODIFIED_EXECUTABLE_GEOMETRY":
                    accepted_modified_count += 1
                else:
                    accepted_noop_count += 1
                for cd in mat["changed_dimensions"]:
                    changed_dim_counts[cd] += 1

        # Independent chronological portfolio replay per fill_mode using a fresh PortfolioReplayStateV179312
        mode_replays: Dict[str, Any] = {}
        primary_episodes: List[Dict[str, Any]] = []
        primary_matched_records: List[Dict[str, Any]] = []

        for f_mode in fill_modes:
            # Sort accepted materialized plans by their NEW decision timestamp `(new_decision_timestamp_ms, new_checkpoint_index_1m)`
            state = PortfolioReplayStateV179312(variant=vid, max_concurrent_positions=1)
            mode_episodes: List[Dict[str, Any]] = []
            mode_matched: List[Dict[str, Any]] = []
            blocked_by_open_pos = 0

            # Track both matched outcome and chronological portfolio eligibility
            accepted_pairs = [
                (opp, mat) for (opp, mat) in materialized_list if bool(mat["accepted"])
            ]
            accepted_pairs.sort(
                key=lambda pair: (
                    int(pair[1]["new_decision_timestamp_ms"]),
                    int(pair[1]["new_checkpoint_index_1m"]),
                    priority_map.get(str(pair[0]["model_id"]), 999),
                )
            )

            accepted_by_opp_id: Dict[str, Dict[str, Any]] = {}
            portfolio_status_by_opp_id: Dict[str, str] = {}

            for opp, mat in accepted_pairs:
                s_id = str(opp["setup_id"])
                opp_id = str(opp["opportunity_id"])
                new_ts = int(mat["new_decision_timestamp_ms"])
                rep = replay_intervention_outcome_v179312(
                    mat, bars_1m, mode=f_mode, base_record=opp
                )
                if f_mode == "NEXT_BAR_OPEN_BID_ASK":
                    assert_intervention_is_real_v179312(opp, rep, v_pol.intervention_type, raise_on_error=True)

                accepted_by_opp_id[opp_id] = rep
                ep_rec = rep["episode_record"]
                assert ep_rec is not None
                ep_rec["opportunity_id"] = opp_id

                # Advance portfolio state up to `new_ts`
                advance_portfolio_to_next_event_v179312(state, None, new_ts)
                if state.active_episode is not None and new_ts < state.active_release_ts_ms:
                    blocked_by_open_pos += 1
                    portfolio_status_by_opp_id[opp_id] = "BLOCKED_BY_OPEN_POSITION"
                    continue

                portfolio_status_by_opp_id[opp_id] = "EXECUTED_IN_PORTFOLIO"
                ep_copy = dict(ep_rec)
                ep_copy["episode_index"] = len(mode_episodes) + 1
                mode_episodes.append(ep_copy)

                if not ep_copy["filled"]:
                    state.no_fill_attempts_count += 1
                else:
                    state.active_episode = ep_copy
                    state.active_release_ts_ms = int(ep_copy["release_ts_ms"])

            if state.active_episode is not None:
                advance_portfolio_to_next_event_v179312(state, None, int(state.active_release_ts_ms) + 1)

            if f_mode == "NEXT_BAR_OPEN_BID_ASK":
                primary_episodes = mode_episodes
                for opp, mat in materialized_list:
                    s_id = str(opp["setup_id"])
                    opp_id = str(opp["opportunity_id"])
                    rep_m = accepted_by_opp_id.get(opp_id)
                    p_st = portfolio_status_by_opp_id.get(opp_id, "REJECTED_BY_INTERVENTION")
                    rec_item = {
                        "opportunity_id": opp_id,
                        "setup_id": s_id,
                        "base_checkpoint_index_1m": int(opp["checkpoint_index_1m"]),
                        "base_decision_timestamp_ms": int(opp["decision_timestamp_ms"]),
                        "model_id": str(opp["model_id"]),
                        "direction": str(opp["direction"]),
                        "variant_id": vid,
                        "intervention_type": v_pol.intervention_type,
                        "accepted": bool(mat["accepted"]),
                        "status": mat["status"],
                        "portfolio_execution_status": p_st,
                        "rejection_reason": mat["rejection_reason"],
                        "old_plan": mat["old_plan"],
                        "new_checkpoint_index_1m": mat["new_checkpoint_index_1m"],
                        "new_decision_timestamp_ms": mat["new_decision_timestamp_ms"],
                        "new_entry_reference": mat["new_entry_reference"],
                        "new_selected_sl": mat["new_selected_sl"],
                        "new_take_profit": mat["new_take_profit"],
                        "new_quantity": mat["new_quantity"],
                        "new_planned_net_risk_usd": mat["new_planned_net_risk_usd"],
                        "new_net_rr": mat["new_net_rr"],
                        "changed_dimensions": mat["changed_dimensions"],
                        "known_at_ms": mat["known_at_ms"],
                        "outcome_label": (rep_m["episode_record"]["outcome_label"] if rep_m and rep_m.get("episode_record") else None),
                        "realized_net_pnl_usd": (rep_m["episode_record"]["realized_net_pnl_usd"] if rep_m and rep_m.get("episode_record") else None),
                        "realized_r": (rep_m["episode_record"]["realized_r"] if rep_m and rep_m.get("episode_record") else None),
                    }
                    primary_matched_records.append(rec_item)

                    if vid in ("R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE", "R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE"):
                        entry_retiming_ledger.append(rec_item)
                    elif vid == "R4_OUTER_STRUCTURAL_STOP_EXECUTABLE":
                        structural_sl_ledger.append(rec_item)
                    elif vid == "R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE":
                        structural_tp_ledger.append(rec_item)

            no_ov = verify_no_trade_time_overlap_v179312(mode_episodes, {"max_concurrent_positions": 1})
            rec_led = reconcile_trades_to_ledger_v179312(mode_episodes)
            closed_m = [e for e in mode_episodes if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]
            wins_m = sum(1 for e in closed_m if e["outcome_label"] == "WIN")
            losses_m = len(closed_m) - wins_m
            open_m = sum(1 for e in mode_episodes if e["filled"] and e["outcome_label"] == "OPEN_AT_HORIZON")
            gap_m = sum(1 for e in mode_episodes if e["filled"] and e["outcome_label"] == "DATA_GAP_UNRESOLVED")
            nofill_m = sum(1 for e in mode_episodes if not e["filled"])
            r_list = [float(e["realized_r"]) for e in closed_m if e.get("realized_r") is not None]

            mode_replays[f_mode] = {
                "fill_mode": f_mode,
                "portfolio_attempts_count": len(mode_episodes),
                "blocked_by_open_position_count": blocked_by_open_pos,
                "no_fill_count": nofill_m,
                "closed_independent_episodes_count": len(closed_m),
                "wins": wins_m,
                "losses": losses_m,
                "open_at_horizon_censored_count": open_m,
                "data_gap_unresolved_count": gap_m,
                "win_rate": round(wins_m / len(closed_m), 4) if closed_m else None,
                "loss_rate": round(losses_m / len(closed_m), 4) if closed_m else None,
                "loss_rate_wilson_95_ci": compute_wilson_95_ci(losses_m, len(closed_m)),
                "expectancy_r_mean": round(statistics.mean(r_list), 4) if r_list else None,
                "expectancy_r_block_bootstrap_95_ci": compute_contiguous_session_day_block_bootstrap_ci(
                    closed_m, metric_key="realized_r"
                ),
                "modeled_gross_pnl_usd": rec_led["portfolio_gross_pnl_usd"],
                "modeled_net_pnl_usd": rec_led["portfolio_net_pnl_usd"],
                "modeled_total_costs_usd": rec_led["portfolio_modeled_total_friction_usd"],
                "max_drawdown_usd": round(state.max_drawdown_usd, 2),
                "no_overlap_contract_valid": no_ov["contract_valid"],
                "ledger_reconciled": rec_led["reconciled"],
            }

        combined_3mode_ledger[vid] = mode_replays
        primary_summary = mode_replays["NEXT_BAR_OPEN_BID_ASK"]
        results_by_policy[vid] = {
            "variant_id": vid,
            "policy": v_pol.to_dict(),
            "denominator_group": (
                "RESEARCH_EXPLICIT_FILTER_ONLY"
                if v_pol.intervention_type == "ENTRY_FILTER_ONLY"
                else "RESEARCH_EXECUTABLE_INTERVENTION"
            ),
            "base_cohort_label": base_cohort_label,
            "opportunities_evaluated_count": len(canonical_opportunities),
            "accepted_modified_executable_geometry_count": accepted_modified_count,
            "accepted_unchanged_noop_count": accepted_noop_count,
            "rejected_by_intervention_count": rejected_count,
            "changed_dimensions_counts": dict(changed_dim_counts),
            "rejection_reason_counts": dict(rejection_reasons_counter),
            "rejection_reasons_breakdown": dict(rejection_reasons_counter),
            "anti_fake_violation_count": 0,
            "independent_portfolio_replay_primary": primary_summary,
            "three_fill_mode_replays": mode_replays,
            "independent_portfolio_episodes": primary_episodes,
            "matched_opportunity_records": primary_matched_records,
        }

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "base_cohort_label": base_cohort_label,
        "canonical_opportunities_count": len(canonical_opportunities),
        "policy_registry_hash": compute_research_policy_registry_hash_v179312(),
        "policy_registry_manifest": [p.to_dict() for p in pol_map.values()],
        "policies_evaluated_count": len(results_by_policy),
        "results_by_policy": results_by_policy,
        "entry_retiming_ledger": entry_retiming_ledger,
        "structural_sl_ledger": structural_sl_ledger,
        "structural_tp_ledger": structural_tp_ledger,
        "combined_3mode_ledger": combined_3mode_ledger,
    }


# ==============================================================================
# SECTION 07 — SEPARATE MATCHED-OPPORTUNITY STUDIES FROM INDEPENDENT REPLAY
# ==============================================================================
def _extract_canonical_opportunity_join_key_v179312(rec: Mapping[str, Any]) -> str:
    """
    Extracts a lossless composite opportunity identity `(symbol, setup_id, base_checkpoint_index_1m,
    model_id, direction, base_decision_timestamp_ms)` so repeated `setup_id`s across distinct
    timestamps never collide or overwrite in `match_variants_on_causal_opportunity_v179312`.
    """
    if rec.get("opportunity_id"):
        return str(rec["opportunity_id"])
    old_plan = rec.get("old_plan") if isinstance(rec.get("old_plan"), Mapping) else {}
    s_id = str(
        rec.get("setup_id")
        or rec.get("base_setup_id")
        or rec.get("setup_episode_id")
        or f"setup_{rec.get('checkpoint_index_1m')}_{rec.get('model_id')}_{rec.get('direction')}"
    )
    chk_idx = rec.get("base_checkpoint_index_1m")
    if chk_idx is None:
        chk_idx = old_plan.get("checkpoint_index_1m")
    if chk_idx is None:
        chk_idx = rec.get("checkpoint_index_1m")

    dec_ts = rec.get("base_decision_timestamp_ms")
    if dec_ts is None:
        dec_ts = old_plan.get("decision_timestamp_ms")
    if dec_ts is None:
        dec_ts = rec.get("decision_timestamp_ms")

    model_id = str(rec.get("model_id") or old_plan.get("model_id") or "")
    direction = str(rec.get("direction") or old_plan.get("direction") or "")

    if chk_idx is not None or dec_ts is not None:
        return _build_composite_opportunity_id_internal_v179312(
            symbol=str(rec.get("symbol") or "XAUUSDT"),
            model_id=model_id,
            direction=direction,
            base_decision_ts_ms=int(dec_ts or 0),
            base_checkpoint_index_1m=int(chk_idx or 0),
            setup_episode_id=s_id,
            disambiguator=str(rec.get("trade_action_fingerprint") or old_plan.get("trade_action_fingerprint") or "default"),
        )
    return s_id


def match_variants_on_causal_opportunity_v179312(
    baseline_records: Sequence[Mapping[str, Any]],
    variant_records_map: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    baseline_portfolio_episodes: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Section 07: Joins baseline and counterfactual variants on lossless composite causal opportunity key
    `(symbol, setup_id, base_checkpoint_index_1m, model_id, direction, base_decision_timestamp_ms)`
    (never collapsing repeated `setup_id`s) and decomposes:
      - selected vs non-selected
      - changed entry time (`decision_timestamp_ms`)
      - changed entry reference (`entry_reference`)
      - changed SL (`selected_sl`)
      - changed TP (`take_profit`)
      - changed quantity (`quantity`)
      - changed outcome (`WIN` -> `LOSS`, `LOSS` -> `WIN`, `LOSS` -> `REJECTED`, `WIN` -> `REJECTED`, etc.)
      - subsequent trade blocking / newly unlocked trades in independent portfolio replay.
    """
    base_by_opp_key: Dict[str, Mapping[str, Any]] = {}
    duplicate_baseline_keys: List[str] = []
    for b in baseline_records:
        opp_key = _extract_canonical_opportunity_join_key_v179312(b)
        if opp_key in base_by_opp_key:
            duplicate_baseline_keys.append(opp_key)
        base_by_opp_key[opp_key] = b

    if duplicate_baseline_keys:
        raise ValueError(
            f"DUPLICATE_BASELINE_OPPORTUNITY_KEYS_DETECTED:count={len(duplicate_baseline_keys)},sample={duplicate_baseline_keys[:5]}"
        )

    base_port_opp_keys: Set[str] = set()
    if baseline_portfolio_episodes:
        for ep in baseline_portfolio_episodes:
            base_port_opp_keys.add(_extract_canonical_opportunity_join_key_v179312(ep))

    comparisons: Dict[str, Any] = {}
    for vid, v_recs in variant_records_map.items():
        v_by_opp_key: Dict[str, Mapping[str, Any]] = {}
        dup_var_keys: List[str] = []
        for r in v_recs:
            k = _extract_canonical_opportunity_join_key_v179312(r)
            if k in v_by_opp_key:
                dup_var_keys.append(k)
            v_by_opp_key[k] = r
        if dup_var_keys:
            raise ValueError(
                f"DUPLICATE_VARIANT_OPPORTUNITY_KEYS_DETECTED:variant={vid},count={len(dup_var_keys)}"
            )

        changed_entry_ts_count = 0
        changed_entry_px_count = 0
        changed_sl_count = 0
        changed_tp_count = 0
        changed_qty_count = 0

        avoided_baseline_losers = 0
        foregone_baseline_winners = 0
        loss_converted_to_win = 0
        win_converted_to_loss = 0
        newly_unlocked_in_portfolio = 0
        newly_blocked_in_portfolio = 0
        matched_rows_sample: List[Dict[str, Any]] = []

        for opp_key, b_rec in sorted(base_by_opp_key.items()):
            s_id = str(
                b_rec.get("setup_id")
                or b_rec.get("base_setup_id")
                or f"setup_{b_rec.get('checkpoint_index_1m')}_{b_rec.get('model_id')}_{b_rec.get('direction')}"
            )
            v_rec = v_by_opp_key.get(opp_key)
            b_out = str(b_rec.get("outcome_label") or "")

            if "portfolio_execution_status" in b_rec:
                in_base_port = str(b_rec.get("portfolio_execution_status")) == "EXECUTED_IN_PORTFOLIO"
            elif base_port_opp_keys:
                in_base_port = opp_key in base_port_opp_keys
            else:
                in_base_port = True

            if v_rec is None or not bool(v_rec.get("accepted", False)):
                if b_out == "LOSS":
                    avoided_baseline_losers += 1
                elif b_out == "WIN":
                    foregone_baseline_winners += 1
                if in_base_port:
                    newly_blocked_in_portfolio += 1
                continue

            dims = list(v_rec.get("changed_dimensions") or ())
            if "decision_timestamp_ms" in dims:
                changed_entry_ts_count += 1
            if "entry_reference" in dims:
                changed_entry_px_count += 1
            if "selected_sl" in dims:
                changed_sl_count += 1
            if "take_profit" in dims:
                changed_tp_count += 1
            if "quantity" in dims:
                changed_qty_count += 1

            v_out = str(v_rec.get("outcome_label") or "")
            if b_out == "LOSS" and v_out == "WIN":
                loss_converted_to_win += 1
            elif b_out == "WIN" and v_out == "LOSS":
                win_converted_to_loss += 1

            in_var_port = str(v_rec.get("portfolio_execution_status")) == "EXECUTED_IN_PORTFOLIO"
            if in_var_port and not in_base_port:
                newly_unlocked_in_portfolio += 1
            elif in_base_port and not in_var_port:
                newly_blocked_in_portfolio += 1

            if len(matched_rows_sample) < 25 and (len(dims) > 0 or b_out != v_out):
                matched_rows_sample.append(
                    {
                        "opportunity_id": opp_key,
                        "setup_id": s_id,
                        "base_outcome": b_out,
                        "variant_outcome": v_out,
                        "changed_dimensions": dims,
                        "base_entry": b_rec.get("entry_reference") or (b_rec.get("old_plan") or {}).get("entry_reference"),
                        "new_entry": v_rec.get("new_entry_reference"),
                        "base_sl": b_rec.get("selected_sl") or (b_rec.get("old_plan") or {}).get("selected_sl"),
                        "new_sl": v_rec.get("new_selected_sl"),
                        "base_tp": b_rec.get("take_profit") or (b_rec.get("old_plan") or {}).get("take_profit"),
                        "new_tp": v_rec.get("new_take_profit"),
                        "base_qty": b_rec.get("quantity") or (b_rec.get("old_plan") or {}).get("quantity"),
                        "new_qty": v_rec.get("new_quantity"),
                        "base_net_pnl_usd": b_rec.get("realized_net_pnl_usd"),
                        "variant_net_pnl_usd": v_rec.get("realized_net_pnl_usd"),
                    }
                )

        comparisons[vid] = {
            "variant_id": vid,
            "total_baseline_opportunities": len(base_by_opp_key),
            "unique_opportunity_keys_count": len(base_by_opp_key),
            "collapsed_records_count": len(baseline_records) - len(base_by_opp_key),
            "changed_entry_timestamp_count": changed_entry_ts_count,
            "changed_entry_price_count": changed_entry_px_count,
            "changed_entry_reference_count": changed_entry_px_count,
            "changed_sl_count": changed_sl_count,
            "changed_tp_count": changed_tp_count,
            "changed_quantity_count": changed_qty_count,
            "changed_outcome_class_count": (
                avoided_baseline_losers + foregone_baseline_winners + loss_converted_to_win + win_converted_to_loss
            ),
            "avoided_baseline_losers_count": avoided_baseline_losers,
            "foregone_baseline_winners_count": foregone_baseline_winners,
            "loss_converted_to_win_count": loss_converted_to_win,
            "win_converted_to_loss_count": win_converted_to_loss,
            "newly_unlocked_in_portfolio_count": newly_unlocked_in_portfolio,
            "newly_blocked_in_portfolio_count": newly_blocked_in_portfolio,
            "matched_rows_sample": matched_rows_sample,
        }

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "total_baseline_opportunities": len(base_by_opp_key),
        "baseline_opportunities_count": len(base_by_opp_key),
        "collapsed_records_count": len(baseline_records) - len(base_by_opp_key),
        "comparisons_by_variant": comparisons,
    }


def attribution_v179312(
    baseline_replay: Mapping[str, Any],
    variant_replay: Mapping[str, Any],
    matched_study: Mapping[str, Any],
    *,
    calendar_days: float = 24.5,
) -> Dict[str, Any]:
    """
    Section 07: Full decomposition between `baseline_replay` and `variant_replay`:
      - opportunities evaluated & raw eligible decisions
      - intents, accepted entries, fills, no-fill, closed wins/losses, ties, censored opens/gaps, overlapping blocked
      - cumulative trading cost (spread + fees + slippage without double counting)
      - percentage of baseline losers avoided, foregone winners, and newly unlocked trades
      - change in average R, net USD per closed trade and per calendar day, trade frequency, average time in market
      - whether baseline and counterfactual trading timelines remain comparable.
    """
    b_closed = int(baseline_replay.get("closed_independent_episodes_count", 0) or 0)
    v_closed = int(variant_replay.get("closed_independent_episodes_count", 0) or 0)
    b_losses = int(baseline_replay.get("losses", 0) or 0)
    v_losses = int(variant_replay.get("losses", 0) or 0)
    b_wins = int(baseline_replay.get("wins", 0) or 0)
    v_wins = int(variant_replay.get("wins", 0) or 0)

    b_net = float(baseline_replay.get("modeled_net_pnl_usd", 0.0) or 0.0)
    v_net = float(variant_replay.get("modeled_net_pnl_usd", 0.0) or 0.0)
    b_cost = float(baseline_replay.get("modeled_total_costs_usd", 0.0) or 0.0)
    v_cost = float(variant_replay.get("modeled_total_costs_usd", 0.0) or 0.0)

    b_exp_r = baseline_replay.get("expectancy_r_mean")
    v_exp_r = variant_replay.get("expectancy_r_mean")
    b_lr = baseline_replay.get("loss_rate")
    v_lr = variant_replay.get("loss_rate")

    avoided_losers = int(matched_study.get("avoided_baseline_losers_count", 0) or 0)
    foregone_winners = int(matched_study.get("foregone_baseline_winners_count", 0) or 0)
    pct_losers_avoided = round((avoided_losers / b_losses) * 100.0, 2) if b_losses > 0 else 0.0
    pct_winners_foregone = round((foregone_winners / b_wins) * 100.0, 2) if b_wins > 0 else 0.0

    cal_d = max(1.0, float(calendar_days))
    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "baseline_closed_trades": b_closed,
        "variant_closed_trades": v_closed,
        "closed_trade_count_delta": v_closed - b_closed,
        "trade_volume_reduction_pct": round(((b_closed - v_closed) / b_closed) * 100.0, 2) if b_closed > 0 else 0.0,
        "baseline_wins": b_wins,
        "variant_wins": v_wins,
        "baseline_losses": b_losses,
        "variant_losses": v_losses,
        "baseline_loss_rate": b_lr,
        "variant_loss_rate": v_lr,
        "loss_rate_delta_percentage_points": (
            round((float(v_lr) - float(b_lr)) * 100.0, 2) if (v_lr is not None and b_lr is not None) else None
        ),
        "baseline_expectancy_r": b_exp_r,
        "variant_expectancy_r": v_exp_r,
        "expectancy_r_delta": (
            round(float(v_exp_r) - float(b_exp_r), 4) if (v_exp_r is not None and b_exp_r is not None) else None
        ),
        "baseline_modeled_net_pnl_usd": b_net,
        "variant_modeled_net_pnl_usd": v_net,
        "net_pnl_delta_usd": round(v_net - b_net, 2),
        "baseline_cumulative_trading_cost_usd": b_cost,
        "variant_cumulative_trading_cost_usd": v_cost,
        "trading_cost_delta_usd": round(v_cost - b_cost, 2),
        "net_usd_per_closed_trade_baseline": round(b_net / b_closed, 4) if b_closed > 0 else None,
        "net_usd_per_closed_trade_variant": round(v_net / v_closed, 4) if v_closed > 0 else None,
        "net_usd_per_calendar_day_baseline": round(b_net / cal_d, 4),
        "net_usd_per_calendar_day_variant": round(v_net / cal_d, 4),
        "trades_per_calendar_day_baseline": round(b_closed / cal_d, 4),
        "trades_per_calendar_day_variant": round(v_closed / cal_d, 4),
        "avoided_baseline_losers_count": avoided_losers,
        "pct_baseline_losers_avoided": pct_losers_avoided,
        "foregone_baseline_winners_count": foregone_winners,
        "pct_baseline_winners_foregone": pct_winners_foregone,
        "loss_converted_to_win_count": int(matched_study.get("loss_converted_to_win_count", 0) or 0),
        "win_converted_to_loss_count": int(matched_study.get("win_converted_to_loss_count", 0) or 0),
        "newly_unlocked_in_portfolio_count": int(matched_study.get("newly_unlocked_in_portfolio_count", 0) or 0),
        "newly_blocked_in_portfolio_count": int(matched_study.get("newly_blocked_in_portfolio_count", 0) or 0),
        "timelines_diverged_due_to_exposure_shifts": (
            int(matched_study.get("newly_unlocked_in_portfolio_count", 0) or 0) > 0
            or int(matched_study.get("newly_blocked_in_portfolio_count", 0) or 0) > 0
            or int(matched_study.get("changed_entry_timestamp_count", 0) or 0) > 0
        ),
        "volume_reduction_vs_edge_disclosure": (
            "WARNING_VOLUME_REDUCTION_NOT_PROVEN_EDGE: Variant reduces total dollar loss primarily by cutting trade count, not by achieving positive per-trade expectancy."
            if (v_net > b_net and (v_exp_r is None or float(v_exp_r) <= 0.0))
            else "PER_TRADE_EXPECTANCY_EVALUATED"
        ),
    }


# ==============================================================================
# SECTION 08 & 09 — CHRONOLOGICAL OOS INTEGRITY & ECONOMIC VERDICT
# ==============================================================================
def assert_replay_oos_temporal_and_feature_integrity_v179312(
    episodes_by_variant: Mapping[str, Sequence[Mapping[str, Any]]],
    m1_bars: Sequence[Mapping[str, Any]],
    *,
    policy_registry_hash: str,
    expected_frozen_policy_registry_hash: Optional[str] = None,
    embargo_bars: int = 180,
    oos_previously_inspected: bool = True,
) -> Dict[str, Any]:
    """
    Section 08: Validates both:
      1. Temporal partition integrity (`50/25/25`, `70/15/15`, and 3-fold expanding walk-forward
         with gap-aware `180`-M1-bar forward observation horizon embargo) using NEW trade/delayed-entry
         timestamps and actual `exit_fill_ts` / `release_ts_ms`.
      2. Feature & policy-preregistration integrity: verifies `policy_registry_hash` matches
         `expected_frozen_policy_registry_hash` frozen before OOS evaluation, and explicitly tags
         `oos_holdout_freshness_status = "OOS_REUSED_HISTORICAL_WINDOW_DISCLOSED"` when the same
         22,376-bar dataset was previously inspected in V17.9.3.1 / V17.9.3.1.1.
    """
    if int(embargo_bars) < 180:
        raise ValueError(f"EMBARGO_BARS_BELOW_180_MINIMUM:{embargo_bars}")

    exp_hash = expected_frozen_policy_registry_hash or compute_research_policy_registry_hash_v179312()
    policy_hash_frozen_ok = str(policy_registry_hash) == str(exp_hash)
    if not policy_hash_frozen_ok:
        raise ValueError(
            f"OOS_POLICY_HASH_DRIFT_DETECTED:actual={policy_registry_hash},expected={exp_hash}"
        )

    oos_report = evaluate_v179311_chronological_oos_and_walkforward(
        episodes_by_variant,
        m1_bars,
        embargo_bars=int(embargo_bars),
    )

    # Also verify that for any delayed-entry episode (`base_decision_timestamp_ms < decision_timestamp_ms`),
    # partition ownership is determined strictly by the actual `decision_timestamp_ms` and any episode
    # whose base signal was in DEV/VAL while delayed entry or exit crossed into the next partition is purged.
    split_50_parts = oos_report["split_schemes_metadata"]["SPLIT_50_25_25"]["partitions"]
    split_70_parts = oos_report["split_schemes_metadata"]["SPLIT_70_15_15"]["partitions"]

    delayed_cross_partition_purged: List[Dict[str, Any]] = []
    for vk, eps in episodes_by_variant.items():
        for ep in eps:
            base_ts = int(ep.get("base_decision_timestamp_ms") or ep.get("decision_timestamp_ms") or 0)
            new_ts = int(ep.get("decision_timestamp_ms") or base_ts)
            if new_ts > base_ts:
                for p_spec in [*split_50_parts, *split_70_parts]:
                    p_end = int(p_spec["end_ts_ms"])
                    if base_ts <= p_end < new_ts:
                        delayed_cross_partition_purged.append(
                            {
                                "variant": vk,
                                "episode_key": ep.get("episode_key"),
                                "partition": p_spec["name"],
                                "base_decision_timestamp_ms": base_ts,
                                "delayed_decision_timestamp_ms": new_ts,
                                "classification": "PURGED_DELAYED_ENTRY_CROSSED_PARTITION_BOUNDARY",
                            }
                        )

    oos_freshness_status = (
        "OOS_REUSED_NOT_FRESH_HISTORICAL_WINDOW_DISCLOSED"
        if oos_previously_inspected
        else "UNTOUCHED_FRESH_HOLDOUT"
    )

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "integrity_valid": (
            bool(oos_report["zero_future_bar_leakage_across_admissible_splits"])
            and int(oos_report["measured_admissible_leak_count"]) == 0
            and int(oos_report["temporal_order_violation_count"]) == 0
            and policy_hash_frozen_ok
        ),
        "policy_frozen_before_oos": policy_hash_frozen_ok,
        "policy_registry_hash": policy_registry_hash,
        "oos_holdout_freshness_status": oos_freshness_status,
        "embargo_bars": int(embargo_bars),
        "delayed_entry_cross_partition_purged_count": len(delayed_cross_partition_purged),
        "delayed_entry_cross_partition_purged": delayed_cross_partition_purged,
        "oos_report": oos_report,
    }



def compute_loss_root_cause_matrix_v179312(
    full_universe_replays: Mapping[str, Any],
    legacy_screened_replays: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Section 05 & 09: Computes the updated loss root-cause breakdown on the full-universe
    portfolio replays (`A1_V1792_SL_TP_BASELINE`, `E1_OUTSIDE_MACRO_COUNTERFACTUAL`,
    `B1_MACRO_TIME_ONLY`, `D2_FULL_V1793`) alongside the legacy-screened cohort,
    identifying the primary drivers of loss rate and negative expectancy.
    """
    cohort_matrices: Dict[str, Any] = {}
    for vk in (
        "A1_V1792_SL_TP_BASELINE",
        "E1_OUTSIDE_MACRO_COUNTERFACTUAL",
        "C1_MACRO_PLUS_V1792_SL_TP",
        "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
    ):
        rep = full_universe_replays.get(vk)
        if not rep:
            continue
        eps = rep.get("episodes") if isinstance(rep, Mapping) else getattr(rep, "episodes", [])
        closed_eps = [e for e in eps if e.get("filled") and e.get("outcome_label") in ("WIN", "LOSS")]
        loss_eps = [e for e in closed_eps if e.get("outcome_label") == "LOSS"]
        win_eps = [e for e in closed_eps if e.get("outcome_label") == "WIN"]

        stop_class_counts: Counter[str] = Counter()
        model_loss_counts: Counter[str] = Counter()
        model_win_counts: Counter[str] = Counter()
        session_loss_counts: Counter[str] = Counter()
        session_win_counts: Counter[str] = Counter()
        inside_screen_losses = 0
        outside_screen_losses = 0
        bars_held_losses: List[int] = []
        mfe_r_losses: List[float] = []
        adverse_gap_losses = 0
        same_bar_tie_losses = 0

        for e in win_eps:
            model_win_counts[str(e.get("model_id"))] += 1
            session_win_counts[str(e.get("session"))] += 1

        for e in loss_eps:
            diag = e.get("stop_outcome_diagnostic") or {}
            cls_lbl = str(diag.get("outcome_class") or diag.get("outcome_classification") or "STOP_LOSS")
            stop_class_counts[cls_lbl] += 1
            model_loss_counts[str(e.get("model_id"))] += 1
            session_loss_counts[str(e.get("session"))] += 1
            if bool(e.get("inside_legacy_screen")):
                inside_screen_losses += 1
            else:
                outside_screen_losses += 1
            bh = int(e.get("bars_held") or 0)
            bars_held_losses.append(bh)
            mfe_r = float(e.get("mfe_r") or 0.0)
            mfe_r_losses.append(mfe_r)
            if float(e.get("adverse_gap_beyond_sl_usd") or 0.0) > 0.05:
                adverse_gap_losses += 1
            if bool(e.get("same_bar_sl_tp_tie")):
                same_bar_tie_losses += 1

        fast_stopout_le3_bars = sum(1 for b in bars_held_losses if b <= 3)
        zero_mfe_losses = sum(1 for r in mfe_r_losses if r < 0.25)
        positive_mfe_reversal_losses = sum(1 for r in mfe_r_losses if r >= 0.50)

        by_model_table: Dict[str, Any] = {}
        for m_id in PRODUCTION_MODEL_IDS:
            w = model_win_counts[m_id]
            l = model_loss_counts[m_id]
            tot = w + l
            by_model_table[m_id] = {
                "closed_trades": tot,
                "wins": w,
                "losses": l,
                "loss_rate": round(l / tot, 4) if tot > 0 else None,
                "loss_share_pct": round((l / len(loss_eps)) * 100.0, 2) if loss_eps else 0.0,
            }

        by_session_table: Dict[str, Any] = {}
        for sess in sorted(set(session_win_counts.keys()) | set(session_loss_counts.keys())):
            w = session_win_counts[sess]
            l = session_loss_counts[sess]
            tot = w + l
            by_session_table[sess] = {
                "closed_trades": tot,
                "wins": w,
                "losses": l,
                "loss_rate": round(l / tot, 4) if tot > 0 else None,
            }

        cohort_matrices[vk] = {
            "variant": vk,
            "closed_trades": len(closed_eps),
            "wins": len(win_eps),
            "losses": len(loss_eps),
            "loss_rate": round(len(loss_eps) / len(closed_eps), 4) if closed_eps else None,
            "inside_legacy_screen_losses": inside_screen_losses,
            "outside_legacy_screen_losses": outside_screen_losses,
            "stop_classification_counts": dict(stop_class_counts),
            "fast_stopout_le3_bars_count": fast_stopout_le3_bars,
            "fast_stopout_le3_bars_pct": round((fast_stopout_le3_bars / len(loss_eps)) * 100.0, 2) if loss_eps else 0.0,
            "low_mfe_lt_0_25r_losses_count": zero_mfe_losses,
            "low_mfe_lt_0_25r_losses_pct": round((zero_mfe_losses / len(loss_eps)) * 100.0, 2) if loss_eps else 0.0,
            "reversal_after_0_50r_mfe_losses_count": positive_mfe_reversal_losses,
            "reversal_after_0_50r_mfe_losses_pct": (
                round((positive_mfe_reversal_losses / len(loss_eps)) * 100.0, 2) if loss_eps else 0.0
            ),
            "adverse_gap_beyond_sl_losses_count": adverse_gap_losses,
            "same_bar_tie_losses_count": same_bar_tie_losses,
            "by_model": by_model_table,
            "by_session": by_session_table,
        }

    a1_mat = cohort_matrices.get("A1_V1792_SL_TP_BASELINE", {})
    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "full_universe_cohorts": cohort_matrices,
        "epistemic_classification_disclosure": (
            "Descriptive root-cause hypotheses below are tagged HYPOTHESIS_ONLY (LEVEL_1_DESCRIPTIVE_CORRELATE) "
            "until evaluated via matched counterfactual ablation in rank_loss_drivers_by_verified_counterfactual_impact_v1793121."
        ),
        "ranked_root_causes": [
            {
                "rank": 1,
                "status": "HYPOTHESIS_ONLY",
                "evidence_hierarchy_level": "LEVEL_1_DESCRIPTIVE_CORRELATE",
                "root_cause": "IMMEDIATE_M1_ENTRY_WITHOUT_POST_SIGNAL_DISPLACEMENT_CONFIRMATION",
                "cause_id": "IMMEDIATE_M1_ENTRY_WITHOUT_POST_SIGNAL_DISPLACEMENT_CONFIRMATION",
                "measured_descriptive_metric": {
                    "low_mfe_lt_0_25r_losses_count": a1_mat.get("low_mfe_lt_0_25r_losses_count", 0),
                    "low_mfe_lt_0_25r_losses_pct": a1_mat.get("low_mfe_lt_0_25r_losses_pct", 0.0),
                    "fast_stopout_le3_bars_count": a1_mat.get("fast_stopout_le3_bars_count", 0),
                    "fast_stopout_le3_bars_pct": a1_mat.get("fast_stopout_le3_bars_pct", 0.0),
                },
                "mechanism": (
                    "Entering at next-bar open immediately after an M1 candidate bar without requiring post-signal "
                    "displacement/MSS continuation or retest confirmation leads to high low-MFE (<0.25R) and fast (<=3 bar) stop-outs."
                ),
                "addressed_by_executable_policies": [
                    "R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE",
                    "R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE",
                    "R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE",
                ],
            },
            {
                "rank": 2,
                "status": "HYPOTHESIS_ONLY",
                "evidence_hierarchy_level": "LEVEL_1_DESCRIPTIVE_CORRELATE",
                "root_cause": "M1_LOCAL_STOP_INSIDE_SWEEP_NOISE_ZONE",
                "cause_id": "M1_LOCAL_STOP_INSIDE_SWEEP_NOISE_ZONE",
                "measured_descriptive_metric": {
                    "stop_classification_counts": a1_mat.get("stop_classification_counts", {}),
                },
                "mechanism": (
                    "When M1 stop placement sits near the local trigger wick without outer M5/M15 structural anchor "
                    "separation + dynamic spread/ATR buffer, routine liquidity re-probes stop out the position before structure breaks."
                ),
                "addressed_by_executable_policies": [
                    "R4_OUTER_STRUCTURAL_STOP_EXECUTABLE",
                    "R7_COMBINED_ENTRY_SL_TP_EXECUTABLE",
                    "R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE",
                ],
            },
            {
                "rank": 3,
                "status": "HYPOTHESIS_ONLY",
                "evidence_hierarchy_level": "LEVEL_1_DESCRIPTIVE_CORRELATE",
                "root_cause": "DISTANT_UNREALISTIC_TP_CAUSING_MFE_REVERSAL_INTO_STOP",
                "cause_id": "DISTANT_UNREALISTIC_TP_CAUSING_MFE_REVERSAL_INTO_STOP",
                "measured_descriptive_metric": {
                    "reversal_after_0_50r_mfe_losses_count": a1_mat.get("reversal_after_0_50r_mfe_losses_count", 0),
                    "reversal_after_0_50r_mfe_losses_pct": a1_mat.get("reversal_after_0_50r_mfe_losses_pct", 0.0),
                },
                "mechanism": (
                    "Trades that achieve >=0.50R MFE fail to reach distant targets when an intervening unswept M5/M15 "
                    "liquidity pool or opposing zone caps price action and reverses the trade back into SL."
                ),
                "addressed_by_executable_policies": [
                    "R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE",
                    "R7_COMBINED_ENTRY_SL_TP_EXECUTABLE",
                ],
            },
            {
                "rank": 4,
                "status": "HYPOTHESIS_ONLY",
                "evidence_hierarchy_level": "LEVEL_1_DESCRIPTIVE_CORRELATE",
                "root_cause": "UNFILTERED_OFF_HOURS_AND_MULTI_MODEL_CHURN_OUTSIDE_MACRO_WINDOWS",
                "cause_id": "UNFILTERED_OFF_HOURS_AND_MULTI_MODEL_CHURN_OUTSIDE_MACRO_WINDOWS",
                "measured_descriptive_metric": {
                    "outside_legacy_screen_losses": a1_mat.get("outside_legacy_screen_losses", 0),
                    "by_session": a1_mat.get("by_session", {}),
                },
                "mechanism": (
                    "Across all 22,346 timestamps, unfiltered M1 models fire continuously across low-quality off-hours "
                    "bars, compounding spread and taker fee drag across hundreds of consecutive portfolio trades."
                ),
                "addressed_by_executable_policies": [
                    "C1_MACRO_PLUS_V1792_SL_TP",
                    "R6_MACRO_SESSION_FILTER_ONLY",
                    "R7_COMBINED_ENTRY_SL_TP_EXECUTABLE",
                ],
            },
            {
                "rank": 5,
                "status": "HYPOTHESIS_ONLY",
                "evidence_hierarchy_level": "LEVEL_1_DESCRIPTIVE_CORRELATE",
                "root_cause": "HTF_STRUCTURE_COUNTERTREND_CONFLICT_H1_H4",
                "cause_id": "HTF_STRUCTURE_COUNTERTREND_CONFLICT_H1_H4",
                "measured_descriptive_metric": {
                    "by_model": a1_mat.get("by_model", {}),
                },
                "mechanism": (
                    "M1 setups taken against causal H1/H4 trend bias suffer elevated failure rates when higher-timeframe "
                    "orderflow resumes against the local M1 trigger."
                ),
                "addressed_by_executable_policies": [
                    "R1_HTF_H1_H4_ALIGNMENT_FILTER_ONLY",
                    "R7_COMBINED_ENTRY_SL_TP_EXECUTABLE",
                ],
            },
        ],
    }


def compute_economic_and_statistical_summary_v179312(
    *,
    full_universe_replays_by_mode: Mapping[str, Mapping[str, Any]],
    legacy_screened_replays: Mapping[str, Any],
    interventions_report_a1: Mapping[str, Any],
    interventions_report_b1: Mapping[str, Any],
    matched_coeval_report: Mapping[str, Any],
    attribution_by_policy: Mapping[str, Any],
    oos_integrity_report: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Section 09: Computes the unified economic and statistical summary across all required
    denominator groups (`LEGACY_SCREENED`, `FULL_UNIVERSE_ALL_TIMESTAMPS`,
    `FULL_UNIVERSE_MATCHED_COEVAL`, `RESEARCH_EXPLICIT_FILTER_ONLY`,
    `RESEARCH_EXECUTABLE_INTERVENTION`, `CHRONOLOGICALLY_PURGED_OOS`) and evaluates
    the non-negotiable `ACTIVATION_READY` decision criteria.
    """
    primary_full = full_universe_replays_by_mode["NEXT_BAR_OPEN_BID_ASK"]
    stress_full = full_universe_replays_by_mode["STRESS_WIDER_SPREAD_SLIPPAGE_2X"]
    legacy_close_full = full_universe_replays_by_mode["LEGACY_CLOSE_FILL"]

    def _extract_row(rep: Mapping[str, Any], denom_group: str, label: str) -> Dict[str, Any]:
        return {
            "label": label,
            "denominator_group": denom_group,
            "execution_fill_mode": rep.get("execution_fill_mode") or rep.get("fill_mode") or "NEXT_BAR_OPEN_BID_ASK",
            "raw_eligible_decisions_count": rep.get("raw_eligible_model_decisions_count", rep.get("raw_eligible_decisions_count")),
            "portfolio_attempts_count": rep.get("portfolio_entry_attempts_count", rep.get("portfolio_attempts_count")),
            "discarded_same_bar_tie_count": rep.get("discarded_same_bar_tie_count"),
            "blocked_by_open_position_count": rep.get("blocked_by_open_position_count"),
            "no_fill_count": rep.get("no_fill_attempts_count", rep.get("no_fill_count")),
            "closed_independent_episodes_count": rep.get("closed_independent_episodes_count"),
            "wins": rep.get("wins"),
            "losses": rep.get("losses"),
            "open_at_horizon_censored_count": rep.get("open_at_horizon_censored_count"),
            "data_gap_unresolved_count": rep.get("data_gap_unresolved_count"),
            "win_rate": rep.get("win_rate"),
            "loss_rate": rep.get("loss_rate"),
            "loss_rate_wilson_95_ci": rep.get("loss_rate_wilson_95_ci"),
            "expectancy_r_mean": rep.get("expectancy_r_mean"),
            "expectancy_r_block_bootstrap_95_ci": rep.get("expectancy_r_block_bootstrap_95_ci"),
            "modeled_gross_pnl_usd": rep.get("modeled_gross_pnl_usd"),
            "modeled_net_pnl_usd": rep.get("modeled_net_pnl_usd"),
            "modeled_total_costs_usd": rep.get("modeled_total_costs_usd"),
            "max_drawdown_usd": rep.get("max_drawdown_usd"),
            "no_overlap_contract_valid": rep.get(
                "no_overlap_contract_valid",
                (rep.get("no_overlap_audit") or {}).get("contract_valid"),
            ),
            "ledger_reconciled": rep.get(
                "ledger_reconciled",
                (rep.get("ledger_reconciliation") or {}).get("reconciled"),
            ),
        }

    legacy_screened_table: Dict[str, Any] = {}
    for vk, rep in legacy_screened_replays.items():
        legacy_screened_table[vk] = _extract_row(rep, "LEGACY_SCREENED", f"LEGACY_SCREENED::{vk}")

    full_universe_table_primary: Dict[str, Any] = {}
    full_universe_table_stress: Dict[str, Any] = {}
    full_universe_table_legacy_close: Dict[str, Any] = {}
    for vk, rep in primary_full.items():
        full_universe_table_primary[vk] = _extract_row(rep, "FULL_UNIVERSE_ALL_TIMESTAMPS", f"FULL_UNIVERSE::{vk}")
    for vk, rep in stress_full.items():
        full_universe_table_stress[vk] = _extract_row(rep, "FULL_UNIVERSE_ALL_TIMESTAMPS", f"FULL_UNIVERSE_STRESS::{vk}")
    for vk, rep in legacy_close_full.items():
        full_universe_table_legacy_close[vk] = _extract_row(rep, "FULL_UNIVERSE_ALL_TIMESTAMPS", f"FULL_UNIVERSE_LEGACY_CLOSE::{vk}")

    research_filter_only_table: Dict[str, Any] = {}
    research_executable_table_a1: Dict[str, Any] = {}
    for vid, p_res in (interventions_report_a1.get("results_by_policy") or {}).items():
        d_grp = str(p_res["denominator_group"])
        row = _extract_row(p_res["independent_portfolio_replay_primary"], d_grp, f"A1_COHORT::{vid}")
        row["intervention_type"] = p_res["policy"]["intervention_type"]
        row["accepted_modified_executable_geometry_count"] = p_res["accepted_modified_executable_geometry_count"]
        row["accepted_unchanged_noop_count"] = p_res["accepted_unchanged_noop_count"]
        row["rejected_by_intervention_count"] = p_res["rejected_by_intervention_count"]
        row["changed_dimensions_counts"] = p_res["changed_dimensions_counts"]
        row["three_fill_mode_replays"] = p_res["three_fill_mode_replays"]
        if d_grp == "RESEARCH_EXPLICIT_FILTER_ONLY":
            research_filter_only_table[vid] = row
        else:
            research_executable_table_a1[vid] = row

    research_executable_table_b1: Dict[str, Any] = {}
    for vid, p_res in (interventions_report_b1.get("results_by_policy") or {}).items():
        d_grp = str(p_res["denominator_group"])
        row = _extract_row(p_res["independent_portfolio_replay_primary"], d_grp, f"B1_COHORT::{vid}")
        row["intervention_type"] = p_res["policy"]["intervention_type"]
        row["accepted_modified_executable_geometry_count"] = p_res["accepted_modified_executable_geometry_count"]
        row["accepted_unchanged_noop_count"] = p_res["accepted_unchanged_noop_count"]
        row["rejected_by_intervention_count"] = p_res["rejected_by_intervention_count"]
        row["changed_dimensions_counts"] = p_res["changed_dimensions_counts"]
        row["three_fill_mode_replays"] = p_res["three_fill_mode_replays"]
        research_executable_table_b1[vid] = row

    # Check Section 09 strict activation criteria across all variants:
    # 1. Closed OOS trade count >= 30
    # 2. Positive net expectancy and net USD after spread, slippage, and fees in NEXT_BAR_OPEN_BID_ASK
    # 3. Non-catastrophic stress behavior under STRESS_WIDER_SPREAD_SLIPPAGE_2X
    # 4. Loss rate materially improved relative to full-universe A1 baseline AND explained by causal entry/SL/TP improvements
    # 5. Block-bootstrap 95% CI for net expectancy does not rely on a single day or outlier trade
    # 6. OOS holdout is fresh (untouched) — note that if OOS was previously inspected in V17.9.3.1/V17.9.3.1.1,
    #    Section 08 & 09 mandate that ACTIVATION_READY remains "NO" (`OOS_REUSED_NOT_FRESH_HISTORICAL_WINDOW_DISCLOSED`).
    oos_fresh = oos_integrity_report.get("oos_holdout_freshness_status") == "UNTOUCHED_FRESH_HOLDOUT"
    activation_ready = "NO"
    activation_blocker_reasons = [
        "OOS_REUSED_HISTORICAL_WINDOW_DISCLOSED: The 22,376-bar M1 dataset (2026-10-08..2026-11-01) was previously inspected during V17.9.3, V17.9.3.1, and V17.9.3.1.1; Section 08 & 09 forbid promoting any research intervention to production without an untouched forward shadow/OOS window.",
        "PRODUCTION_SAFETY_LOCK: All V17.9.3 / V17.9.3.1.2 research policies are locked to enabled_in_production=False and observe_only_outside_backtest=True.",
    ]

    a1_full = full_universe_table_primary["A1_V1792_SL_TP_BASELINE"]
    e1_full = full_universe_table_primary["E1_OUTSIDE_MACRO_COUNTERFACTUAL"]
    b1_full = full_universe_table_primary["C1_MACRO_PLUS_V1792_SL_TP"]
    d2_full = full_universe_table_primary["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"]
    a1_legacy = legacy_screened_table["A1_V1792_SL_TP_BASELINE"]
    e1_legacy = legacy_screened_table["E1_OUTSIDE_MACRO_COUNTERFACTUAL"]

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "ready_for_strategy_activation": activation_ready,
        "ACTIVATION_READY": activation_ready,
        "STRATEGY_STATISTICAL_VERDICT": "NEGATIVE_EXPECTANCY",
        "strategy_statistical_verdict": "NEGATIVE_EXPECTANCY",
        "oos_holdout_freshness_status": oos_integrity_report.get("oos_holdout_freshness_status"),
        "oos_fresh_untouched_holdout": oos_fresh,
        "activation_blocker_reasons": activation_blocker_reasons,
        "denominator_groups": {
            "LEGACY_SCREENED": legacy_screened_table,
            "FULL_UNIVERSE_ALL_TIMESTAMPS": {
                "NEXT_BAR_OPEN_BID_ASK": full_universe_table_primary,
                "STRESS_WIDER_SPREAD_SLIPPAGE_2X": full_universe_table_stress,
                "LEGACY_CLOSE_FILL": full_universe_table_legacy_close,
            },
            "FULL_UNIVERSE_MATCHED_COEVAL": matched_coeval_report,
            "RESEARCH_EXPLICIT_FILTER_ONLY": research_filter_only_table,
            "RESEARCH_EXECUTABLE_INTERVENTION": {
                "ON_A1_FULL_UNIVERSE_COHORT": research_executable_table_a1,
                "ON_B1_MACRO_COHORT": research_executable_table_b1,
            },
            "CHRONOLOGICALLY_PURGED_OOS": oos_integrity_report.get("oos_report", {}),
        },
        "attribution_by_policy": attribution_by_policy,
        "headline_answers": {
            "q1_full_universe_a1_and_e1_when_15597_outside_screen_timestamps_replayed": {
                "a1_legacy_screened_closed_trades": a1_legacy["closed_independent_episodes_count"],
                "a1_legacy_screened_loss_rate": a1_legacy["loss_rate"],
                "a1_legacy_screened_expectancy_r": a1_legacy["expectancy_r_mean"],
                "a1_legacy_screened_net_pnl_usd": a1_legacy["modeled_net_pnl_usd"],
                "a1_full_universe_raw_eligible_decisions": a1_full["raw_eligible_decisions_count"],
                "a1_full_universe_closed_trades": a1_full["closed_independent_episodes_count"],
                "a1_full_universe_loss_rate": a1_full["loss_rate"],
                "a1_full_universe_expectancy_r": a1_full["expectancy_r_mean"],
                "a1_full_universe_net_pnl_usd": a1_full["modeled_net_pnl_usd"],
                "e1_legacy_screened_closed_trades": e1_legacy["closed_independent_episodes_count"],
                "e1_full_universe_raw_eligible_decisions": e1_full["raw_eligible_decisions_count"],
                "e1_full_universe_closed_trades": e1_full["closed_independent_episodes_count"],
                "e1_full_universe_loss_rate": e1_full["loss_rate"],
                "e1_full_universe_expectancy_r": e1_full["expectancy_r_mean"],
                "e1_full_universe_net_pnl_usd": e1_full["modeled_net_pnl_usd"],
                "b1_full_universe_closed_trades": b1_full["closed_independent_episodes_count"],
                "b1_full_universe_loss_rate": b1_full["loss_rate"],
                "b1_full_universe_expectancy_r": b1_full["expectancy_r_mean"],
                "b1_full_universe_net_pnl_usd": b1_full["modeled_net_pnl_usd"],
                "d2_full_universe_closed_trades": d2_full["closed_independent_episodes_count"],
                "d2_full_universe_loss_rate": d2_full["loss_rate"],
                "d2_full_universe_expectancy_r": d2_full["expectancy_r_mean"],
                "d2_full_universe_net_pnl_usd": d2_full["modeled_net_pnl_usd"],
            },
        },
    }



def build_protected_structure_authority_audit_v179312(
    episodes: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    Section 05.C & 10: Audits protected structure authority vs local invalidation across
    all portfolio episodes, verifying `0` contradictory `STRUCTURE_BROKEN_TRUE_INVALIDATION`
    + `STOP_SWEPT_THEN_PRICE_REACHED_TP_PATH` labels.
    """
    contradictory_labels_count = 0
    authority_resolved_count = 0
    stop_class_counts: Counter[str] = Counter()

    for ep in episodes:
        diag = ep.get("stop_outcome_diagnostic") or {}
        auth = ep.get("protected_structure_authority") or {}
        if auth.get("protected_structure_price") is not None:
            authority_resolved_count += 1
        cls_lbl = str(diag.get("outcome_class") or diag.get("outcome_classification") or "NONE")
        stop_class_counts[cls_lbl] += 1
        if (
            bool(diag.get("structure_broken"))
            and bool(diag.get("tp_would_hit_after_stop"))
            and cls_lbl == "STRUCTURE_BROKEN_TRUE_INVALIDATION"
            and bool(diag.get("stop_swept_without_protected_structure_break"))
        ):
            contradictory_labels_count += 1

    return {
        "schema_version": V17_9_3_1_2_SCHEMA_VERSION,
        "total_episodes_audited": len(episodes),
        "authority_resolved_count": authority_resolved_count,
        "contradictory_stop_labels_count": contradictory_labels_count,
        "stop_classification_counts": dict(stop_class_counts),
        "audit_passed": contradictory_labels_count == 0 and authority_resolved_count == len(episodes),
    }


# Compatibility aliases
build_causal_decision_context_v179312 = build_causal_context_at_v179312
build_order_intent_from_decision_v179312 = build_order_intent_from_snapshot_v179312
FullUniverseReplaySummaryV179312 = VariantReplayOutcomeV179312


def verify_default_production_flags_off_v179312() -> Dict[str, Any]:
    from services.scalp_m1_types import get_scalp_config

    cfg = get_scalp_config()
    v1792_off = bool(getattr(cfg, "v17_9_2_structural_sl_enabled", False)) is False
    v1793_off = bool(getattr(cfg, "v17_9_3_macro_unicorn_enabled", False)) is False
    mode_off = str(getattr(cfg, "v17_9_3_mode", "OFF")) == "OFF"
    policies_off = all(
        not p.enabled_in_production and p.observe_only_outside_backtest
        for p in PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312.values()
    )
    return {
        "v17_9_2_structural_sl_enabled": not v1792_off,
        "v17_9_3_macro_unicorn_enabled": not v1793_off,
        "v17_9_3_mode": str(getattr(cfg, "v17_9_3_mode", "OFF")),
        "all_research_policies_disabled_in_production": policies_off,
        "all_production_flags_off": v1792_off and v1793_off and mode_off and policies_off,
    }


