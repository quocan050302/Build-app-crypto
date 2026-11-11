"""
backend/services/v17_9_2_structural_stop_intelligence.py
AURUM DESK V17.9.2 — Causal Structure-Valid SL/TP & Trade Invalidation Intelligence.

Pure, deterministic, side-effect-free calculation and forensic classification module:
  - Causal multi-timeframe structural stop anchor candidate collection (`collect_causal_stop_anchor_candidates`)
  - Deterministic structure-valid anchor ranking (`rank_structure_valid_anchors`)
  - Provenance-verified ATR + spread noise buffer calculation (`compute_causal_noise_buffer`)
  - Outward tick-quantized structure-valid stop derivation (`derive_structure_valid_stop`)
  - Fail-closed invalidation contract validator (`validate_structure_invalidation_contract`)
  - Causal liquidity target candidate resolution & cost-viable target selection
    (`resolve_causal_target_candidates`, `select_viable_structural_target`)
  - Authoritative cost-aware Net R:R and dynamic position sizing (`evaluate_stop_tp_risk_and_size`)
  - Post-entry outcome-only forensic classification & premature stop rate measurement
    (`classify_structure_stop_outcome`, `measure_premature_stop_rate`)

Zero database mutations, zero network I/O, zero wall-clock dependencies, zero lookahead.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from domain_calculator import CostAssumptions, calculate_risk_reward, validate_price_geometry
from services.liquidity_scenario_service import identify_confirmed_swings
from services.stop_placement_service import (
    TIMEFRAME_MS,
    _candle_ohlc,
    _candle_open_ms,
    quantize_stop_outward,
    validate_anchor_not_invalidated,
)
from services.stop_placement_types import (
    StopAnchor,
    StopAnchorKind,
)

V17_9_2_ALGORITHM_VERSION = "V17_9_2"
V1792_ALGORITHM_VERSION = V17_9_2_ALGORITHM_VERSION


def _is_valid_finite_price(val: Any) -> bool:
    if val is None or isinstance(val, bool):
        return False
    try:
        f = float(val)
        return math.isfinite(f) and f > 0.0
    except (TypeError, ValueError):
        return False


def _candle_close_ms(bar: Any, tf_ms: int = 60_000) -> int:
    if type(bar) is dict:
        ct = bar.get("close_time")
        if ct is not None and not isinstance(ct, bool):
            return int(ct)
        ot = bar.get("open_time", bar.get("timestamp", 0))
        return int(ot) + tf_ms
    if isinstance(bar, Mapping):
        ct = bar.get("close_time")
        if ct is not None and not isinstance(ct, bool):
            return int(ct)
        ot = bar.get("open_time", bar.get("timestamp", 0))
        return int(ot) + tf_ms
    ct = getattr(bar, "close_time", None)
    if ct is not None and not isinstance(ct, bool):
        return int(ct)
    ot = getattr(bar, "open_time", getattr(bar, "timestamp", 0))
    return int(ot) + tf_ms


def _candle_is_closed(bar: Any, cutoff_ms: int, tf_ms: int = 60_000) -> bool:
    if type(bar) is dict:
        is_c = bar.get("is_closed", True)
    elif isinstance(bar, Mapping):
        is_c = bar.get("is_closed", True)
    else:
        is_c = getattr(bar, "is_closed", True)
    if is_c is False:
        return False
    return _candle_close_ms(bar, tf_ms) <= int(cutoff_ms)


def _canonical_json_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


@dataclass(frozen=True)
class StructuralStopCandidate:
    anchor_id: str
    anchor_kind: str
    anchor_price: float
    timeframe: str
    occurred_at_ms: int
    known_at_ms: int
    source_bar_open_ms: int
    source_bar_close_ms: int
    is_closed_bar: bool = True
    is_causal: bool = True
    is_invalidated: bool = False
    is_protected_swing: bool = False
    is_local_sweep_only: bool = False
    structural_rank: int = 50
    source_leg_id: Optional[str] = None
    source_break_id: Optional[str] = None
    model_compatibility: Tuple[str, ...] = ()
    rejection_reasons: Tuple[str, ...] = ()
    algorithm_version: str = V17_9_2_ALGORITHM_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "anchor_kind": self.anchor_kind,
            "anchor_price": round(float(self.anchor_price), 4) if _is_valid_finite_price(self.anchor_price) else None,
            "timeframe": self.timeframe,
            "occurred_at_ms": int(self.occurred_at_ms),
            "known_at_ms": int(self.known_at_ms),
            "source_bar_open_ms": int(self.source_bar_open_ms),
            "source_bar_close_ms": int(self.source_bar_close_ms),
            "is_closed_bar": bool(self.is_closed_bar),
            "is_causal": bool(self.is_causal),
            "is_invalidated": bool(self.is_invalidated),
            "is_protected_swing": bool(self.is_protected_swing),
            "is_local_sweep_only": bool(self.is_local_sweep_only),
            "structural_rank": int(self.structural_rank),
            "source_leg_id": self.source_leg_id,
            "source_break_id": self.source_break_id,
            "model_compatibility": list(self.model_compatibility),
            "rejection_reasons": list(self.rejection_reasons),
            "algorithm_version": self.algorithm_version,
        }


@dataclass(frozen=True)
class StructuralTargetPlanV1792:
    status: str  # "VALID" | "UNAVAILABLE" | "REJECTED"
    target_price: Optional[float]
    selected_target_id: Optional[str]
    source_kind: Optional[str]
    timeframe: Optional[str]
    occurred_at_ms: Optional[int]
    known_at_ms: Optional[int]
    reward_distance: Optional[float]
    gross_rr: float = 0.0
    net_rr: float = 0.0
    target_mode: str = "BASELINE_FROZEN"
    candidates_evaluated: int = 0
    rejection_reasons: Tuple[str, ...] = ()
    algorithm_version: str = V17_9_2_ALGORITHM_VERSION

    @property
    def authoritative_level_id(self) -> Optional[str]:
        return self.selected_target_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "target_price": round(float(self.target_price), 2) if self.target_price is not None else None,
            "selected_target_id": self.selected_target_id,
            "authoritative_level_id": self.selected_target_id,
            "source_kind": self.source_kind,
            "timeframe": self.timeframe,
            "occurred_at_ms": self.occurred_at_ms,
            "known_at_ms": self.known_at_ms,
            "reward_distance": round(float(self.reward_distance), 4) if self.reward_distance is not None else None,
            "gross_rr": round(float(self.gross_rr), 4),
            "net_rr": round(float(self.net_rr), 4),
            "target_mode": self.target_mode,
            "candidates_evaluated": int(self.candidates_evaluated),
            "rejection_reasons": list(self.rejection_reasons),
            "algorithm_version": self.algorithm_version,
        }


@dataclass(frozen=True)
class StructuralStopPlanV1792:
    status: str  # "ELIGIBLE" | "VALID_SETUP_REJECTED_BY_RISK" | "INVALID_STOP"
    v1792_mode: str
    model_id: str
    symbol: str
    direction: str
    entry_price: float
    decision_timestamp_ms: int
    selected_anchor: Optional[StructuralStopCandidate]
    frozen_anchor_id: Optional[str]
    invalidation_price: Optional[float]
    noise_buffer_price: float
    buffer_components: Dict[str, float]
    dominant_buffer_component: str
    proposed_sl: Optional[float]
    stop_distance: Optional[float]
    baseline_sl: Optional[float]
    baseline_anchor_id: Optional[str]
    target_plan: Optional[StructuralTargetPlanV1792]
    hypothetical_quantity: float
    gross_rr: float
    net_rr: float
    net_risk_usdt: float
    net_reward_usdt: float
    rejection_reasons: Tuple[str, ...] = ()
    candidates: Tuple[StructuralStopCandidate, ...] = ()
    observe_metadata_fingerprint: str = ""
    algorithm_version: str = V17_9_2_ALGORITHM_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "v1792_mode": self.v1792_mode,
            "model_id": self.model_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": round(float(self.entry_price), 2),
            "decision_timestamp_ms": int(self.decision_timestamp_ms),
            "selected_anchor": self.selected_anchor.to_dict() if self.selected_anchor else None,
            "frozen_anchor_id": self.frozen_anchor_id,
            "invalidation_price": round(float(self.invalidation_price), 2) if self.invalidation_price is not None else None,
            "noise_buffer_price": round(float(self.noise_buffer_price), 4),
            "buffer_components": {k: round(float(v), 4) for k, v in self.buffer_components.items()},
            "dominant_buffer_component": self.dominant_buffer_component,
            "proposed_sl": round(float(self.proposed_sl), 2) if self.proposed_sl is not None else None,
            "stop_distance": round(float(self.stop_distance), 4) if self.stop_distance is not None else None,
            "baseline_sl": round(float(self.baseline_sl), 2) if self.baseline_sl is not None else None,
            "baseline_anchor_id": self.baseline_anchor_id,
            "target_plan": self.target_plan.to_dict() if self.target_plan else None,
            "hypothetical_quantity": round(float(self.hypothetical_quantity), 6),
            "gross_rr": round(float(self.gross_rr), 4),
            "net_rr": round(float(self.net_rr), 4),
            "net_risk_usdt": round(float(self.net_risk_usdt), 4),
            "net_reward_usdt": round(float(self.net_reward_usdt), 4),
            "rejection_reasons": list(self.rejection_reasons),
            "candidates_count": len(self.candidates),
            "candidates": [c.to_dict() for c in self.candidates[:12]],
            "observe_metadata_fingerprint": self.observe_metadata_fingerprint,
            "algorithm_version": self.algorithm_version,
        }


@dataclass(frozen=True)
class StopOutcomeDiagnostic:
    episode_id: str
    model_id: str
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    invalidation_price: float
    protected_structure_price: float
    decision_timestamp_ms: int
    outcome_class: str
    stop_hit: bool
    stop_hit_bar_ts: Optional[int]
    tp_hit: bool
    tp_hit_bar_ts: Optional[int]
    structure_broken: bool
    structure_break_bar_ts: Optional[int]
    tp_would_hit_after_stop: bool
    mfe_usd: float
    mae_usd: float
    mfe_r: float
    mae_r: float
    bars_held: int
    provenance: str = "HISTORICAL_BACKTEST_V1792"
    sub_class: str = "NONE"
    mfe_before_stop_usd: float = 0.0
    mfe_after_stop_usd: float = 0.0
    has_data_gap_before_exit: bool = False
    max_bar_gap_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "model_id": self.model_id,
            "direction": self.direction,
            "entry_price": round(float(self.entry_price), 2),
            "sl_price": round(float(self.sl_price), 2),
            "tp_price": round(float(self.tp_price), 2),
            "invalidation_price": round(float(self.invalidation_price), 2),
            "protected_structure_price": round(float(self.protected_structure_price), 2),
            "decision_timestamp_ms": int(self.decision_timestamp_ms),
            "outcome_class": self.outcome_class,
            "sub_class": self.sub_class,
            "stop_hit": bool(self.stop_hit),
            "stop_hit_bar_ts": self.stop_hit_bar_ts,
            "tp_hit": bool(self.tp_hit),
            "tp_hit_bar_ts": self.tp_hit_bar_ts,
            "structure_broken": bool(self.structure_broken),
            "structure_break_bar_ts": self.structure_break_bar_ts,
            "tp_would_hit_after_stop": bool(self.tp_would_hit_after_stop),
            "mfe_usd": round(float(self.mfe_usd), 4),
            "mae_usd": round(float(self.mae_usd), 4),
            "mfe_r": round(float(self.mfe_r), 4),
            "mae_r": round(float(self.mae_r), 4),
            "mfe_before_stop_usd": round(float(self.mfe_before_stop_usd), 4),
            "mfe_after_stop_usd": round(float(self.mfe_after_stop_usd), 4),
            "has_data_gap_before_exit": bool(self.has_data_gap_before_exit),
            "max_bar_gap_ms": int(self.max_bar_gap_ms),
            "bars_held": int(self.bars_held),
            "provenance": self.provenance,
        }


def collect_causal_stop_anchor_candidates(
    *,
    model_id: str,
    direction: str,
    entry_price: float,
    decision_timestamp_ms: int,
    closed_m1: Sequence[Any] = (),
    closed_m5: Sequence[Any] = (),
    closed_m15: Sequence[Any] = (),
    sh_1m: Sequence[Any] = (),
    sl_1m: Sequence[Any] = (),
    selected_fvg: Optional[Mapping[str, Any]] = None,
    selected_break: Optional[Mapping[str, Any]] = None,
    selected_sweep: Optional[Mapping[str, Any]] = None,
    selected_reference: Optional[Mapping[str, Any]] = None,
    unicorn_evidence: Optional[Any] = None,
    baseline_anchor: Optional[StopAnchor] = None,
    tick_size: float = 0.01,
    enforce_cross_tf_invalidation_check: bool = True,
) -> List[StructuralStopCandidate]:
    """
    Section 4.2: Collect causal stop anchor candidates from closed M1/M5/M15 market structure.
    Strictly enforces `known_at_ms <= decision_timestamp_ms` and `source_bar_close_ms <= decision_timestamp_ms`.
    Distinguishes outer protected swings (`is_protected_swing=True`) from internal/local sweep extremes
    (`is_local_sweep_only=True`).
    """
    norm_dir = str(direction or "LONG").strip().upper()
    cutoff_ms = int(decision_timestamp_ms)
    strictly_closed_m1 = [b for b in closed_m1 if _candle_is_closed(b, cutoff_ms, 60_000)]
    strictly_closed_m5 = [b for b in closed_m5 if _candle_is_closed(b, cutoff_ms, 300_000)]
    strictly_closed_m15 = [b for b in closed_m15 if _candle_is_closed(b, cutoff_ms, 900_000)]

    candidates: List[StructuralStopCandidate] = []
    seen_ids = set()

    def _add_candidate(
        *,
        anchor_id: str,
        anchor_kind: str,
        anchor_price: Any,
        timeframe: str,
        occurred_at_ms: int,
        known_at_ms: int,
        source_bar_open_ms: int,
        source_bar_close_ms: int,
        is_closed_bar: bool,
        is_protected_swing: bool,
        is_local_sweep_only: bool,
        structural_rank: int,
        source_leg_id: Optional[str] = None,
        source_break_id: Optional[str] = None,
        model_compatibility: Tuple[str, ...] = (),
    ) -> None:
        if not anchor_id or anchor_id in seen_ids:
            return
        seen_ids.add(anchor_id)

        rej: List[str] = []
        if not _is_valid_finite_price(anchor_price):
            rej.append("NON_FINITE_ANCHOR_PRICE")
            px = 0.0
        else:
            px = round(float(anchor_price), 4)

        if not is_closed_bar or int(source_bar_close_ms) > cutoff_ms:
            rej.append("UNCLOSED_BAR_ANCHOR_REJECTED")
        if int(known_at_ms) > cutoff_ms or int(occurred_at_ms) > cutoff_ms or int(occurred_at_ms) > int(known_at_ms):
            rej.append("FUTURE_ANCHOR_REJECTED")

        if px > 0.0 and _is_valid_finite_price(entry_price):
            if norm_dir == "LONG" and px >= float(entry_price):
                rej.append("ANCHOR_WRONG_SIDE_OF_ENTRY")
            elif norm_dir == "SHORT" and px <= float(entry_price):
                rej.append("ANCHOR_WRONG_SIDE_OF_ENTRY")

        # Check whether subsequent closed M1/M5/M15 bars after occurred_at_ms already invalidated this anchor
        is_inv = False
        if px > 0.0 and not rej and (strictly_closed_m1 or strictly_closed_m5 or strictly_closed_m15):
            temp_anc = StopAnchor(
                id=anchor_id,
                kind=anchor_kind,
                price=px,
                timeframe=timeframe,
                occurred_at_ms=int(occurred_at_ms),
                known_at_ms=int(known_at_ms),
                source_leg_id=str(source_leg_id or anchor_id),
                source_break_id=source_break_id,
            )
            inv_ok = True
            if strictly_closed_m1:
                inv_ok = validate_anchor_not_invalidated(
                    temp_anc, strictly_closed_m1, norm_dir, cutoff_ms, bar_duration_ms=60_000
                )
            if inv_ok and enforce_cross_tf_invalidation_check:
                tf_up = str(timeframe or "1M").strip().upper()
                if tf_up in ("5M", "15M") and strictly_closed_m5:
                    inv_ok = validate_anchor_not_invalidated(
                        temp_anc, strictly_closed_m5, norm_dir, cutoff_ms, bar_duration_ms=300_000
                    )
                if inv_ok and tf_up == "15M" and strictly_closed_m15:
                    inv_ok = validate_anchor_not_invalidated(
                        temp_anc, strictly_closed_m15, norm_dir, cutoff_ms, bar_duration_ms=900_000
                    )
            if not inv_ok:
                is_inv = True
                rej.append("ANCHOR_ALREADY_INVALIDATED")

        is_causal = ("FUTURE_ANCHOR_REJECTED" not in rej) and ("UNCLOSED_BAR_ANCHOR_REJECTED" not in rej)
        candidates.append(
            StructuralStopCandidate(
                anchor_id=str(anchor_id),
                anchor_kind=str(anchor_kind),
                anchor_price=px,
                timeframe=str(timeframe),
                occurred_at_ms=int(occurred_at_ms),
                known_at_ms=int(known_at_ms),
                source_bar_open_ms=int(source_bar_open_ms),
                source_bar_close_ms=int(source_bar_close_ms),
                is_closed_bar=bool(is_closed_bar),
                is_causal=is_causal,
                is_invalidated=is_inv,
                is_protected_swing=bool(is_protected_swing),
                is_local_sweep_only=bool(is_local_sweep_only),
                structural_rank=int(structural_rank),
                source_leg_id=source_leg_id,
                source_break_id=source_break_id,
                model_compatibility=model_compatibility,
                rejection_reasons=tuple(dict.fromkeys(rej)),
            )
        )

    # 1. Unicorn Breaker-FVG distal boundary (highest priority for UNICORN_BREAKER_FVG)
    if unicorn_evidence is not None and getattr(unicorn_evidence, "is_valid", True):
        distal_px = getattr(unicorn_evidence, "breaker_distal_price", getattr(unicorn_evidence, "invalidation_price", None))
        uni_known = int(getattr(unicorn_evidence, "known_at_ms", getattr(unicorn_evidence, "breaker_confirmed_at_ms", cutoff_ms)) or cutoff_ms)
        uni_occ = int(getattr(unicorn_evidence, "breaker_confirmed_at_ms", uni_known) or uni_known)
        if _is_valid_finite_price(distal_px):
            _add_candidate(
                anchor_id=f"v1792_uni_{getattr(unicorn_evidence, 'unicorn_id', 'brk')}",
                anchor_kind=StopAnchorKind.BREAKER_DISTAL_BOUNDARY.value,
                anchor_price=float(distal_px),
                timeframe="1M",
                occurred_at_ms=uni_occ,
                known_at_ms=uni_known,
                source_bar_open_ms=uni_occ - 60_000,
                source_bar_close_ms=uni_known,
                is_closed_bar=uni_known <= cutoff_ms,
                is_protected_swing=True,
                is_local_sweep_only=False,
                structural_rank=98,
                source_leg_id=str(getattr(unicorn_evidence, "breaker_id", "uni_leg")),
                model_compatibility=("UNICORN_BREAKER_FVG", "SMC_M1_FVG_CONTINUATION", "SMC_M1_CONTINUATION"),
            )

    # 2–4. Confirmed M5, M15, and M1 swings (common across all models at the same cutoff_ms)
    m1_kind = StopAnchorKind.PROTECTED_SWING_LOW.value if norm_dir == "LONG" else StopAnchorKind.PROTECTED_SWING_HIGH.value
    common_memo = getattr(collect_causal_stop_anchor_candidates, "_COMMON_SWING_ANC_MEMO", None)
    if common_memo is None:
        common_memo = {}
        setattr(collect_causal_stop_anchor_candidates, "_COMMON_SWING_ANC_MEMO", common_memo)
    common_key = (
        norm_dir,
        round(float(entry_price), 4) if _is_valid_finite_price(entry_price) else None,
        cutoff_ms,
        len(strictly_closed_m1),
        _candle_close_ms(strictly_closed_m1[-1], 60_000) if strictly_closed_m1 else 0,
        _candle_ohlc(strictly_closed_m1[-1]) if strictly_closed_m1 else None,
        _candle_ohlc(strictly_closed_m1[0]) if strictly_closed_m1 else None,
        len(strictly_closed_m5),
        _candle_ohlc(strictly_closed_m5[-1]) if strictly_closed_m5 else None,
        len(strictly_closed_m15),
        _candle_ohlc(strictly_closed_m15[-1]) if strictly_closed_m15 else None,
        len(sh_1m),
        len(sl_1m),
        round(float(tick_size), 4),
        bool(enforce_cross_tf_invalidation_check),
    )
    common_hit = common_memo.get(common_key)
    if common_hit is not None:
        common_cands, outer_m1_extreme = common_hit
        for cc in common_cands:
            if cc.anchor_id not in seen_ids:
                seen_ids.add(cc.anchor_id)
                candidates.append(cc)
    else:
        pre_common_len = len(candidates)
        htf_sw_memo = getattr(collect_causal_stop_anchor_candidates, "_HTF_SW_MEMO", None)
        if htf_sw_memo is None:
            htf_sw_memo = {}
            setattr(collect_causal_stop_anchor_candidates, "_HTF_SW_MEMO", htf_sw_memo)
        if len(strictly_closed_m5) >= 3:
            m5_k = ("5M", len(strictly_closed_m5), _candle_close_ms(strictly_closed_m5[-1], 300_000), _candle_ohlc(strictly_closed_m5[-1]), _candle_ohlc(strictly_closed_m5[0]))
            m5_hit = htf_sw_memo.get(m5_k)
            if m5_hit is not None:
                sh_5m, sl_5m = m5_hit
            else:
                sh_5m, sl_5m = identify_confirmed_swings(
                    strictly_closed_m5, timeframe="5M", left_bars=1, right_bars=1, as_of_ms=cutoff_ms
                )
                if len(htf_sw_memo) >= 64:
                    htf_sw_memo.clear()
                htf_sw_memo[m5_k] = (sh_5m, sl_5m)
            m5_swings = sl_5m if norm_dir == "LONG" else sh_5m
            m5_kind = StopAnchorKind.PROTECTED_SWING_LOW.value if norm_dir == "LONG" else StopAnchorKind.PROTECTED_SWING_HIGH.value
            for sw in m5_swings:
                sw_px = float(sw.get("price", 0.0))
                sw_occ = int(sw.get("occurred_at_ms", sw.get("timestamp", 0)))
                sw_known = int(sw.get("known_at_ms", sw_occ + 300_000))
                _add_candidate(
                    anchor_id=f"v1792_m5_{sw.get('id', sw_occ)}",
                    anchor_kind=m5_kind,
                    anchor_price=sw_px,
                    timeframe="5M",
                    occurred_at_ms=sw_occ,
                    known_at_ms=sw_known,
                    source_bar_open_ms=sw_occ,
                    source_bar_close_ms=sw_known,
                    is_closed_bar=sw_known <= cutoff_ms,
                    is_protected_swing=True,
                    is_local_sweep_only=False,
                    structural_rank=92,
                    source_leg_id=str(sw.get("id", f"m5_{sw_occ}")),
                    model_compatibility=(
                        "SMC_M1_REVERSAL",
                        "SMC_M1_M5_LIQUIDITY_REVERSAL",
                        "SMC_M1_FVG_CONTINUATION",
                        "SMC_M1_CONTINUATION",
                        "SMC_M1_BREAKOUT_RETEST",
                    ),
                )

        if len(strictly_closed_m15) >= 3:
            m15_k = ("15M", len(strictly_closed_m15), _candle_close_ms(strictly_closed_m15[-1], 900_000), _candle_ohlc(strictly_closed_m15[-1]), _candle_ohlc(strictly_closed_m15[0]))
            m15_hit = htf_sw_memo.get(m15_k)
            if m15_hit is not None:
                sh_15m, sl_15m = m15_hit
            else:
                sh_15m, sl_15m = identify_confirmed_swings(
                    strictly_closed_m15, timeframe="15M", left_bars=1, right_bars=1, as_of_ms=cutoff_ms
                )
                if len(htf_sw_memo) >= 64:
                    htf_sw_memo.clear()
                htf_sw_memo[m15_k] = (sh_15m, sl_15m)
            m15_swings = sl_15m if norm_dir == "LONG" else sh_15m
            m15_kind = StopAnchorKind.PROTECTED_SWING_LOW.value if norm_dir == "LONG" else StopAnchorKind.PROTECTED_SWING_HIGH.value
            for sw in m15_swings:
                sw_px = float(sw.get("price", 0.0))
                sw_occ = int(sw.get("occurred_at_ms", sw.get("timestamp", 0)))
                sw_known = int(sw.get("known_at_ms", sw_occ + 900_000))
                _add_candidate(
                    anchor_id=f"v1792_m15_{sw.get('id', sw_occ)}",
                    anchor_kind=m15_kind,
                    anchor_price=sw_px,
                    timeframe="15M",
                    occurred_at_ms=sw_occ,
                    known_at_ms=sw_known,
                    source_bar_open_ms=sw_occ,
                    source_bar_close_ms=sw_known,
                    is_closed_bar=sw_known <= cutoff_ms,
                    is_protected_swing=True,
                    is_local_sweep_only=False,
                    structural_rank=88,
                    source_leg_id=str(sw.get("id", f"m15_{sw_occ}")),
                    model_compatibility=(
                        "SMC_M1_REVERSAL",
                        "SMC_M1_M5_LIQUIDITY_REVERSAL",
                        "SMC_M1_FVG_CONTINUATION",
                        "SMC_M1_CONTINUATION",
                        "SMC_M1_BREAKOUT_RETEST",
                    ),
                )

        raw_m1_swings = list(sl_1m if norm_dir == "LONG" else sh_1m)
        if not raw_m1_swings and len(strictly_closed_m1) >= 5:
            sh_auto, sl_auto = identify_confirmed_swings(
                strictly_closed_m1, timeframe="1M", left_bars=2, right_bars=2, as_of_ms=cutoff_ms
            )
            raw_m1_swings = list(sl_auto if norm_dir == "LONG" else sh_auto)

        valid_m1_prices = [
            float(s.get("price", 0.0))
            for s in raw_m1_swings
            if _is_valid_finite_price(s.get("price"))
            and int(s.get("known_at_ms", s.get("occurred_at_ms", 0))) <= cutoff_ms
            and (
                (norm_dir == "LONG" and float(s.get("price")) < float(entry_price))
                if _is_valid_finite_price(entry_price)
                else True
            )
            and (
                (norm_dir == "SHORT" and float(s.get("price")) > float(entry_price))
                if _is_valid_finite_price(entry_price)
                else True
            )
        ]
        outer_m1_extreme: Optional[float] = None
        if valid_m1_prices:
            outer_m1_extreme = min(valid_m1_prices) if norm_dir == "LONG" else max(valid_m1_prices)

        m1_kind = StopAnchorKind.PROTECTED_SWING_LOW.value if norm_dir == "LONG" else StopAnchorKind.PROTECTED_SWING_HIGH.value
        for sw in raw_m1_swings:
            sw_id = str(sw.get("id", f"m1_sw_{sw.get('occurred_at_ms', 0)}"))
            sw_px = float(sw.get("price", 0.0))
            sw_occ = int(sw.get("occurred_at_ms", sw.get("timestamp", 0)))
            sw_known = int(sw.get("known_at_ms", sw_occ + 120_000))
            is_outer = bool(outer_m1_extreme is not None and abs(sw_px - outer_m1_extreme) <= tick_size * 1.5)
            _add_candidate(
                anchor_id=sw_id,
                anchor_kind=m1_kind,
                anchor_price=sw_px,
                timeframe="1M",
                occurred_at_ms=sw_occ,
                known_at_ms=sw_known,
                source_bar_open_ms=sw_occ,
                source_bar_close_ms=sw_known,
                is_closed_bar=sw_known <= cutoff_ms,
                is_protected_swing=is_outer,
                is_local_sweep_only=not is_outer,
                structural_rank=90 if is_outer else 72,
                source_leg_id=sw_id,
                model_compatibility=(
                    "SMC_M1_FVG_CONTINUATION",
                    "SMC_M1_CONTINUATION",
                    "SMC_M1_BREAKOUT_RETEST",
                    "SMC_M1_REVERSAL",
                    "SMC_M1_M5_LIQUIDITY_REVERSAL",
                ),
            )
        if len(common_memo) >= 16:
            common_memo.clear()
        common_memo[common_key] = (tuple(candidates[pre_common_len:]), outer_m1_extreme)

    # 5. Baseline anchor (e.g. local sweep extreme or FVG displacement origin)
    if baseline_anchor is not None:
        is_sweep_only = baseline_anchor.kind == StopAnchorKind.SWEEP_EXTREME.value
        if outer_m1_extreme is not None:
            if norm_dir == "LONG" and float(baseline_anchor.price) > outer_m1_extreme + tick_size * 2:
                is_sweep_only = True
            elif norm_dir == "SHORT" and float(baseline_anchor.price) < outer_m1_extreme - tick_size * 2:
                is_sweep_only = True
        _add_candidate(
            anchor_id=str(baseline_anchor.id),
            anchor_kind=str(baseline_anchor.kind),
            anchor_price=float(baseline_anchor.price),
            timeframe=str(baseline_anchor.timeframe or "1M"),
            occurred_at_ms=int(baseline_anchor.occurred_at_ms),
            known_at_ms=int(baseline_anchor.known_at_ms),
            source_bar_open_ms=int(baseline_anchor.occurred_at_ms) - 60_000,
            source_bar_close_ms=int(baseline_anchor.known_at_ms),
            is_closed_bar=int(baseline_anchor.known_at_ms) <= cutoff_ms,
            is_protected_swing=not is_sweep_only,
            is_local_sweep_only=is_sweep_only,
            structural_rank=74 if is_sweep_only else 89,
            source_leg_id=baseline_anchor.source_leg_id,
            source_break_id=baseline_anchor.source_break_id,
            model_compatibility=(model_id,),
        )

    # 6. Check if any unclosed bar was passed in closed_m1 and record an explicit rejected candidate for audit
    for idx_b, b in enumerate(closed_m1):
        if not _candle_is_closed(b, cutoff_ms, 60_000):
            _, h_b, l_b, _ = _candle_ohlc(b)
            open_ms = _candle_open_ms(b)
            close_ms = _candle_close_ms(b, 60_000)
            _add_candidate(
                anchor_id=f"unclosed_bar_{open_ms}_{idx_b}",
                anchor_kind=m1_kind,
                anchor_price=l_b if norm_dir == "LONG" else h_b,
                timeframe="1M",
                occurred_at_ms=open_ms,
                known_at_ms=close_ms,
                source_bar_open_ms=open_ms,
                source_bar_close_ms=close_ms,
                is_closed_bar=False,
                is_protected_swing=False,
                is_local_sweep_only=True,
                structural_rank=0,
            )

    return candidates


def rank_structure_valid_anchors(
    candidates: Sequence[StructuralStopCandidate],
    *,
    model_id: str,
    direction: str,
    entry_price: float,
    decision_timestamp_ms: int,
    tick_size: float = 0.01,
    max_stop_distance_usd: float = 35.0,
) -> Tuple[Optional[StructuralStopCandidate], List[StructuralStopCandidate], Tuple[str, ...]]:
    """
    Section 4.3: Deterministic ranking of causal structural stop anchor candidates.
    Prioritizes:
      1. Valid causal, closed-bar, un-invalidated candidates on the protective side of entry
         within `max_stop_distance_usd`
      2. Protected swings (`is_protected_swing=True`, `is_local_sweep_only=False`) over internal/local sweep wicks
      3. Higher `structural_rank`
      4. More recent `occurred_at_ms`
      5. Deterministic tie-break by `anchor_id` ascending
    Never ranks by future outcome.
    """
    norm_dir = str(direction or "LONG").strip().upper()
    cutoff_ms = int(decision_timestamp_ms)

    valid_list: List[StructuralStopCandidate] = []
    invalid_list: List[StructuralStopCandidate] = []
    rejection_acc: List[str] = []

    for c in candidates:
        reasons = list(c.rejection_reasons)
        if not c.is_causal or not c.is_closed_bar or c.known_at_ms > cutoff_ms or c.source_bar_close_ms > cutoff_ms:
            if "FUTURE_ANCHOR_REJECTED" not in reasons:
                reasons.append("FUTURE_ANCHOR_REJECTED")
        if c.is_invalidated:
            if "ANCHOR_ALREADY_INVALIDATED" not in reasons:
                reasons.append("ANCHOR_ALREADY_INVALIDATED")
        if not _is_valid_finite_price(c.anchor_price):
            if "NON_FINITE_ANCHOR_PRICE" not in reasons:
                reasons.append("NON_FINITE_ANCHOR_PRICE")
        elif _is_valid_finite_price(entry_price):
            if norm_dir == "LONG" and c.anchor_price >= float(entry_price) - tick_size:
                reasons.append("ANCHOR_WRONG_SIDE_OF_ENTRY")
            elif norm_dir == "SHORT" and c.anchor_price <= float(entry_price) + tick_size:
                reasons.append("ANCHOR_WRONG_SIDE_OF_ENTRY")
            dist = abs(float(entry_price) - float(c.anchor_price))
            if dist > max_stop_distance_usd:
                reasons.append("STRUCTURAL_SL_TOO_WIDE")

        if reasons:
            updated_c = (
                c
                if tuple(reasons) == c.rejection_reasons
                else StructuralStopCandidate(**{**c.__dict__, "rejection_reasons": tuple(dict.fromkeys(reasons))})
            )
            invalid_list.append(updated_c)
            rejection_acc.extend(reasons)
        else:
            valid_list.append(c)

    def _sort_key(cand: StructuralStopCandidate) -> Tuple[Any, ...]:
        compat_bonus = 1 if (not cand.model_compatibility or model_id in cand.model_compatibility) else 0
        prot_bonus = 1 if (cand.is_protected_swing and not cand.is_local_sweep_only) else 0
        # Prefer M1 outer protected or M5 protected that isn't excessively far
        tf_bonus = 2 if cand.timeframe in ("1M", "5M") else 1
        return (
            -prot_bonus,
            -compat_bonus,
            -int(cand.structural_rank),
            -tf_bonus,
            -int(cand.occurred_at_ms),
            str(cand.anchor_id),
        )

    ranked_valid = sorted(valid_list, key=_sort_key)
    ranked_invalid = sorted(invalid_list, key=lambda x: str(x.anchor_id))
    all_ranked = ranked_valid + ranked_invalid

    if not ranked_valid:
        term_reasons = tuple(dict.fromkeys(rejection_acc)) or ("NO_CAUSAL_STRUCTURAL_ANCHOR",)
        return None, all_ranked, term_reasons

    return ranked_valid[0], all_ranked, ()


def compute_causal_noise_buffer(
    *,
    atr_m1: Optional[float],
    atr_m5: Optional[float] = None,
    spread: Optional[float] = 0.20,
    tick_size: float = 0.01,
    buffer_mode: str = "STRUCTURE_PLUS_NOISE",
    anchor_timeframe: str = "1M",
    k_m1: float = 0.20,
    k_m5: float = 0.12,
    k_spread: float = 1.25,
    min_buffer_ticks: int = 5,
    max_buffer_usd: float = 2.50,
) -> Tuple[float, Dict[str, float], str]:
    """
    Section 4.4: Compute causal noise buffer beyond structural invalidation price.
    - Never fabricates `atr_m5 = atr_m1 * 2.1` when `atr_m5` is missing.
    - Monotonic in `atr_m1`, `atr_m5`, and `spread`.
    - In `STRUCTURE_ONLY` mode, returns minimal 1-tick structural clearance (`tick_size`).
    """
    eff_tick = float(tick_size) if (_is_valid_finite_price(tick_size) and float(tick_size) > 0) else 0.01
    mode_norm = str(buffer_mode or "STRUCTURE_PLUS_NOISE").strip().upper()
    if mode_norm == "STRUCTURE_ONLY":
        comp = {"structure_only_tick": round(eff_tick, 4)}
        return round(eff_tick, 4), comp, "structure_only_tick"

    tick_floor = round(max(1, int(min_buffer_ticks)) * eff_tick, 4)
    components: Dict[str, float] = {"tick_floor": tick_floor}

    if _is_valid_finite_price(spread):
        components["spread_component"] = round(float(spread) * float(k_spread), 4)

    if _is_valid_finite_price(atr_m1):
        components["atr_m1_component"] = round(float(atr_m1) * float(k_m1), 4)

    if _is_valid_finite_price(atr_m5):
        components["atr_m5:"] = round(float(atr_m5) * float(k_m5), 4)
        # Keep clean key name `atr_m5_component` while ensuring `"atr_m5"` substring check works
        components.pop("atr_m5:", None)
        components["atr_m5_component"] = round(float(atr_m5) * float(k_m5), 4)

    dominant_key = max(components.keys(), key=lambda k: components[k])
    raw_buf = max(components.values())
    bounded_buf = min(float(max_buffer_usd), max(tick_floor, raw_buf))

    # Quantize buffer upward to nearest tick
    d_buf = Decimal(str(bounded_buf))
    d_tick = Decimal(str(eff_tick))
    q_buf = float((d_buf / d_tick).to_integral_value(rounding=ROUND_CEILING) * d_tick)
    return round(q_buf, 4), components, dominant_key


def derive_structure_valid_stop(
    *,
    direction: str,
    invalidation_price: float,
    noise_buffer_price: float,
    tick_size: float = 0.01,
    entry_price: Optional[float] = None,
) -> Optional[float]:
    """
    Section 4.5: Derive outward tick-quantized structure-valid stop price:
      - LONG: `floor_tick(invalidation_price - noise_buffer_price)`
      - SHORT: `ceil_tick(invalidation_price + noise_buffer_price)`
    """
    if not _is_valid_finite_price(invalidation_price):
        return None
    if noise_buffer_price is None or isinstance(noise_buffer_price, bool) or not math.isfinite(float(noise_buffer_price)) or float(noise_buffer_price) < 0.0:
        return None
    if not _is_valid_finite_price(tick_size):
        return None

    norm_dir = str(direction or "").strip().upper()
    inv_d = Decimal(str(invalidation_price))
    buf_d = Decimal(str(max(float(tick_size), float(noise_buffer_price))))
    tick_d = Decimal(str(tick_size))

    if norm_dir == "LONG":
        raw_sl = inv_d - buf_d
        q_sl = float((raw_sl / tick_d).to_integral_value(rounding=ROUND_FLOOR) * tick_d)
        if q_sl <= 0.0:
            return None
        if _is_valid_finite_price(entry_price) and q_sl >= float(entry_price):
            return None
        return round(q_sl, 4)
    elif norm_dir == "SHORT":
        raw_sl = inv_d + buf_d
        q_sl = float((raw_sl / tick_d).to_integral_value(rounding=ROUND_CEILING) * tick_d)
        if _is_valid_finite_price(entry_price) and q_sl <= float(entry_price):
            return None
        return round(q_sl, 4)
    return None


def validate_structure_invalidation_contract(
    *,
    direction: str,
    entry_price: Any,
    proposed_sl: Any,
    anchor: Optional[StructuralStopCandidate],
    noise_buffer_price: Any,
    decision_timestamp_ms: int,
    tick_size: float = 0.01,
    max_stop_distance_usd: float = 35.0,
) -> Tuple[bool, Tuple[str, ...]]:
    """
    Section 4.6: Fail-closed validator verifying all structural stop and anchor invariants.
    """
    reasons: List[str] = []
    norm_dir = str(direction or "").strip().upper()
    if norm_dir not in ("LONG", "SHORT"):
        reasons.append("INVALID_DIRECTION")

    if not _is_valid_finite_price(tick_size):
        reasons.append("INVALID_TICK_SIZE")

    if (
        not _is_valid_finite_price(entry_price)
        or not _is_valid_finite_price(proposed_sl)
        or anchor is None
        or not _is_valid_finite_price(anchor.anchor_price)
        or noise_buffer_price is None
        or isinstance(noise_buffer_price, bool)
        or not math.isfinite(float(noise_buffer_price))
        or float(noise_buffer_price) <= 0.0
    ):
        reasons.append("NON_FINITE_STOP_OR_ANCHOR")
        return False, tuple(dict.fromkeys(reasons))

    ent = float(entry_price)
    sl = float(proposed_sl)
    inv = float(anchor.anchor_price)
    buf = float(noise_buffer_price)
    cutoff = int(decision_timestamp_ms)

    if not anchor.is_closed_bar or int(anchor.source_bar_close_ms) > cutoff:
        reasons.append("UNCLOSED_BAR_ANCHOR_REJECTED")
    if not anchor.is_causal or int(anchor.known_at_ms) > cutoff or int(anchor.occurred_at_ms) > int(anchor.known_at_ms):
        reasons.append("FUTURE_ANCHOR_REJECTED")
    if anchor.is_invalidated:
        reasons.append("ANCHOR_ALREADY_INVALIDATED")

    if norm_dir == "LONG":
        if sl >= ent:
            reasons.append("SL_WRONG_SIDE_OF_ENTRY")
        if inv >= ent:
            reasons.append("ANCHOR_WRONG_SIDE_OF_ENTRY")
        if sl > inv - buf + 1e-6:
            reasons.append("SL_INSIDE_PROTECTED_INVALIDATION")
    elif norm_dir == "SHORT":
        if sl <= ent:
            reasons.append("SL_WRONG_SIDE_OF_ENTRY")
        if inv <= ent:
            reasons.append("ANCHOR_WRONG_SIDE_OF_ENTRY")
        if sl < inv + buf - 1e-6:
            reasons.append("SL_INSIDE_PROTECTED_INVALIDATION")

    stop_dist = abs(ent - sl)
    if stop_dist > float(max_stop_distance_usd):
        reasons.append("STRUCTURAL_SL_TOO_WIDE")
    if stop_dist < float(tick_size):
        reasons.append("STOP_TOO_TIGHT")

    return len(reasons) == 0, tuple(dict.fromkeys(reasons))


def resolve_causal_target_candidates(
    *,
    direction: str,
    entry_price: float,
    decision_timestamp_ms: int,
    liquidity_pools: Sequence[Any] = (),
    closed_m1: Sequence[Any] = (),
    closed_m5: Sequence[Any] = (),
    closed_m15: Sequence[Any] = (),
    closed_h1: Sequence[Any] = (),
    closed_h4: Sequence[Any] = (),
    closed_d1: Sequence[Any] = (),
    atr_m1: Optional[float] = None,
    tick_size: float = 0.01,
    enforce_cross_tf_sweep_check: bool = True,
) -> List[Dict[str, Any]]:
    """
    Section 4.7: Resolve causal unconsumed liquidity target candidates from:
      - Provided `liquidity_pools` (M1/M5/M15/H1/H4/PDH/PDL)
      - Confirmed closed M1, M5, M15, H1, H4 swings (`known_at_ms <= decision_timestamp_ms`)
      - Completed prior-day D1 High/Low (`PDH`/`PDL` with `close_time <= decision_timestamp_ms`)
    Strictly filters out consumed/swept/expired levels across all closed LTF/HTF bars, future levels,
    and wrong-side levels.
    """
    norm_dir = str(direction or "LONG").strip().upper()
    cutoff_ms = int(decision_timestamp_ms)
    if not _is_valid_finite_price(entry_price):
        return []
    ent = float(entry_price)
    min_dist = max(float(atr_m1) * 1.0, 0.50) if _is_valid_finite_price(atr_m1) else 0.50
    expected_side = "BUY_SIDE" if norm_dir == "LONG" else "SELL_SIDE"
    terminal_states = {"SWEPT", "CONSUMED", "INVALIDATED", "EXPIRED", "MITIGATED"}

    strictly_closed_by_tf = {
        "1M": [b for b in (closed_m1 or ()) if _candle_is_closed(b, cutoff_ms, 60_000)],
        "5M": [b for b in (closed_m5 or ()) if _candle_is_closed(b, cutoff_ms, 300_000)],
        "15M": [b for b in (closed_m15 or ()) if _candle_is_closed(b, cutoff_ms, 900_000)],
        "1H": [b for b in (closed_h1 or ()) if _candle_is_closed(b, cutoff_ms, 3_600_000)],
        "4H": [b for b in (closed_h4 or ()) if _candle_is_closed(b, cutoff_ms, 14_400_000)],
    }
    tf_triples = {
        tf_k: [(_candle_open_ms(b), _candle_ohlc(b)[1], _candle_ohlc(b)[2]) for b in tf_v]
        for tf_k, tf_v in strictly_closed_by_tf.items()
    }

    def _is_pool_swept_cross_tf(lvl_px: float, after_open_ms: int, same_tf_bars: Sequence[Any], tf_key: str = "") -> bool:
        same_tr = tf_triples.get(tf_key)
        if same_tr is not None:
            for b_open, bh, bl in same_tr:
                if b_open <= after_open_ms:
                    continue
                if (norm_dir == "LONG" and bh >= lvl_px) or (norm_dir == "SHORT" and bl <= lvl_px):
                    return True
        else:
            for b in same_tf_bars:
                if _candle_open_ms(b) <= after_open_ms:
                    continue
                _, bh, bl, _ = _candle_ohlc(b)
                if (norm_dir == "LONG" and bh >= lvl_px) or (norm_dir == "SHORT" and bl <= lvl_px):
                    return True
        if enforce_cross_tf_sweep_check:
            for ltf_lbl in ("1M", "5M", "15M", "1H", "4H"):
                for b_open, bh, bl in tf_triples[ltf_lbl]:
                    if b_open <= after_open_ms:
                        continue
                    if (norm_dir == "LONG" and bh >= lvl_px) or (norm_dir == "SHORT" and bl <= lvl_px):
                        return True
        return False

    raw_candidates: List[Dict[str, Any]] = []
    seen_ids = set()

    def _try_add_pool(
        pool_id: str,
        price: Any,
        side: str,
        source_kind: str,
        timeframe: str,
        occurred_at_ms: int,
        known_at_ms: int,
        state: str,
    ) -> None:
        if not pool_id or pool_id in seen_ids:
            return
        if not _is_valid_finite_price(price):
            return
        px = round(float(price), 2)
        if int(known_at_ms) > cutoff_ms or int(occurred_at_ms) > cutoff_ms:
            return
        st_upper = str(state or "UNTOUCHED").strip().upper()
        if st_upper in terminal_states:
            return
        side_upper = str(side or "").strip().upper()
        if side_upper and side_upper not in (expected_side, "HIGH" if norm_dir == "LONG" else "LOW"):
            return
        if norm_dir == "LONG" and px <= ent + min_dist:
            return
        if norm_dir == "SHORT" and px >= ent - min_dist:
            return

        seen_ids.add(pool_id)
        raw_candidates.append(
            {
                "id": str(pool_id),
                "price": px,
                "side": expected_side,
                "source_kind": str(source_kind),
                "timeframe": str(timeframe),
                "occurred_at_ms": int(occurred_at_ms),
                "known_at_ms": int(known_at_ms),
                "state": st_upper,
                "distance": round(abs(px - ent), 4),
            }
        )

    # 1. Explicit liquidity_pools
    for p in liquidity_pools or ():
        if isinstance(p, Mapping):
            p_id = str(p.get("id", p.get("level_id", "")))
            p_px = p.get("price")
            p_side = str(p.get("side", expected_side))
            p_kind = str(p.get("source_kind", p.get("level_type", "LIQUIDITY_POOL")))
            p_tf = str(p.get("timeframe", p.get("originating_timeframe", "1M")))
            p_known = int(p.get("known_at_ms", p.get("occurred_at_ms", cutoff_ms)) or cutoff_ms)
            p_occ = int(p.get("occurred_at_ms", p_known) or p_known)
            p_state = str(p.get("state", "UNTOUCHED"))
        else:
            p_id = str(getattr(p, "id", getattr(p, "level_id", "")))
            p_px = getattr(p, "price", None)
            p_side = str(getattr(p, "side", expected_side))
            p_kind = str(getattr(p, "source_kind", getattr(p, "level_type", "LIQUIDITY_POOL")))
            p_tf = str(getattr(p, "timeframe", getattr(p, "originating_timeframe", "1M")))
            p_known = int(getattr(p, "known_at_ms", getattr(p, "occurred_at_ms", cutoff_ms)) or cutoff_ms)
            p_occ = int(getattr(p, "occurred_at_ms", p_known) or p_known)
            p_state = str(getattr(p, "state", "UNTOUCHED"))
        if not _is_valid_finite_price(p_px):
            continue
        px_f = float(p_px)
        if (norm_dir == "LONG" and px_f <= ent + min_dist) or (norm_dir == "SHORT" and px_f >= ent - min_dist):
            continue
        p_tf_up = p_tf.upper()
        if _is_pool_swept_cross_tf(
            px_f, p_occ, strictly_closed_by_tf.get(p_tf_up, ()), p_tf_up
        ):
            continue
        _try_add_pool(p_id, p_px, p_side, p_kind, p_tf, p_occ, p_known, p_state)

    # 2. Confirmed M1/M5/M15/H1/H4 unconsumed swings from closed bars
    tf_configs = (
        ("1M", strictly_closed_by_tf["1M"], 60_000, 2, 2),
        ("5M", strictly_closed_by_tf["5M"], 300_000, 1, 1),
        ("15M", strictly_closed_by_tf["15M"], 900_000, 1, 1),
        ("1H", strictly_closed_by_tf["1H"], 3_600_000, 1, 1),
        ("4H", strictly_closed_by_tf["4H"], 14_400_000, 1, 1),
    )
    tgt_sw_memo = getattr(resolve_causal_target_candidates, "_TGT_SW_MEMO", None)
    if tgt_sw_memo is None:
        tgt_sw_memo = {}
        setattr(resolve_causal_target_candidates, "_TGT_SW_MEMO", tgt_sw_memo)
    for tf_label, c_bars, tf_ms, lb, rb in tf_configs:
        if len(c_bars) < lb + rb + 1:
            continue
        sw_k = (tf_label, lb, rb, len(c_bars), _candle_close_ms(c_bars[-1], tf_ms), _candle_ohlc(c_bars[-1]), _candle_ohlc(c_bars[0]))
        sw_hit = tgt_sw_memo.get(sw_k)
        if sw_hit is not None:
            sh_list, sl_list = sw_hit
        else:
            sh_list, sl_list = identify_confirmed_swings(
                c_bars, timeframe=tf_label, left_bars=lb, right_bars=rb, as_of_ms=cutoff_ms
            )
            if len(tgt_sw_memo) >= 64:
                tgt_sw_memo.clear()
            tgt_sw_memo[sw_k] = (sh_list, sl_list)
        target_swings = sh_list if norm_dir == "LONG" else sl_list
        for sw in target_swings:
            sw_px = float(sw.get("price", 0.0))
            if (norm_dir == "LONG" and sw_px <= ent + min_dist) or (norm_dir == "SHORT" and sw_px >= ent - min_dist):
                continue
            sw_occ = int(sw.get("occurred_at_ms", sw.get("timestamp", 0)))
            sw_known = int(sw.get("known_at_ms", sw_occ + rb * tf_ms))
            if not _is_pool_swept_cross_tf(sw_px, sw_occ, c_bars, tf_label):
                _try_add_pool(
                    f"pool_{tf_label}_{sw.get('id', sw_occ)}",
                    sw_px,
                    expected_side,
                    f"{tf_label}_SWING_{'HIGH' if norm_dir == 'LONG' else 'LOW'}",
                    tf_label,
                    sw_occ,
                    sw_known,
                    "UNTOUCHED",
                )

    # 3. Completed prior-day PDH / PDL from closed_d1
    c_d1 = [b for b in (closed_d1 or ()) if _candle_is_closed(b, cutoff_ms, 86_400_000)]
    if c_d1:
        last_d1 = c_d1[-1]
        d1_open = _candle_open_ms(last_d1)
        d1_close = _candle_close_ms(last_d1, 86_400_000)
        _, d1_h, d1_l, _ = _candle_ohlc(last_d1)
        if d1_close <= cutoff_ms:
            lvl_px = d1_h if norm_dir == "LONG" else d1_l
            lvl_kind = "PDH" if norm_dir == "LONG" else "PDL"
            if not _is_pool_swept_cross_tf(lvl_px, d1_close - 1, strictly_closed_by_tf["1H"]):
                _try_add_pool(
                    f"pool_{lvl_kind}_{d1_close}",
                    lvl_px,
                    expected_side,
                    lvl_kind,
                    "1D",
                    d1_open,
                    d1_close,
                    "UNTOUCHED",
                )

    raw_candidates.sort(key=lambda c: (float(c["distance"]), str(c["id"])))
    return raw_candidates


def select_viable_structural_target(
    *,
    direction: str,
    entry_price: float,
    proposed_sl: Optional[float],
    decision_timestamp_ms: int,
    target_candidates: Sequence[Mapping[str, Any]],
    baseline_target_price: Optional[float] = None,
    baseline_target_id: Optional[str] = None,
    min_net_rr: float = 2.0,
    target_mode: str = "BASELINE_FROZEN",
    capital_usdt: float = 1000.0,
    risk_pct: float = 0.25,
    leverage: int = 20,
    margin_mode: str = "ISOLATED",
    costs: Optional[CostAssumptions] = None,
) -> StructuralTargetPlanV1792:
    """
    Section 4.7: Select an authoritative, causal liquidity target.
      - In `BASELINE_FROZEN` mode (`A`, `B`, `C`, `D1`, `D2`): uses `baseline_target_price` / `baseline_target_id`
        so stop-loss experiments are strictly isolated from target changes.
      - In `V1792_CAUSAL_STRUCTURAL_VIABLE` mode (`D3`, `D4`, `D6`): iterates sorted causal liquidity
        target candidates (nearest to farthest) and selects the nearest causal unconsumed pool that
        achieves `net_rr >= min_net_rr` under full round-trip fees and slippage.
      - NEVER invents `TP = entry +/- k * stop_distance`.
    """
    norm_dir = str(direction or "LONG").strip().upper()
    eff_costs = costs or CostAssumptions()
    mode_norm = str(target_mode or "BASELINE_FROZEN").strip().upper()
    cutoff_ms = int(decision_timestamp_ms)

    if mode_norm == "BASELINE_FROZEN":
        if not _is_valid_finite_price(baseline_target_price):
            return StructuralTargetPlanV1792(
                status="UNAVAILABLE",
                target_price=None,
                selected_target_id=None,
                source_kind=None,
                timeframe=None,
                occurred_at_ms=None,
                known_at_ms=None,
                reward_distance=None,
                gross_rr=0.0,
                net_rr=0.0,
                target_mode=mode_norm,
                candidates_evaluated=len(target_candidates),
                rejection_reasons=("NO_STRUCTURAL_TP_TARGET", "SHADOW_REJECTED_NO_VALID_TARGET"),
            )
        tp_px = round(float(baseline_target_price), 2)
        if (norm_dir == "LONG" and tp_px <= float(entry_price)) or (norm_dir == "SHORT" and tp_px >= float(entry_price)):
            return StructuralTargetPlanV1792(
                status="REJECTED",
                target_price=None,
                selected_target_id=baseline_target_id,
                source_kind="BASELINE_POOL",
                timeframe="1M",
                occurred_at_ms=cutoff_ms,
                known_at_ms=cutoff_ms,
                reward_distance=None,
                gross_rr=0.0,
                net_rr=0.0,
                target_mode=mode_norm,
                candidates_evaluated=len(target_candidates),
                rejection_reasons=("TARGET_WRONG_SIDE_OF_ENTRY",),
            )
        rew_dist = round(abs(tp_px - float(entry_price)), 4)
        g_rr = 0.0
        n_rr = 0.0
        rej: List[str] = []
        if _is_valid_finite_price(proposed_sl):
            calc = calculate_risk_reward(
                direction=norm_dir,
                planned_entry=float(entry_price),
                stop_loss=float(proposed_sl),
                take_profit=tp_px,
                capital_usdt=capital_usdt,
                risk_pct=risk_pct,
                leverage=leverage,
                margin_mode=margin_mode,
                min_net_rr=min_net_rr,
                costs=eff_costs,
                now_ms=cutoff_ms,
            )
            g_rr = round(float(calc.gross_rr or 0.0), 4)
            n_rr = round(float(calc.net_rr or 0.0), 4)
            if n_rr < float(min_net_rr):
                rej.append("NET_RR_TOO_LOW")
        return StructuralTargetPlanV1792(
            status="VALID",
            target_price=tp_px,
            selected_target_id=baseline_target_id or "baseline_target",
            source_kind="BASELINE_POOL",
            timeframe="1M",
            occurred_at_ms=cutoff_ms,
            known_at_ms=cutoff_ms,
            reward_distance=rew_dist,
            gross_rr=g_rr,
            net_rr=n_rr,
            target_mode=mode_norm,
            candidates_evaluated=len(target_candidates),
            rejection_reasons=tuple(rej),
        )

    # V1792_CAUSAL_STRUCTURAL_VIABLE mode
    if not target_candidates:
        return StructuralTargetPlanV1792(
            status="UNAVAILABLE",
            target_price=None,
            selected_target_id=None,
            source_kind=None,
            timeframe=None,
            occurred_at_ms=None,
            known_at_ms=None,
            reward_distance=None,
            gross_rr=0.0,
            net_rr=0.0,
            target_mode=mode_norm,
            candidates_evaluated=0,
            rejection_reasons=("NO_STRUCTURAL_TP_TARGET", "SHADOW_REJECTED_NO_VALID_TARGET"),
        )

    best_unviable_cand: Optional[Mapping[str, Any]] = None
    best_unviable_g_rr = -999.0
    best_unviable_n_rr = -999.0

    for cand in target_candidates:
        c_px = float(cand.get("price", 0.0))
        c_known = int(cand.get("known_at_ms", cutoff_ms))
        if c_known > cutoff_ms:
            continue
        if (norm_dir == "LONG" and c_px <= float(entry_price)) or (norm_dir == "SHORT" and c_px >= float(entry_price)):
            continue

        if _is_valid_finite_price(proposed_sl):
            calc = calculate_risk_reward(
                direction=norm_dir,
                planned_entry=float(entry_price),
                stop_loss=float(proposed_sl),
                take_profit=c_px,
                capital_usdt=capital_usdt,
                risk_pct=risk_pct,
                leverage=leverage,
                margin_mode=margin_mode,
                min_net_rr=min_net_rr,
                costs=eff_costs,
                now_ms=cutoff_ms,
            )
            g_rr = round(float(calc.gross_rr or 0.0), 4)
            n_rr = round(float(calc.net_rr or 0.0), 4)
            if calc.is_valid and calc.can_execute and n_rr >= float(min_net_rr):
                return StructuralTargetPlanV1792(
                    status="VALID",
                    target_price=round(c_px, 2),
                    selected_target_id=str(cand.get("id")),
                    source_kind=str(cand.get("source_kind", "LIQUIDITY_POOL")),
                    timeframe=str(cand.get("timeframe", "1M")),
                    occurred_at_ms=int(cand.get("occurred_at_ms", c_known)),
                    known_at_ms=c_known,
                    reward_distance=round(abs(c_px - float(entry_price)), 4),
                    gross_rr=g_rr,
                    net_rr=n_rr,
                    target_mode=mode_norm,
                    candidates_evaluated=len(target_candidates),
                    rejection_reasons=(),
                )
            if n_rr > best_unviable_n_rr:
                best_unviable_n_rr = n_rr
                best_unviable_g_rr = g_rr
                best_unviable_cand = cand
        else:
            return StructuralTargetPlanV1792(
                status="VALID",
                target_price=round(c_px, 2),
                selected_target_id=str(cand.get("id")),
                source_kind=str(cand.get("source_kind", "LIQUIDITY_POOL")),
                timeframe=str(cand.get("timeframe", "1M")),
                occurred_at_ms=int(cand.get("occurred_at_ms", c_known)),
                known_at_ms=c_known,
                reward_distance=round(abs(c_px - float(entry_price)), 4),
                gross_rr=0.0,
                net_rr=0.0,
                target_mode=mode_norm,
                candidates_evaluated=len(target_candidates),
                rejection_reasons=(),
            )

    if best_unviable_cand is not None:
        c_px = round(float(best_unviable_cand["price"]), 2)
        return StructuralTargetPlanV1792(
            status="VALID",
            target_price=c_px,
            selected_target_id=str(best_unviable_cand.get("id")),
            source_kind=str(best_unviable_cand.get("source_kind", "LIQUIDITY_POOL")),
            timeframe=str(best_unviable_cand.get("timeframe", "1M")),
            occurred_at_ms=int(best_unviable_cand.get("occurred_at_ms", cutoff_ms)),
            known_at_ms=int(best_unviable_cand.get("known_at_ms", cutoff_ms)),
            reward_distance=round(abs(c_px - float(entry_price)), 4),
            gross_rr=best_unviable_g_rr,
            net_rr=best_unviable_n_rr,
            target_mode=mode_norm,
            candidates_evaluated=len(target_candidates),
            rejection_reasons=("NET_RR_TOO_LOW", "SHADOW_REJECTED_INSUFFICIENT_NET_RR"),
        )

    return StructuralTargetPlanV1792(
        status="UNAVAILABLE",
        target_price=None,
        selected_target_id=None,
        source_kind=None,
        timeframe=None,
        occurred_at_ms=None,
        known_at_ms=None,
        reward_distance=None,
        gross_rr=0.0,
        net_rr=0.0,
        target_mode=mode_norm,
        candidates_evaluated=len(target_candidates),
        rejection_reasons=("NO_STRUCTURAL_TP_TARGET", "SHADOW_REJECTED_NO_VALID_TARGET"),
    )


def evaluate_stop_tp_risk_and_size(
    *,
    direction: str,
    entry_price: float,
    proposed_sl: Optional[float],
    target_price: Optional[float],
    capital_usdt: float = 1000.0,
    risk_pct: float = 0.25,
    leverage: int = 20,
    margin_mode: str = "ISOLATED",
    min_net_rr: float = 2.0,
    costs: Optional[CostAssumptions] = None,
    min_qty: float = 0.01,
    now_ms: int = 0,
) -> Dict[str, Any]:
    """
    Section 4.8: Evaluate authoritative cost-aware Net R:R and dynamic position sizing.
    Uses `domain_calculator.calculate_risk_reward` under fixed risk budget (`capital_usdt * risk_pct / 100`).
    If wider SL shrinks position size below `min_qty` (`0.01` XAU), rejects with `POSITION_SIZE_BELOW_MIN_LOT`.
    Never inflates risk budget or lowers `min_net_rr`.
    """
    eff_costs = costs or CostAssumptions()
    risk_budget_usdt = round(float(capital_usdt) * (float(risk_pct) / 100.0), 4)
    reasons: List[str] = []

    if not _is_valid_finite_price(entry_price) or not _is_valid_finite_price(proposed_sl):
        return {
            "is_executable": False,
            "hypothetical_quantity": 0.0,
            "gross_rr": 0.0,
            "net_rr": 0.0,
            "net_risk_usdt": 0.0,
            "net_reward_usdt": 0.0,
            "risk_budget_usdt": risk_budget_usdt,
            "rejection_reasons": ("INVALID_STOP_OR_ENTRY",),
        }

    if not _is_valid_finite_price(target_price):
        return {
            "is_executable": False,
            "hypothetical_quantity": 0.0,
            "gross_rr": 0.0,
            "net_rr": 0.0,
            "net_risk_usdt": 0.0,
            "net_reward_usdt": 0.0,
            "risk_budget_usdt": risk_budget_usdt,
            "rejection_reasons": ("SHADOW_REJECTED_NO_VALID_TARGET",),
        }

    ent = float(entry_price)
    sl = float(proposed_sl)
    tp = float(target_price)
    norm_dir = str(direction or "LONG").strip().upper()

    # Check minimum lot feasibility under risk budget before rounding
    stop_dist = abs(ent - sl)
    per_oz_fee_and_slip = (
        ent * float(eff_costs.taker_fee_rate)
        + sl * float(eff_costs.taker_fee_rate)
        + 2.0 * float(eff_costs.slippage_usd)
    )
    per_oz_total_risk = stop_dist + per_oz_fee_and_slip
    max_affordable_qty = risk_budget_usdt / max(1e-9, per_oz_total_risk)

    calc = calculate_risk_reward(
        direction=norm_dir,
        planned_entry=ent,
        stop_loss=sl,
        take_profit=tp,
        capital_usdt=capital_usdt,
        risk_pct=risk_pct,
        leverage=leverage,
        margin_mode=margin_mode,
        min_net_rr=min_net_rr,
        costs=eff_costs,
        now_ms=now_ms,
    )

    qty = round(float(calc.quantity or 0.0), 6)
    g_rr = round(float(calc.gross_rr or 0.0), 4)
    n_rr = round(float(calc.net_rr or 0.0), 4)
    net_risk = round(float(calc.net_risk_usdt or 0.0), 4)
    net_rew = round(float(calc.net_reward_usdt or 0.0), 4)

    if max_affordable_qty < float(min_qty) or qty < float(min_qty) or net_risk > risk_budget_usdt + 1e-5:
        reasons.append("POSITION_SIZE_BELOW_MIN_LOT")
        qty = 0.0

    if not calc.is_valid:
        reasons.append(str(calc.invalid_reason or "CALCULATOR_INVALID"))
    elif not calc.can_execute:
        reasons.append(str(calc.skip_reason or "CANNOT_EXECUTE"))

    if n_rr < float(min_net_rr) or not calc.meets_min_rr:
        reasons.append("NET_RR_TOO_LOW")
        reasons.append("SHADOW_REJECTED_INSUFFICIENT_NET_RR")
        qty = 0.0

    is_exec = len(reasons) == 0 and qty >= float(min_qty)
    return {
        "is_executable": is_exec,
        "hypothetical_quantity": qty if is_exec else 0.0,
        "gross_rr": g_rr,
        "net_rr": n_rr,
        "net_risk_usdt": net_risk if is_exec else 0.0,
        "net_reward_usdt": net_rew if is_exec else 0.0,
        "risk_budget_usdt": risk_budget_usdt,
        "rejection_reasons": tuple(dict.fromkeys(reasons)),
    }


def plan_v17_9_2_structural_stop_and_target(
    *,
    model_id: str,
    symbol: str = "XAUUSDT",
    direction: str,
    entry_price: float,
    decision_timestamp_ms: int,
    closed_m1: Sequence[Any] = (),
    closed_m5: Sequence[Any] = (),
    closed_m15: Sequence[Any] = (),
    closed_h1: Sequence[Any] = (),
    closed_h4: Sequence[Any] = (),
    closed_d1: Sequence[Any] = (),
    sh_1m: Sequence[Any] = (),
    sl_1m: Sequence[Any] = (),
    selected_fvg: Optional[Mapping[str, Any]] = None,
    selected_break: Optional[Mapping[str, Any]] = None,
    selected_sweep: Optional[Mapping[str, Any]] = None,
    selected_reference: Optional[Mapping[str, Any]] = None,
    unicorn_evidence: Optional[Any] = None,
    liquidity_pools: Sequence[Any] = (),
    baseline_anchor: Optional[StopAnchor] = None,
    baseline_sl: Optional[float] = None,
    baseline_buffer_price: float = 0.05,
    baseline_target_price: Optional[float] = None,
    baseline_target_id: Optional[str] = None,
    atr_m1: Optional[float] = None,
    atr_m5: Optional[float] = None,
    spread: Optional[float] = 0.20,
    tick_size: float = 0.01,
    capital_usdt: float = 1000.0,
    risk_pct: float = 0.25,
    leverage: int = 20,
    margin_mode: str = "ISOLATED",
    min_net_rr: float = 2.0,
    costs: Optional[CostAssumptions] = None,
    v1792_mode: str = "OBSERVE_ONLY",
    buffer_mode: str = "STRUCTURE_PLUS_NOISE",
    target_mode: str = "BASELINE_FROZEN",
    enforce_cross_tf_sweep_check: bool = True,
    enforce_cross_tf_invalidation_check: bool = True,
) -> StructuralStopPlanV1792:
    """
    Section 4.1–4.8 Orchestrator: End-to-end causal V17.9.2 structural stop & target planner.
    """
    norm_dir = str(direction or "LONG").strip().upper()
    cutoff_ms = int(decision_timestamp_ms)
    mode_norm = str(v1792_mode or "OBSERVE_ONLY").strip().upper()

    # 1. Collect & rank causal stop anchor candidates
    anc_cands_memo = getattr(plan_v17_9_2_structural_stop_and_target, "_ANC_CANDS_MEMO", None)
    if anc_cands_memo is None:
        anc_cands_memo = {}
        setattr(plan_v17_9_2_structural_stop_and_target, "_ANC_CANDS_MEMO", anc_cands_memo)
    ac_key = (
        model_id,
        norm_dir,
        round(float(entry_price), 4),
        cutoff_ms,
        len(closed_m1),
        _candle_close_ms(closed_m1[-1], 60_000) if closed_m1 else 0,
        _candle_ohlc(closed_m1[-1]) if closed_m1 else None,
        _candle_ohlc(closed_m1[0]) if closed_m1 else None,
        len(closed_m5),
        _candle_ohlc(closed_m5[-1]) if closed_m5 else None,
        len(closed_m15),
        _candle_ohlc(closed_m15[-1]) if closed_m15 else None,
        str(selected_fvg.get("id")) if isinstance(selected_fvg, Mapping) else None,
        str(selected_break.get("break_id")) if isinstance(selected_break, Mapping) else None,
        str(selected_sweep.get("id")) if isinstance(selected_sweep, Mapping) else None,
        str(getattr(baseline_anchor, "id", None)),
        getattr(baseline_anchor, "price", None),
        bool(getattr(unicorn_evidence, "is_valid", False)),
        tick_size,
        bool(enforce_cross_tf_invalidation_check),
    )
    ac_hit = anc_cands_memo.get(ac_key)
    if ac_hit is not None:
        selected_anc, ranked_cands, rank_reasons = ac_hit
    else:
        candidates = collect_causal_stop_anchor_candidates(
            model_id=model_id,
            direction=norm_dir,
            entry_price=entry_price,
            decision_timestamp_ms=cutoff_ms,
            closed_m1=closed_m1,
            closed_m5=closed_m5,
            closed_m15=closed_m15,
            sh_1m=sh_1m,
            sl_1m=sl_1m,
            selected_fvg=selected_fvg,
            selected_break=selected_break,
            selected_sweep=selected_sweep,
            selected_reference=selected_reference,
            unicorn_evidence=unicorn_evidence,
            baseline_anchor=baseline_anchor,
            tick_size=tick_size,
            enforce_cross_tf_invalidation_check=enforce_cross_tf_invalidation_check,
        )
        selected_anc, ranked_cands, rank_reasons = rank_structure_valid_anchors(
            candidates,
            model_id=model_id,
            direction=norm_dir,
            entry_price=entry_price,
            decision_timestamp_ms=cutoff_ms,
            tick_size=tick_size,
        )
        if len(anc_cands_memo) >= 32:
            anc_cands_memo.clear()
        anc_cands_memo[ac_key] = (selected_anc, ranked_cands, rank_reasons)

    # 2. Compute causal noise buffer & derive proposed SL
    if mode_norm == "TP_POLICY_ONLY":
        # TP_POLICY_ONLY isolates the target policy while keeping baseline SL
        buf_px = float(baseline_buffer_price or 0.05)
        buf_comps = {"baseline_buffer": buf_px}
        dom_comp = "baseline_buffer"
        inv_px = float(baseline_anchor.price) if baseline_anchor is not None else baseline_sl
        prop_sl = float(baseline_sl) if _is_valid_finite_price(baseline_sl) else None
        froz_id = str(baseline_anchor.id) if baseline_anchor is not None else None
        contract_ok = prop_sl is not None
        contract_reasons: Tuple[str, ...] = () if contract_ok else ("NO_CAUSAL_STRUCTURAL_ANCHOR",)
    else:
        buf_px, buf_comps, dom_comp = compute_causal_noise_buffer(
            atr_m1=atr_m1,
            atr_m5=atr_m5,
            spread=spread,
            tick_size=tick_size,
            buffer_mode=buffer_mode,
            anchor_timeframe=selected_anc.timeframe if selected_anc else "1M",
        )
        inv_px = selected_anc.anchor_price if selected_anc is not None else None
        prop_sl = (
            derive_structure_valid_stop(
                direction=norm_dir,
                invalidation_price=inv_px,
                noise_buffer_price=buf_px,
                tick_size=tick_size,
                entry_price=entry_price,
            )
            if inv_px is not None
            else None
        )
        froz_id = selected_anc.anchor_id if selected_anc is not None else None
        contract_ok, contract_reasons = validate_structure_invalidation_contract(
            direction=norm_dir,
            entry_price=entry_price,
            proposed_sl=prop_sl,
            anchor=selected_anc,
            noise_buffer_price=buf_px,
            decision_timestamp_ms=cutoff_ms,
            tick_size=tick_size,
        )

    # 3. Resolve causal target candidates & select target
    eff_target_mode = (
        "V1792_CAUSAL_STRUCTURAL_VIABLE"
        if mode_norm in ("TP_POLICY_ONLY", "STRUCTURE_PLUS_TP")
        else str(target_mode or "BASELINE_FROZEN")
    )
    target_cands_memo = getattr(plan_v17_9_2_structural_stop_and_target, "_TARGET_CANDS_MEMO", None)
    if target_cands_memo is None:
        target_cands_memo = {}
        setattr(plan_v17_9_2_structural_stop_and_target, "_TARGET_CANDS_MEMO", target_cands_memo)
    tc_key = (
        norm_dir,
        round(float(entry_price), 4),
        cutoff_ms,
        len(liquidity_pools),
        tuple((getattr(p, "id", None), getattr(p, "price", None), getattr(p, "swept", None)) for p in liquidity_pools[:8]),
        len(closed_m1),
        _candle_close_ms(closed_m1[-1], 60_000) if closed_m1 else 0,
        _candle_ohlc(closed_m1[-1]) if closed_m1 else None,
        _candle_ohlc(closed_m1[0]) if closed_m1 else None,
        len(closed_m5),
        _candle_ohlc(closed_m5[-1]) if closed_m5 else None,
        len(closed_m15),
        _candle_ohlc(closed_m15[-1]) if closed_m15 else None,
        len(closed_h1),
        len(closed_h4),
        len(closed_d1),
        atr_m1,
        tick_size,
        bool(enforce_cross_tf_sweep_check),
    )
    target_cands = target_cands_memo.get(tc_key)
    if target_cands is None:
        target_cands = resolve_causal_target_candidates(
            direction=norm_dir,
            entry_price=entry_price,
            decision_timestamp_ms=cutoff_ms,
            liquidity_pools=liquidity_pools,
            closed_m1=closed_m1,
            closed_m5=closed_m5,
            closed_m15=closed_m15,
            closed_h1=closed_h1,
            closed_h4=closed_h4,
            closed_d1=closed_d1,
            atr_m1=atr_m1,
            tick_size=tick_size,
            enforce_cross_tf_sweep_check=enforce_cross_tf_sweep_check,
        )
        if len(target_cands_memo) >= 32:
            target_cands_memo.clear()
        target_cands_memo[tc_key] = target_cands
    t_plan = select_viable_structural_target(
        direction=norm_dir,
        entry_price=entry_price,
        proposed_sl=prop_sl,
        decision_timestamp_ms=cutoff_ms,
        target_candidates=target_cands,
        baseline_target_price=baseline_target_price,
        baseline_target_id=baseline_target_id,
        min_net_rr=min_net_rr,
        target_mode=eff_target_mode,
        capital_usdt=capital_usdt,
        risk_pct=risk_pct,
        leverage=leverage,
        margin_mode=margin_mode,
        costs=costs,
    )

    # 4. Evaluate cost-aware Net R:R and position size
    risk_eval = evaluate_stop_tp_risk_and_size(
        direction=norm_dir,
        entry_price=entry_price,
        proposed_sl=prop_sl,
        target_price=t_plan.target_price,
        capital_usdt=capital_usdt,
        risk_pct=risk_pct,
        leverage=leverage,
        margin_mode=margin_mode,
        min_net_rr=min_net_rr,
        costs=costs,
        now_ms=cutoff_ms,
    )

    all_rej: List[str] = []
    if not contract_ok:
        all_rej.extend(rank_reasons)
        all_rej.extend(contract_reasons)
    all_rej.extend(t_plan.rejection_reasons)
    all_rej.extend(risk_eval["rejection_reasons"])
    dedup_rej = tuple(dict.fromkeys(all_rej))

    if not contract_ok or prop_sl is None:
        status = "INVALID_STOP"
    elif len(dedup_rej) == 0 and risk_eval["is_executable"]:
        status = "ELIGIBLE"
    else:
        status = "VALID_SETUP_REJECTED_BY_RISK"

    stop_dist = round(abs(float(entry_price) - float(prop_sl)), 4) if prop_sl is not None else None
    obs_fp = _canonical_json_hash(
        {
            "alg": V17_9_2_ALGORITHM_VERSION,
            "mode": mode_norm,
            "model_id": model_id,
            "dir": norm_dir,
            "ts": cutoff_ms,
            "anc": froz_id,
            "sl": prop_sl,
            "tp": t_plan.target_price,
            "status": status,
        }
    )

    return StructuralStopPlanV1792(
        status=status,
        v1792_mode=mode_norm,
        model_id=model_id,
        symbol=symbol,
        direction=norm_dir,
        entry_price=round(float(entry_price), 2),
        decision_timestamp_ms=cutoff_ms,
        selected_anchor=selected_anc,
        frozen_anchor_id=froz_id,
        invalidation_price=round(float(inv_px), 2) if inv_px is not None else None,
        noise_buffer_price=round(float(buf_px), 4),
        buffer_components=buf_comps,
        dominant_buffer_component=dom_comp,
        proposed_sl=round(float(prop_sl), 2) if prop_sl is not None else None,
        stop_distance=stop_dist,
        baseline_sl=round(float(baseline_sl), 2) if _is_valid_finite_price(baseline_sl) else None,
        baseline_anchor_id=str(baseline_anchor.id) if baseline_anchor is not None else None,
        target_plan=t_plan,
        hypothetical_quantity=float(risk_eval["hypothetical_quantity"]),
        gross_rr=float(risk_eval["gross_rr"]),
        net_rr=float(risk_eval["net_rr"]),
        net_risk_usdt=float(risk_eval["net_risk_usdt"]),
        net_reward_usdt=float(risk_eval["net_reward_usdt"]),
        rejection_reasons=dedup_rej,
        candidates=tuple(ranked_cands),
        observe_metadata_fingerprint=obs_fp,
    )


def _wilson_interval_95(successes: int, total: int) -> Optional[Dict[str, float]]:
    if total <= 0:
        return None
    z = 1.959963984540054
    p = successes / total
    denom = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * total)) / total)) / denom
    return {
        "lower_95": round(max(0.0, center - half), 4),
        "upper_95": round(min(1.0, center + half), 4),
    }


def classify_structure_stop_outcome(
    *,
    episode_id: str,
    model_id: str,
    direction: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    invalidation_price: float,
    protected_structure_price: float,
    decision_timestamp_ms: int,
    forward_bars: Sequence[Any],
    provenance: str = "HISTORICAL_BACKTEST_V1792",
    delayed_break_window_bars: int = 5,
    classify_gap_unresolved: bool = False,
    gap_threshold_ms: int = 300_000,
) -> StopOutcomeDiagnostic:
    """
    Section 4.9 & V17.9.2.1 Section 4.4: Outcome-only post-entry forensic classifier.
    Classifies a trade path into mutually exclusive labels:
      - `CLEAN_WIN`: TP hit before SL hit
      - `SAME_BAR_CONSERVATIVE_LOSS`: Both SL and TP touched on the same bar (conservative loss)
      - `PREMATURE_NOISE_SWEEP_STOP`: SL hit before outer protected structure close-through,
        distinguishing `PURE_NOISE_SWEEP_STRUCTURE_HELD`, `NOISE_SWEEP_THEN_TP_HIT_EX_POST`,
        and `PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK` (when structure broke > `delayed_break_window_bars` later)
      - `DELAYED_INEVITABLE_LOSS`: SL hit before structure close-through, then outer protected structure
        broke within `delayed_break_window_bars` without ever reaching TP
      - `TRUE_INVALIDATION_LOSS`: Candle closed decisively beyond `protected_structure_price` on or before
        the stop bar and continued against trade direction
      - `OPEN_AT_HORIZON`: Neither SL nor TP hit within `forward_bars`
      - `DATA_GAP_UNRESOLVED`: Intra-trade M1 gap > `gap_threshold_ms` occurred before exit when
        `classify_gap_unresolved=True`
    """
    norm_dir = str(direction or "LONG").strip().upper()
    ent = float(entry_price)
    sl = float(sl_price)
    tp = float(tp_price)
    inv = float(invalidation_price)
    prot = float(protected_structure_price)
    risk_dist = max(0.01, abs(ent - sl))

    stop_hit = False
    stop_hit_ts: Optional[int] = None
    stop_hit_idx: Optional[int] = None
    tp_hit = False
    tp_hit_ts: Optional[int] = None
    tp_hit_idx: Optional[int] = None
    same_bar_tie = False

    structure_broken = False
    structure_break_ts: Optional[int] = None
    structure_break_idx: Optional[int] = None
    tp_would_hit_after_stop = False

    mfe_usd = 0.0
    mae_usd = 0.0
    mfe_before_stop_usd = 0.0
    mfe_after_stop_usd = 0.0
    bars_held = 0
    has_data_gap_before_exit = False
    max_bar_gap_ms = 0
    prev_close_ms = int(decision_timestamp_ms)

    for idx, b in enumerate(forward_bars):
        b_ts = _candle_open_ms(b)
        b_close_ms = _candle_close_ms(b, 60_000)
        _, bh, bl, bc = _candle_ohlc(b)

        gap_ms = max(0, b_ts - prev_close_ms) if prev_close_ms > 0 and b_ts > 0 else 0
        prev_close_ms = b_close_ms

        if norm_dir == "LONG":
            fav = max(0.0, bh - ent)
            adv = max(0.0, ent - bl)
            bar_hits_sl = bl <= sl
            bar_hits_tp = bh >= tp
            bar_breaks_struct = bc < prot
        else:
            fav = max(0.0, ent - bl)
            adv = max(0.0, bh - ent)
            bar_hits_sl = bh >= sl
            bar_hits_tp = bl <= tp
            bar_breaks_struct = bc > prot

        if not stop_hit and not tp_hit:
            if gap_ms > max_bar_gap_ms:
                max_bar_gap_ms = gap_ms
            if gap_ms > int(gap_threshold_ms):
                has_data_gap_before_exit = True

            mfe_usd = max(mfe_usd, fav)
            mfe_before_stop_usd = max(mfe_before_stop_usd, fav)
            mae_usd = max(mae_usd, adv)
            bars_held = idx + 1

            if bar_hits_sl and bar_hits_tp:
                stop_hit = True
                stop_hit_ts = b_ts
                stop_hit_idx = idx
                same_bar_tie = True
            elif bar_hits_sl:
                stop_hit = True
                stop_hit_ts = b_ts
                stop_hit_idx = idx
            elif bar_hits_tp:
                tp_hit = True
                tp_hit_ts = b_ts
                tp_hit_idx = idx
        else:
            # Post-stop tracking for forensic attribution only (NON_EXECUTABLE_EX_POST)
            if stop_hit:
                mfe_after_stop_usd = max(mfe_after_stop_usd, fav)
                if bar_hits_tp:
                    tp_would_hit_after_stop = True

        if not structure_broken and bar_breaks_struct:
            structure_broken = True
            structure_break_ts = b_ts
            structure_break_idx = idx

    sub_class = "NONE"
    if classify_gap_unresolved and has_data_gap_before_exit:
        outcome_class = "DATA_GAP_UNRESOLVED"
        sub_class = "INTRATRADE_PROVIDER_GAP_BEFORE_EXIT"
    elif tp_hit and not stop_hit:
        outcome_class = "CLEAN_WIN"
        sub_class = "CLEAN_TARGET_HIT"
    elif same_bar_tie:
        outcome_class = "SAME_BAR_CONSERVATIVE_LOSS"
        sub_class = "SAME_BAR_SL_TP_TIE_CONSERVATIVE"
    elif stop_hit:
        assert stop_hit_idx is not None
        broke_on_or_before_stop = bool(
            structure_broken and structure_break_idx is not None and structure_break_idx <= stop_hit_idx
        )
        if broke_on_or_before_stop:
            outcome_class = "TRUE_INVALIDATION_LOSS"
            sub_class = "IMMEDIATE_STRUCTURE_INVALIDATION"
        elif tp_would_hit_after_stop:
            outcome_class = "PREMATURE_NOISE_SWEEP_STOP"
            sub_class = "NOISE_SWEEP_THEN_TP_HIT_EX_POST"
        elif not structure_broken:
            outcome_class = "PREMATURE_NOISE_SWEEP_STOP"
            sub_class = "PURE_NOISE_SWEEP_STRUCTURE_HELD"
        else:
            assert structure_break_idx is not None
            bars_to_break = int(structure_break_idx - stop_hit_idx)
            if bars_to_break <= int(delayed_break_window_bars):
                outcome_class = "DELAYED_INEVITABLE_LOSS"
                sub_class = "DELAYED_INEVITABLE_BREAK_WITHIN_5_BARS"
            else:
                outcome_class = "PREMATURE_NOISE_SWEEP_STOP"
                sub_class = "PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK"
    else:
        outcome_class = "OPEN_AT_HORIZON"
        sub_class = "CENSORED_OPEN_AT_HORIZON"

    return StopOutcomeDiagnostic(
        episode_id=str(episode_id),
        model_id=str(model_id),
        direction=norm_dir,
        entry_price=ent,
        sl_price=sl,
        tp_price=tp,
        invalidation_price=inv,
        protected_structure_price=prot,
        decision_timestamp_ms=int(decision_timestamp_ms),
        outcome_class=outcome_class,
        stop_hit=stop_hit,
        stop_hit_bar_ts=stop_hit_ts,
        tp_hit=tp_hit,
        tp_hit_bar_ts=tp_hit_ts,
        structure_broken=structure_broken,
        structure_break_bar_ts=structure_break_ts,
        tp_would_hit_after_stop=tp_would_hit_after_stop,
        mfe_usd=round(mfe_usd, 4),
        mae_usd=round(mae_usd, 4),
        mfe_r=round(mfe_usd / risk_dist, 4),
        mae_r=round(mae_usd / risk_dist, 4),
        bars_held=bars_held,
        provenance=provenance,
        sub_class=sub_class,
        mfe_before_stop_usd=round(mfe_before_stop_usd, 4),
        mfe_after_stop_usd=round(mfe_after_stop_usd, 4),
        has_data_gap_before_exit=has_data_gap_before_exit,
        max_bar_gap_ms=max_bar_gap_ms,
    )


def measure_premature_stop_rate(
    diagnostics: Sequence[StopOutcomeDiagnostic],
) -> Dict[str, Any]:
    """
    Section 4.9 & V17.9.2.1 Section 3.A: Aggregate premature stop vs true invalidation rates
    across a sequence of `StopOutcomeDiagnostic` records.
    Returns None (`null`) when denominator == 0 and supplies Wilson 95% confidence intervals.
    """
    total = len(diagnostics)
    counts: Dict[str, int] = {
        "CLEAN_WIN": 0,
        "PREMATURE_NOISE_SWEEP_STOP": 0,
        "DELAYED_INEVITABLE_LOSS": 0,
        "TRUE_INVALIDATION_LOSS": 0,
        "SAME_BAR_CONSERVATIVE_LOSS": 0,
        "OPEN_AT_HORIZON": 0,
        "DATA_GAP_UNRESOLVED": 0,
    }
    sub_counts: Dict[str, int] = {
        "PURE_NOISE_SWEEP_STRUCTURE_HELD": 0,
        "NOISE_SWEEP_THEN_TP_HIT_EX_POST": 0,
        "PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK": 0,
        "DELAYED_INEVITABLE_BREAK_WITHIN_5_BARS": 0,
        "IMMEDIATE_STRUCTURE_INVALIDATION": 0,
        "CLEAN_TARGET_HIT": 0,
        "SAME_BAR_SL_TP_TIE_CONSERVATIVE": 0,
        "CENSORED_OPEN_AT_HORIZON": 0,
        "INTRATRADE_PROVIDER_GAP_BEFORE_EXIT": 0,
    }
    stopped = 0
    tp_after_stop = 0
    gap_before_exit_count = 0
    mfe_r_list: List[float] = []
    mae_r_list: List[float] = []
    bars_list: List[int] = []

    for d in diagnostics:
        oc = d.outcome_class
        counts[oc] = counts.get(oc, 0) + 1
        sc = getattr(d, "sub_class", "NONE")
        if sc in sub_counts:
            sub_counts[sc] += 1
        if d.stop_hit:
            stopped += 1
        if d.tp_would_hit_after_stop:
            tp_after_stop += 1
        if getattr(d, "has_data_gap_before_exit", False):
            gap_before_exit_count += 1
        mfe_r_list.append(float(d.mfe_r))
        mae_r_list.append(float(d.mae_r))
        bars_list.append(int(d.bars_held))

    premature_cnt = counts.get("PREMATURE_NOISE_SWEEP_STOP", 0)
    delayed_cnt = counts.get("DELAYED_INEVITABLE_LOSS", 0)
    true_inv_cnt = counts.get("TRUE_INVALIDATION_LOSS", 0)

    return {
        "total_episodes": total,
        "stopped_episodes": stopped,
        "clean_win_count": counts["CLEAN_WIN"],
        "premature_stop_count": premature_cnt,
        "pure_noise_sweep_structure_held_count": sub_counts["PURE_NOISE_SWEEP_STRUCTURE_HELD"],
        "premature_wick_before_later_break_count": sub_counts["PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK"],
        "noise_sweep_then_tp_hit_ex_post_count": sub_counts["NOISE_SWEEP_THEN_TP_HIT_EX_POST"],
        "delayed_inevitable_loss_count": delayed_cnt,
        "true_invalidation_loss_count": true_inv_cnt,
        "same_bar_tie_loss_count": counts["SAME_BAR_CONSERVATIVE_LOSS"],
        "open_at_horizon_count": counts["OPEN_AT_HORIZON"],
        "data_gap_unresolved_count": counts["DATA_GAP_UNRESOLVED"],
        "has_data_gap_before_exit_count": gap_before_exit_count,
        "tp_would_hit_after_stop_count": tp_after_stop,
        "premature_stop_rate_of_stopped": round(premature_cnt / stopped, 4) if stopped > 0 else None,
        "premature_stop_rate_of_stopped_wilson_95_ci": _wilson_interval_95(premature_cnt, stopped),
        "premature_stop_rate_of_total": round(premature_cnt / total, 4) if total > 0 else None,
        "premature_stop_rate_of_total_wilson_95_ci": _wilson_interval_95(premature_cnt, total),
        "delayed_inevitable_rate_of_stopped": round(delayed_cnt / stopped, 4) if stopped > 0 else None,
        "true_invalidation_rate_of_stopped": round(true_inv_cnt / stopped, 4) if stopped > 0 else None,
        "true_invalidation_rate_of_stopped_wilson_95_ci": _wilson_interval_95(true_inv_cnt, stopped),
        "mean_mfe_r": round(sum(mfe_r_list) / len(mfe_r_list), 4) if mfe_r_list else None,
        "mean_mae_r": round(sum(mae_r_list) / len(mae_r_list), 4) if mae_r_list else None,
        "mean_bars_held": round(sum(bars_list) / len(bars_list), 2) if bars_list else None,
        "outcome_class_counts": counts,
        "sub_class_counts": sub_counts,
    }


def simulate_order_fill_and_outcome_sensitivity(
    *,
    direction: str,
    planned_entry: float,
    sl_price: float,
    tp_price: float,
    quantity: float,
    decision_timestamp_ms: int,
    forward_bars: Sequence[Any],
    fill_mode: str = "NEXT_BAR_OPEN_BID_ASK",
    spread_usd: float = 0.20,
    slippage_usd: float = 0.10,
    taker_fee_rate: float = 0.0004,
    maker_fee_rate: float = 0.0002,
    tp_is_maker: bool = False,
    gap_threshold_ms: int = 300_000,
    risk_budget_usd: float = 2.50,
) -> Dict[str, Any]:
    """
    V17.9.2.1 Section 5.3 & 12(D): Realistic forward trade fill & outcome simulator comparing:
      - `LEGACY_CLOSE_FILL`: enters at `planned_entry` (bar close), checks raw M1 high/low against SL/TP.
      - `NEXT_BAR_OPEN_BID_ASK`: enters at `forward_bars[0].open` with bid/ask half-spread + adverse slippage;
        checks LONG exit at bid (`price - half_spread`) and SHORT exit at ask (`price + half_spread`);
        handles gap-spanning entry (`NO_FILL_ENTRY_GAP`), gap-open adverse stops (`ADVERSE_GAP_OPEN_STOP_LOSS`),
        intratrade provider gaps (`DATA_GAP_UNRESOLVED`), and intrabar SL+TP ties (`LOSS_TIE_CONSERVATIVE`).
      - `STRESS_WIDER_SPREAD_SLIPPAGE_2X`: same as `NEXT_BAR_OPEN_BID_ASK` with 2x spread and 2x slippage.
    Never uses future bars to alter pre-entry decisions.
    """
    norm_dir = str(direction or "LONG").strip().upper()
    mode_norm = str(fill_mode or "NEXT_BAR_OPEN_BID_ASK").strip().upper()
    eff_spread = float(spread_usd) * (2.0 if mode_norm == "STRESS_WIDER_SPREAD_SLIPPAGE_2X" else 1.0)
    eff_slip = float(slippage_usd) * (2.0 if mode_norm == "STRESS_WIDER_SPREAD_SLIPPAGE_2X" else 1.0)
    half_spread = eff_spread / 2.0
    qty = max(0.0, float(quantity))
    sl = float(sl_price)
    tp = float(tp_price)

    if not forward_bars:
        return {
            "fill_mode": mode_norm,
            "filled": False,
            "outcome": "NO_FORWARD_BARS",
            "fill_price": None,
            "exit_price": None,
            "bars_held": 0,
            "realized_net_pnl_usd": 0.0,
            "realized_r": 0.0,
            "entry_gap_ms": None,
            "max_intratrade_gap_ms": 0,
        }

    first_bar = forward_bars[0]
    first_open_ms = _candle_open_ms(first_bar)
    first_open, _, _, _ = _candle_ohlc(first_bar)
    entry_gap_ms = max(0, first_open_ms - int(decision_timestamp_ms))

    if mode_norm == "LEGACY_CLOSE_FILL":
        fill_px = float(planned_entry)
    else:
        # Next tradable bar entry check: if next bar is delayed by > 60s (provider gap at entry), fail closed
        if entry_gap_ms > 60_000:
            return {
                "fill_mode": mode_norm,
                "filled": False,
                "outcome": "NO_FILL_ENTRY_GAP",
                "fill_price": None,
                "exit_price": None,
                "bars_held": 0,
                "realized_net_pnl_usd": 0.0,
                "realized_r": 0.0,
                "entry_gap_ms": entry_gap_ms,
                "max_intratrade_gap_ms": entry_gap_ms,
            }
        if norm_dir == "LONG":
            fill_px = round(first_open + half_spread + eff_slip, 4)
            if fill_px <= sl or fill_px >= tp:
                return {
                    "fill_mode": mode_norm,
                    "filled": False,
                    "outcome": "NO_FILL_INVALID_NEXT_OPEN_GEOMETRY",
                    "fill_price": fill_px,
                    "exit_price": None,
                    "bars_held": 0,
                    "realized_net_pnl_usd": 0.0,
                    "realized_r": 0.0,
                    "entry_gap_ms": entry_gap_ms,
                    "max_intratrade_gap_ms": 0,
                }
        else:
            fill_px = round(first_open - half_spread - eff_slip, 4)
            if fill_px >= sl or fill_px <= tp:
                return {
                    "fill_mode": mode_norm,
                    "filled": False,
                    "outcome": "NO_FILL_INVALID_NEXT_OPEN_GEOMETRY",
                    "fill_price": fill_px,
                    "exit_price": None,
                    "bars_held": 0,
                    "realized_net_pnl_usd": 0.0,
                    "realized_r": 0.0,
                    "entry_gap_ms": entry_gap_ms,
                    "max_intratrade_gap_ms": 0,
                }

    prev_close_ms = int(decision_timestamp_ms)
    max_intratrade_gap_ms = 0
    outcome = "OPEN_AT_HORIZON"
    exit_px: Optional[float] = None
    bars_held = 0

    for idx, b in enumerate(forward_bars):
        b_open_ms = _candle_open_ms(b)
        b_close_ms = _candle_close_ms(b, 60_000)
        bo, bh, bl, bc = _candle_ohlc(b)
        gap_ms = max(0, b_open_ms - prev_close_ms) if prev_close_ms > 0 and b_open_ms > 0 else 0
        prev_close_ms = b_close_ms
        if gap_ms > max_intratrade_gap_ms:
            max_intratrade_gap_ms = gap_ms
        bars_held = idx + 1

        if mode_norm != "LEGACY_CLOSE_FILL" and idx > 0 and gap_ms > int(gap_threshold_ms):
            # Check if gap open jumped adversely across SL
            if norm_dir == "LONG" and (bo - half_spread) <= sl:
                outcome = "ADVERSE_GAP_OPEN_STOP_LOSS"
                exit_px = round(bo - half_spread - eff_slip, 4)
                break
            if norm_dir == "SHORT" and (bo + half_spread) >= sl:
                outcome = "ADVERSE_GAP_OPEN_STOP_LOSS"
                exit_px = round(bo + half_spread + eff_slip, 4)
                break
            outcome = "DATA_GAP_UNRESOLVED"
            exit_px = round(bo - half_spread if norm_dir == "LONG" else bo + half_spread, 4)
            break

        if mode_norm == "LEGACY_CLOSE_FILL":
            hit_sl = (bl <= sl) if norm_dir == "LONG" else (bh >= sl)
            hit_tp = (bh >= tp) if norm_dir == "LONG" else (bl <= tp)
            if hit_sl and hit_tp:
                outcome = "LOSS_TIE_CONSERVATIVE"
                exit_px = sl
                break
            if hit_sl:
                outcome = "LOSS"
                exit_px = sl
                break
            if hit_tp:
                outcome = "WIN"
                exit_px = tp
                break
        else:
            # Quote-side aware exit: LONG exits at bid (price - half_spread), SHORT exits at ask (price + half_spread)
            if norm_dir == "LONG":
                hit_sl = (bl - half_spread) <= sl
                hit_tp = (bh - half_spread) >= tp
                if hit_sl and hit_tp:
                    outcome = "LOSS_TIE_CONSERVATIVE"
                    exit_px = round(sl - eff_slip, 4)
                    break
                if hit_sl:
                    outcome = "LOSS"
                    exit_px = round(min(sl, bo - half_spread) - eff_slip, 4)
                    break
                if hit_tp:
                    outcome = "WIN"
                    exit_px = round(tp, 4)
                    break
            else:
                hit_sl = (bh + half_spread) >= sl
                hit_tp = (bl + half_spread) <= tp
                if hit_sl and hit_tp:
                    outcome = "LOSS_TIE_CONSERVATIVE"
                    exit_px = round(sl + eff_slip, 4)
                    break
                if hit_sl:
                    outcome = "LOSS"
                    exit_px = round(max(sl, bo + half_spread) + eff_slip, 4)
                    break
                if hit_tp:
                    outcome = "WIN"
                    exit_px = round(tp, 4)
                    break

    if exit_px is None:
        _, _, _, last_c = _candle_ohlc(forward_bars[-1])
        exit_px = round(
            last_c - half_spread if (mode_norm != "LEGACY_CLOSE_FILL" and norm_dir == "LONG")
            else (last_c + half_spread if mode_norm != "LEGACY_CLOSE_FILL" else last_c),
            4,
        )

    gross_pnl_per_oz = (exit_px - fill_px) if norm_dir == "LONG" else (fill_px - exit_px)
    entry_fee_per_oz = fill_px * float(taker_fee_rate)
    exit_fee_rate = float(maker_fee_rate) if (outcome == "WIN" and tp_is_maker) else float(taker_fee_rate)
    exit_fee_per_oz = exit_px * exit_fee_rate
    net_pnl_usd = round((gross_pnl_per_oz - entry_fee_per_oz - exit_fee_per_oz) * qty, 4)
    realized_r = round(net_pnl_usd / max(0.01, float(risk_budget_usd)), 4)

    return {
        "fill_mode": mode_norm,
        "filled": True,
        "outcome": outcome,
        "fill_price": round(fill_px, 4),
        "exit_price": round(exit_px, 4),
        "bars_held": bars_held,
        "realized_net_pnl_usd": net_pnl_usd,
        "realized_r": realized_r,
        "entry_gap_ms": entry_gap_ms,
        "max_intratrade_gap_ms": max_intratrade_gap_ms,
    }


# ==============================================================================
# V17.9.3.1 — EXECUTABLE TRADE LEDGER, NET EXECUTION SIMULATOR & FORENSICS
# ==============================================================================


@dataclass(frozen=True)
class ExecutableTradeLedgerEntryV17931:
    """
    V17.9.3.1 Section 03.A: Immutable per-trade executable ledger entry reconciling
    exact floored position sizing (`quantity_step = 0.01`), entry/exit fill prices,
    taker/maker fees, slippage (never double-counted), gross vs net realized PnL,
    planned net risk (`planned_net_risk_usd <= 2.50`), and `realized_r_net`.
    """

    variant: str
    episode_key: str
    setup_id: str
    model_id: str
    direction: str
    decision_ts: int
    source_bar_close_ms: int
    dataset_sha: str
    entry_order_type: str
    fill_mode: str
    entry_order_timestamp: int
    entry_fill_ts: Optional[int]
    filled: bool
    entry_reference: float
    entry_fill_price: Optional[float]
    entry_fee: float
    entry_slippage: float
    gross_notional: float
    quantity: float
    quantity_step: float
    multiplier: float
    tick_size: float
    planned_sl: float
    planned_tp: float
    trigger_bar_ts: Optional[int]
    sl_trigger_price: float
    tp_trigger_price: float
    exit_fill_ts: Optional[int]
    exit_fill_price: Optional[float]
    exit_type: str
    exit_fee: float
    exit_slippage: float
    realized_gross_pnl_usd: float
    realized_net_pnl_usd: float
    modeled_total_cost_usd: float
    planned_net_risk_usd: float
    realized_r_net: float
    realized_r_gross: float
    max_adverse_gap: int
    intratrade_gap_count: int
    no_fill_reason: Optional[str]
    unresolved_reason: Optional[str]
    open_at_horizon_mtm_separate: Optional[float]
    funding_status: str = "EXCLUDED_UNMODELED"
    historical_quote_quality: str = "MODELED_FROM_M1_CLOSE_20C_SPREAD"
    cost_provenance: str = "BITGET_XAUUSDT_TAKER_4BPS_SLIPPAGE_10C"
    instrument_metadata_hash: str = ""
    executable_price_provenance: str = "MODELED_OHLC_BID_ASK"
    bars_held: int = 0
    entry_gap_ms: int = 0
    same_bar_sl_tp_tie: bool = False
    execution_truth_class: str = "MODELED_HISTORICAL_BAR_EXECUTION"

    @property
    def outcome_label(self) -> str:
        return self.exit_type

    @property
    def entry_fill_mode(self) -> str:
        return self.fill_mode

    @property
    def entry_fee_usd(self) -> float:
        return round(float(self.entry_fee), 4)

    @property
    def exit_fee_usd(self) -> float:
        return round(float(self.exit_fee), 4)

    @property
    def entry_filled(self) -> bool:
        return bool(self.filled)

    @property
    def total_friction_usd(self) -> float:
        return round(float(self.entry_fee) + float(self.exit_fee), 4)

    def verify_accounting_invariants(self) -> Tuple[bool, Optional[str]]:
        if self.executable_price_provenance != "MODELED_OHLC_BID_ASK":
            return False, f"INVALID_PRICE_PROVENANCE:{self.executable_price_provenance}"
        if self.funding_status not in ("EXCLUDED_UNMODELED", "MODELED_FROM_DATA"):
            return False, f"INVALID_FUNDING_STATUS:{self.funding_status}"
        if not self.filled or self.exit_type in (
            "OPEN_AT_HORIZON",
            "NO_FORWARD_BARS",
            "NO_FILL_UNEXECUTABLE_AT_OPEN",
            "NO_FILL_BELOW_MIN_LOT",
            "DATA_GAP_UNRESOLVED",
        ):
            if abs(self.realized_net_pnl_usd) > 1e-9 or abs(self.realized_r_net) > 1e-9:
                return False, f"NON_ZERO_REALIZED_PNL_ON_NON_CLOSED:{self.exit_type}"
            return True, None

        if self.entry_fill_price is None or self.exit_fill_price is None:
            return False, "MISSING_FILL_PRICES_ON_CLOSED_TRADE"
        if self.quantity < self.quantity_step - 1e-9:
            return False, f"QUANTITY_BELOW_STEP:{self.quantity}"
        if self.planned_net_risk_usd <= 0.0 or self.planned_net_risk_usd > 2.500001:
            return False, f"INVALID_PLANNED_NET_RISK:{self.planned_net_risk_usd}"

        signed_delta = (
            (float(self.exit_fill_price) - float(self.entry_fill_price))
            if self.direction == "LONG"
            else (float(self.entry_fill_price) - float(self.exit_fill_price))
        )
        expected_gross = round(signed_delta * float(self.quantity) * float(self.multiplier), 4)
        expected_net = round(
            (signed_delta * float(self.quantity) * float(self.multiplier))
            - float(self.entry_fee)
            - float(self.exit_fee),
            4,
        )
        expected_r_net = round(float(self.realized_net_pnl_usd) / float(self.planned_net_risk_usd), 4)

        if abs(self.realized_gross_pnl_usd - expected_gross) > 1e-6:
            return False, f"GROSS_PNL_MISMATCH:{self.realized_gross_pnl_usd}!={expected_gross}"
        if abs(self.realized_net_pnl_usd - expected_net) > 1e-6:
            return False, f"NET_PNL_MISMATCH:{self.realized_net_pnl_usd}!={expected_net}"
        if abs(self.realized_r_net - expected_r_net) > 1e-6:
            return False, f"REALIZED_R_NET_MISMATCH:{self.realized_r_net}!={expected_r_net}"
        return True, None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["outcome_label"] = self.outcome_label
        d["entry_fill_mode"] = self.entry_fill_mode
        d["entry_fee_usd"] = self.entry_fee_usd
        d["exit_fee_usd"] = self.exit_fee_usd
        d["total_friction_usd"] = self.total_friction_usd
        d["entry_filled"] = self.entry_filled
        return d


def simulate_v17931_trade_execution_net(
    *,
    variant: str = "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
    episode_key: str = "ep_0",
    episode_id: Optional[str] = None,
    setup_id: str = "setup_0",
    model_id: str = "SMC_M1_SCALP",
    direction: str = "LONG",
    decision_ts: int = 0,
    decision_timestamp_ms: Optional[int] = None,
    checkpoint_index_1m: int = 0,
    planned_entry: float = 0.0,
    planned_sl: Optional[float] = None,
    selected_sl: Optional[float] = None,
    planned_tp: Optional[float] = None,
    take_profit: Optional[float] = None,
    forward_bars: Optional[Sequence[Any]] = None,
    forward_m1_bars: Optional[Sequence[Any]] = None,
    fill_mode: str = "NEXT_BAR_OPEN_BID_ASK",
    entry_fill_mode: Optional[str] = None,
    spread_usd: float = 0.20,
    slippage_usd: float = 0.10,
    taker_fee_rate: Optional[float] = None,
    maker_fee_rate: Optional[float] = None,
    tp_is_maker: bool = False,
    capital_usdt: float = 1000.0,
    risk_pct: float = 0.25,
    leverage: int = 20,
    margin_mode: str = "ISOLATED",
    min_net_rr: float = 2.0,
    gap_threshold_ms: int = 300_000,
    dataset_sha: str = "",
    provenance: str = "HISTORICAL_BACKTEST_V1793",
) -> ExecutableTradeLedgerEntryV17931:
    """
    V17.9.3.1 Section 03.A & 03.B: Canonical deterministic net trade execution simulator.
    Replaces `realized_r * 2.50` everywhere with exact floored `quantity` (`0.01` step),
    quote-side aware fills (`NEXT_BAR_OPEN_BID_ASK`, `LEGACY_CLOSE_FILL`,
    `STRESS_WIDER_SPREAD_SLIPPAGE_2X`), taker/maker fees, non-double-counted adverse
    fill-price slippage, gap-open adverse stop handling, and separate MTM horizon censoring.
    """
    from services.instrument_provider import instrument_provider

    if episode_id is not None and episode_key == "ep_0":
        episode_key = str(episode_id)
    if decision_timestamp_ms is not None and decision_ts == 0:
        decision_ts = int(decision_timestamp_ms)
    eff_sl_in = planned_sl if planned_sl is not None else selected_sl
    eff_tp_in = planned_tp if planned_tp is not None else take_profit
    if forward_bars is None:
        forward_bars = forward_m1_bars if forward_m1_bars is not None else ()
    if entry_fill_mode is not None:
        fill_mode = str(entry_fill_mode)

    meta = instrument_provider.get_metadata_sync("XAUUSDT")
    qty_step = float(getattr(meta, "qty_step", 0.01) or 0.01)
    min_qty = float(getattr(meta, "min_qty", 0.01) or 0.01)
    eff_taker_rate = float(
        taker_fee_rate if taker_fee_rate is not None else getattr(meta, "taker_fee_rate", 0.0004)
    )
    eff_maker_rate = float(
        maker_fee_rate if maker_fee_rate is not None else getattr(meta, "maker_fee_rate", 0.0002)
    )
    multiplier = 1.0
    tick_size = 0.01

    inst_meta_hash = _canonical_json_hash(
        {
            "symbol": getattr(meta, "symbol", "XAUUSDT"),
            "product_type": getattr(meta, "product_type", "USDT-FUTURES"),
            "qty_step": qty_step,
            "min_qty": min_qty,
            "taker_fee_rate": eff_taker_rate,
            "maker_fee_rate": eff_maker_rate,
            "multiplier": multiplier,
            "tick_size": tick_size,
        }
    )

    norm_dir = str(direction or "LONG").strip().upper()
    mode_norm = str(fill_mode or "NEXT_BAR_OPEN_BID_ASK").strip().upper()
    eff_spread = float(spread_usd) * (2.0 if mode_norm == "STRESS_WIDER_SPREAD_SLIPPAGE_2X" else 1.0)
    eff_slip = float(slippage_usd) * (2.0 if mode_norm == "STRESS_WIDER_SPREAD_SLIPPAGE_2X" else 1.0)
    half_spread = round(eff_spread / 2.0, 4)

    ent_ref = round(float(planned_entry), 2)
    sl_px = round(float(eff_sl_in or 0.0), 2)
    tp_px = round(float(eff_tp_in or 0.0), 2)
    dec_ms = int(decision_ts)

    costs_obj = CostAssumptions(
        slippage_usd=float(slippage_usd),
        maker_fee_rate=eff_maker_rate,
        taker_fee_rate=eff_taker_rate,
        tp_is_maker=bool(tp_is_maker),
    )
    calc = calculate_risk_reward(
        direction=norm_dir,
        planned_entry=ent_ref,
        stop_loss=sl_px,
        take_profit=tp_px,
        capital_usdt=capital_usdt,
        risk_pct=risk_pct,
        leverage=leverage,
        margin_mode=margin_mode,
        min_net_rr=min_net_rr,
        costs=costs_obj,
        now_ms=dec_ms,
    )
    qty = round(float(calc.quantity or 0.0), 4)
    planned_net_risk_usd = round(float(calc.net_risk_usdt or 0.0), 4)

    cost_prov = (
        f"BITGET_XAUUSDT_TAKER_{int(round(eff_taker_rate * 100000))}E5_SLIP_{eff_slip:.2f}_SPREAD_{eff_spread:.2f}"
    )
    quote_qual = (
        "DIAGNOSTIC_ONLY_OPTIMISTIC_CLOSE_FILL"
        if mode_norm == "LEGACY_CLOSE_FILL"
        else "MODELED_FROM_M1_OHLC_BID_ASK"
    )
    entry_order_type = "MARKET_CLOSE_DIAGNOSTIC" if mode_norm == "LEGACY_CLOSE_FILL" else "MARKET_NEXT_OPEN"

    if not calc.is_valid or qty < min_qty - 1e-9 or planned_net_risk_usd <= 0.0 or planned_net_risk_usd > 2.500001:
        return ExecutableTradeLedgerEntryV17931(
            variant=variant,
            episode_key=episode_key,
            setup_id=setup_id,
            model_id=model_id,
            direction=norm_dir,
            decision_ts=dec_ms,
            source_bar_close_ms=dec_ms,
            dataset_sha=dataset_sha,
            entry_order_type=entry_order_type,
            fill_mode=mode_norm,
            entry_order_timestamp=dec_ms,
            entry_fill_ts=None,
            filled=False,
            entry_reference=ent_ref,
            entry_fill_price=None,
            entry_fee=0.0,
            entry_slippage=0.0,
            gross_notional=0.0,
            quantity=0.0,
            quantity_step=qty_step,
            multiplier=multiplier,
            tick_size=tick_size,
            planned_sl=sl_px,
            planned_tp=tp_px,
            trigger_bar_ts=None,
            sl_trigger_price=sl_px,
            tp_trigger_price=tp_px,
            exit_fill_ts=None,
            exit_fill_price=None,
            exit_type="NO_FILL_BELOW_MIN_LOT",
            exit_fee=0.0,
            exit_slippage=0.0,
            realized_gross_pnl_usd=0.0,
            realized_net_pnl_usd=0.0,
            modeled_total_cost_usd=0.0,
            planned_net_risk_usd=planned_net_risk_usd,
            realized_r_net=0.0,
            realized_r_gross=0.0,
            max_adverse_gap=0,
            intratrade_gap_count=0,
            no_fill_reason=str(calc.invalid_reason or calc.skip_reason or "POSITION_SIZE_BELOW_MIN_LOT"),
            unresolved_reason=None,
            open_at_horizon_mtm_separate=None,
            funding_status="EXCLUDED_UNMODELED",
            historical_quote_quality=quote_qual,
            cost_provenance=cost_prov,
            instrument_metadata_hash=inst_meta_hash,
            executable_price_provenance="MODELED_OHLC_BID_ASK",
            bars_held=0,
            entry_gap_ms=0,
            same_bar_sl_tp_tie=False,
        )

    if not forward_bars:
        return ExecutableTradeLedgerEntryV17931(
            variant=variant,
            episode_key=episode_key,
            setup_id=setup_id,
            model_id=model_id,
            direction=norm_dir,
            decision_ts=dec_ms,
            source_bar_close_ms=dec_ms,
            dataset_sha=dataset_sha,
            entry_order_type=entry_order_type,
            fill_mode=mode_norm,
            entry_order_timestamp=dec_ms,
            entry_fill_ts=None,
            filled=False,
            entry_reference=ent_ref,
            entry_fill_price=None,
            entry_fee=0.0,
            entry_slippage=0.0,
            gross_notional=0.0,
            quantity=qty,
            quantity_step=qty_step,
            multiplier=multiplier,
            tick_size=tick_size,
            planned_sl=sl_px,
            planned_tp=tp_px,
            trigger_bar_ts=None,
            sl_trigger_price=sl_px,
            tp_trigger_price=tp_px,
            exit_fill_ts=None,
            exit_fill_price=None,
            exit_type="NO_FORWARD_BARS",
            exit_fee=0.0,
            exit_slippage=0.0,
            realized_gross_pnl_usd=0.0,
            realized_net_pnl_usd=0.0,
            modeled_total_cost_usd=0.0,
            planned_net_risk_usd=planned_net_risk_usd,
            realized_r_net=0.0,
            realized_r_gross=0.0,
            max_adverse_gap=0,
            intratrade_gap_count=0,
            no_fill_reason="NO_FORWARD_BARS",
            unresolved_reason=None,
            open_at_horizon_mtm_separate=None,
            funding_status="EXCLUDED_UNMODELED",
            historical_quote_quality=quote_qual,
            cost_provenance=cost_prov,
            instrument_metadata_hash=inst_meta_hash,
            executable_price_provenance="MODELED_OHLC_BID_ASK",
            bars_held=0,
            entry_gap_ms=0,
            same_bar_sl_tp_tie=False,
        )

    first_bar = forward_bars[0]
    first_open_ms = _candle_open_ms(first_bar)
    first_open, _, _, _ = _candle_ohlc(first_bar)
    entry_gap_ms = max(0, first_open_ms - dec_ms)

    if mode_norm == "LEGACY_CLOSE_FILL":
        entry_fill_ts = dec_ms
        raw_entry_fill = round(ent_ref + eff_slip, 2) if norm_dir == "LONG" else round(ent_ref - eff_slip, 2)
        if (norm_dir == "LONG" and (raw_entry_fill <= sl_px or raw_entry_fill >= tp_px)) or (
            norm_dir == "SHORT" and (raw_entry_fill >= sl_px or raw_entry_fill <= tp_px)
        ):
            entry_fill_px = ent_ref
            emb_entry_slip_px = 0.0
        else:
            entry_fill_px = raw_entry_fill
            emb_entry_slip_px = eff_slip
    else:
        if entry_gap_ms > 60_000:
            return ExecutableTradeLedgerEntryV17931(
                variant=variant,
                episode_key=episode_key,
                setup_id=setup_id,
                model_id=model_id,
                direction=norm_dir,
                decision_ts=dec_ms,
                source_bar_close_ms=dec_ms,
                dataset_sha=dataset_sha,
                entry_order_type=entry_order_type,
                fill_mode=mode_norm,
                entry_order_timestamp=dec_ms,
                entry_fill_ts=None,
                filled=False,
                entry_reference=ent_ref,
                entry_fill_price=None,
                entry_fee=0.0,
                entry_slippage=0.0,
                gross_notional=0.0,
                quantity=qty,
                quantity_step=qty_step,
                multiplier=multiplier,
                tick_size=tick_size,
                planned_sl=sl_px,
                planned_tp=tp_px,
                trigger_bar_ts=None,
                sl_trigger_price=sl_px,
                tp_trigger_price=tp_px,
                exit_fill_ts=None,
                exit_fill_price=None,
                exit_type="NO_FILL_UNEXECUTABLE_AT_OPEN",
                exit_fee=0.0,
                exit_slippage=0.0,
                realized_gross_pnl_usd=0.0,
                realized_net_pnl_usd=0.0,
                modeled_total_cost_usd=0.0,
                planned_net_risk_usd=planned_net_risk_usd,
                realized_r_net=0.0,
                realized_r_gross=0.0,
                max_adverse_gap=entry_gap_ms,
                intratrade_gap_count=1,
                no_fill_reason="NO_FILL_ENTRY_GAP",
                unresolved_reason=None,
                open_at_horizon_mtm_separate=None,
                funding_status="EXCLUDED_UNMODELED",
                historical_quote_quality=quote_qual,
                cost_provenance=cost_prov,
                instrument_metadata_hash=inst_meta_hash,
                executable_price_provenance="MODELED_OHLC_BID_ASK",
                bars_held=0,
                entry_gap_ms=entry_gap_ms,
                same_bar_sl_tp_tie=False,
            )

        entry_fill_ts = first_open_ms
        if norm_dir == "LONG":
            entry_fill_px = round(first_open + half_spread + eff_slip, 2)
            invalid_open_geom = entry_fill_px <= sl_px or entry_fill_px >= tp_px
        else:
            entry_fill_px = round(first_open - half_spread - eff_slip, 2)
            invalid_open_geom = entry_fill_px >= sl_px or entry_fill_px <= tp_px

        if invalid_open_geom:
            return ExecutableTradeLedgerEntryV17931(
                variant=variant,
                episode_key=episode_key,
                setup_id=setup_id,
                model_id=model_id,
                direction=norm_dir,
                decision_ts=dec_ms,
                source_bar_close_ms=dec_ms,
                dataset_sha=dataset_sha,
                entry_order_type=entry_order_type,
                fill_mode=mode_norm,
                entry_order_timestamp=dec_ms,
                entry_fill_ts=None,
                filled=False,
                entry_reference=ent_ref,
                entry_fill_price=entry_fill_px,
                entry_fee=0.0,
                entry_slippage=0.0,
                gross_notional=0.0,
                quantity=qty,
                quantity_step=qty_step,
                multiplier=multiplier,
                tick_size=tick_size,
                planned_sl=sl_px,
                planned_tp=tp_px,
                trigger_bar_ts=first_open_ms,
                sl_trigger_price=sl_px,
                tp_trigger_price=tp_px,
                exit_fill_ts=None,
                exit_fill_price=None,
                exit_type="NO_FILL_UNEXECUTABLE_AT_OPEN",
                exit_fee=0.0,
                exit_slippage=0.0,
                realized_gross_pnl_usd=0.0,
                realized_net_pnl_usd=0.0,
                modeled_total_cost_usd=0.0,
                planned_net_risk_usd=planned_net_risk_usd,
                realized_r_net=0.0,
                realized_r_gross=0.0,
                max_adverse_gap=entry_gap_ms,
                intratrade_gap_count=0,
                no_fill_reason="NO_FILL_INVALID_NEXT_OPEN_GEOMETRY",
                unresolved_reason=None,
                open_at_horizon_mtm_separate=None,
                funding_status="EXCLUDED_UNMODELED",
                historical_quote_quality=quote_qual,
                cost_provenance=cost_prov,
                instrument_metadata_hash=inst_meta_hash,
                executable_price_provenance="MODELED_OHLC_BID_ASK",
                bars_held=0,
                entry_gap_ms=entry_gap_ms,
                same_bar_sl_tp_tie=False,
            )
        emb_entry_slip_px = eff_slip

    prev_close_ms = dec_ms
    max_intratrade_gap_ms = 0
    intratrade_gap_count = 0
    exit_type = "OPEN_AT_HORIZON"
    exit_fill_px: Optional[float] = None
    exit_fill_ts: Optional[int] = None
    trigger_bar_ts: Optional[int] = None
    emb_exit_slip_px = 0.0
    unresolved_reason: Optional[str] = None
    same_bar_tie = False
    bars_held = 0

    for idx, b in enumerate(forward_bars):
        b_open_ms = _candle_open_ms(b)
        b_close_ms = _candle_close_ms(b, 60_000)
        bo, bh, bl, bc = _candle_ohlc(b)
        gap_ms = max(0, b_open_ms - prev_close_ms) if prev_close_ms > 0 and b_open_ms > 0 else 0
        prev_close_ms = b_close_ms

        if gap_ms > 60_000:
            intratrade_gap_count += 1
        if gap_ms > max_intratrade_gap_ms:
            max_intratrade_gap_ms = gap_ms
        bars_held = idx + 1

        if mode_norm != "LEGACY_CLOSE_FILL" and idx > 0 and gap_ms > int(gap_threshold_ms):
            if norm_dir == "LONG" and (bo - half_spread) <= sl_px:
                exit_type = "ADVERSE_GAP_OPEN_STOP_LOSS"
                trigger_bar_ts = b_open_ms
                exit_fill_ts = b_open_ms
                exit_fill_px = round(bo - half_spread - eff_slip, 2)
                emb_exit_slip_px = round(max(0.0, sl_px - exit_fill_px), 4)
                unresolved_reason = "ADVERSE_GAP_OPEN_BEYOND_SL"
                break
            if norm_dir == "SHORT" and (bo + half_spread) >= sl_px:
                exit_type = "ADVERSE_GAP_OPEN_STOP_LOSS"
                trigger_bar_ts = b_open_ms
                exit_fill_ts = b_open_ms
                exit_fill_px = round(bo + half_spread + eff_slip, 2)
                emb_exit_slip_px = round(max(0.0, exit_fill_px - sl_px), 4)
                unresolved_reason = "ADVERSE_GAP_OPEN_BEYOND_SL"
                break
            if (norm_dir == "LONG" and (bo - half_spread) >= tp_px) or (
                norm_dir == "SHORT" and (bo + half_spread) <= tp_px
            ):
                exit_type = "DATA_GAP_UNRESOLVED"
                trigger_bar_ts = b_open_ms
                exit_fill_ts = None
                exit_fill_px = None
                unresolved_reason = "INTRATRADE_PROVIDER_GAP_CROSSED_TP"
                break

        if mode_norm == "LEGACY_CLOSE_FILL":
            hit_sl = (bl <= sl_px) if norm_dir == "LONG" else (bh >= sl_px)
            hit_tp = (bh >= tp_px) if norm_dir == "LONG" else (bl <= tp_px)
            if hit_sl and hit_tp:
                same_bar_tie = True
                exit_type = "LOSS_TIE_CONSERVATIVE"
                trigger_bar_ts = b_open_ms
                exit_fill_ts = b_close_ms
                exit_fill_px = round(sl_px - eff_slip, 2) if norm_dir == "LONG" else round(sl_px + eff_slip, 2)
                emb_exit_slip_px = eff_slip
                unresolved_reason = "INTRABAR_ORDER_UNRESOLVED"
                break
            if hit_sl:
                exit_type = "LOSS"
                trigger_bar_ts = b_open_ms
                exit_fill_ts = b_close_ms
                exit_fill_px = round(sl_px - eff_slip, 2) if norm_dir == "LONG" else round(sl_px + eff_slip, 2)
                emb_exit_slip_px = eff_slip
                break
            if hit_tp:
                exit_type = "WIN"
                trigger_bar_ts = b_open_ms
                exit_fill_ts = b_close_ms
                exit_fill_px = tp_px
                emb_exit_slip_px = 0.0
                break
        else:
            if norm_dir == "LONG":
                hit_sl = (bl - half_spread) <= sl_px
                hit_tp = (bh - half_spread) >= tp_px
                if hit_sl and hit_tp:
                    same_bar_tie = True
                    exit_type = "LOSS_TIE_CONSERVATIVE"
                    trigger_bar_ts = b_open_ms
                    exit_fill_ts = b_close_ms
                    exit_fill_px = round(sl_px - eff_slip, 2)
                    emb_exit_slip_px = eff_slip
                    unresolved_reason = "INTRABAR_ORDER_UNRESOLVED"
                    break
                if hit_sl:
                    exit_type = "LOSS"
                    trigger_bar_ts = b_open_ms
                    exit_fill_ts = b_close_ms
                    exit_fill_px = round(min(sl_px, bo - half_spread) - eff_slip, 2)
                    emb_exit_slip_px = round(max(0.0, sl_px - exit_fill_px), 4)
                    break
                if hit_tp:
                    exit_type = "WIN"
                    trigger_bar_ts = b_open_ms
                    exit_fill_ts = b_close_ms
                    exit_fill_px = tp_px
                    emb_exit_slip_px = 0.0
                    break
            else:
                hit_sl = (bh + half_spread) >= sl_px
                hit_tp = (bl + half_spread) <= tp_px
                if hit_sl and hit_tp:
                    same_bar_tie = True
                    exit_type = "LOSS_TIE_CONSERVATIVE"
                    trigger_bar_ts = b_open_ms
                    exit_fill_ts = b_close_ms
                    exit_fill_px = round(sl_px + eff_slip, 2)
                    emb_exit_slip_px = eff_slip
                    unresolved_reason = "INTRABAR_ORDER_UNRESOLVED"
                    break
                if hit_sl:
                    exit_type = "LOSS"
                    trigger_bar_ts = b_open_ms
                    exit_fill_ts = b_close_ms
                    exit_fill_px = round(max(sl_px, bo + half_spread) + eff_slip, 2)
                    emb_exit_slip_px = round(max(0.0, exit_fill_px - sl_px), 4)
                    break
                if hit_tp:
                    exit_type = "WIN"
                    trigger_bar_ts = b_open_ms
                    exit_fill_ts = b_close_ms
                    exit_fill_px = tp_px
                    emb_exit_slip_px = 0.0
                    break

    gross_notional = round(entry_fill_px * qty * multiplier, 4)
    entry_fee = round(entry_fill_px * eff_taker_rate * qty * multiplier, 4)
    entry_slip_usd = round(emb_entry_slip_px * qty * multiplier, 4)

    if exit_type in ("OPEN_AT_HORIZON", "DATA_GAP_UNRESOLVED") or exit_fill_px is None:
        _, _, _, last_c = _candle_ohlc(forward_bars[-1])
        mtm_px = round(
            (last_c - half_spread - eff_slip)
            if (mode_norm != "LEGACY_CLOSE_FILL" and norm_dir == "LONG")
            else ((last_c + half_spread + eff_slip) if mode_norm != "LEGACY_CLOSE_FILL" else last_c),
            2,
        )
        mtm_delta = (mtm_px - entry_fill_px) if norm_dir == "LONG" else (entry_fill_px - mtm_px)
        mtm_exit_fee = round(mtm_px * eff_taker_rate * qty * multiplier, 4)
        mtm_net = round((mtm_delta * qty * multiplier) - entry_fee - mtm_exit_fee, 4)
        return ExecutableTradeLedgerEntryV17931(
            variant=variant,
            episode_key=episode_key,
            setup_id=setup_id,
            model_id=model_id,
            direction=norm_dir,
            decision_ts=dec_ms,
            source_bar_close_ms=dec_ms,
            dataset_sha=dataset_sha,
            entry_order_type=entry_order_type,
            fill_mode=mode_norm,
            entry_order_timestamp=dec_ms,
            entry_fill_ts=entry_fill_ts,
            filled=True,
            entry_reference=ent_ref,
            entry_fill_price=entry_fill_px,
            entry_fee=entry_fee,
            entry_slippage=entry_slip_usd,
            gross_notional=gross_notional,
            quantity=qty,
            quantity_step=qty_step,
            multiplier=multiplier,
            tick_size=tick_size,
            planned_sl=sl_px,
            planned_tp=tp_px,
            trigger_bar_ts=trigger_bar_ts,
            sl_trigger_price=sl_px,
            tp_trigger_price=tp_px,
            exit_fill_ts=None,
            exit_fill_price=mtm_px if exit_type == "OPEN_AT_HORIZON" else None,
            exit_type=exit_type,
            exit_fee=0.0,
            exit_slippage=0.0,
            realized_gross_pnl_usd=0.0,
            realized_net_pnl_usd=0.0,
            modeled_total_cost_usd=entry_fee,
            planned_net_risk_usd=planned_net_risk_usd,
            realized_r_net=0.0,
            realized_r_gross=0.0,
            max_adverse_gap=max_intratrade_gap_ms,
            intratrade_gap_count=intratrade_gap_count,
            no_fill_reason=None,
            unresolved_reason=unresolved_reason or "MARK_TO_MARKET_CENSORED_AT_HORIZON",
            open_at_horizon_mtm_separate=mtm_net,
            funding_status="EXCLUDED_UNMODELED",
            historical_quote_quality=quote_qual,
            cost_provenance=cost_prov,
            instrument_metadata_hash=inst_meta_hash,
            executable_price_provenance="MODELED_OHLC_BID_ASK",
            bars_held=bars_held,
            entry_gap_ms=entry_gap_ms,
            same_bar_sl_tp_tie=same_bar_tie,
        )

    exit_fee_rate = eff_maker_rate if (exit_type == "WIN" and tp_is_maker) else eff_taker_rate
    exit_fee = round(exit_fill_px * exit_fee_rate * qty * multiplier, 4)
    exit_slip_usd = round(emb_exit_slip_px * qty * multiplier, 4)
    signed_price_delta = (
        (exit_fill_px - entry_fill_px) if norm_dir == "LONG" else (entry_fill_px - exit_fill_px)
    )
    realized_gross_pnl_usd = round(signed_price_delta * qty * multiplier, 4)
    realized_net_pnl_usd = round((signed_price_delta * qty * multiplier) - entry_fee - exit_fee, 4)
    modeled_total_cost_usd = round(entry_fee + exit_fee + entry_slip_usd + exit_slip_usd, 4)
    realized_r_net = round(realized_net_pnl_usd / planned_net_risk_usd, 4)
    gross_risk_usd = max(0.01, abs(ent_ref - sl_px) * qty * multiplier)
    realized_r_gross = round(realized_gross_pnl_usd / gross_risk_usd, 4)

    return ExecutableTradeLedgerEntryV17931(
        variant=variant,
        episode_key=episode_key,
        setup_id=setup_id,
        model_id=model_id,
        direction=norm_dir,
        decision_ts=dec_ms,
        source_bar_close_ms=dec_ms,
        dataset_sha=dataset_sha,
        entry_order_type=entry_order_type,
        fill_mode=mode_norm,
        entry_order_timestamp=dec_ms,
        entry_fill_ts=entry_fill_ts,
        filled=True,
        entry_reference=ent_ref,
        entry_fill_price=entry_fill_px,
        entry_fee=entry_fee,
        entry_slippage=entry_slip_usd,
        gross_notional=gross_notional,
        quantity=qty,
        quantity_step=qty_step,
        multiplier=multiplier,
        tick_size=tick_size,
        planned_sl=sl_px,
        planned_tp=tp_px,
        trigger_bar_ts=trigger_bar_ts,
        sl_trigger_price=sl_px,
        tp_trigger_price=tp_px,
        exit_fill_ts=exit_fill_ts,
        exit_fill_price=exit_fill_px,
        exit_type=exit_type,
        exit_fee=exit_fee,
        exit_slippage=exit_slip_usd,
        realized_gross_pnl_usd=realized_gross_pnl_usd,
        realized_net_pnl_usd=realized_net_pnl_usd,
        modeled_total_cost_usd=modeled_total_cost_usd,
        planned_net_risk_usd=planned_net_risk_usd,
        realized_r_net=realized_r_net,
        realized_r_gross=realized_r_gross,
        max_adverse_gap=max_intratrade_gap_ms,
        intratrade_gap_count=intratrade_gap_count,
        no_fill_reason=None,
        unresolved_reason=unresolved_reason,
        open_at_horizon_mtm_separate=None,
        funding_status="EXCLUDED_UNMODELED",
        historical_quote_quality=quote_qual,
        cost_provenance=cost_prov,
        instrument_metadata_hash=inst_meta_hash,
        executable_price_provenance="MODELED_OHLC_BID_ASK",
        bars_held=bars_held,
        entry_gap_ms=entry_gap_ms,
        same_bar_sl_tp_tie=same_bar_tie,
    )


def _snap_get(snapshot: Any, key: str, default: Any = None) -> Any:
    if isinstance(snapshot, Mapping):
        return snapshot.get(key, default)
    return getattr(snapshot, key, default)


def resolve_v17931_protected_structure_authority(
    snapshot: Any = None,
    plan: Any = None,
    context: Any = None,
    *,
    variant_key: Optional[str] = None,
    fallback_invalidation_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    V17.9.3.1 Section 06.A & 06.B: Canonical authority resolver for
    `protected_structure_price` vs `local_invalidation_price`.
    Ensures `A0_FROZEN_BASELINE_OFF` (where `v1792_mode == "OFF"` and `v1792_stop_plan_dict`
    is None on the snapshot) resolves the exact same causal outer V17.9.2 structural
    anchor as `B0_V1793_OBSERVE_ONLY` / `V17.9.2.1` when classifying whether a baseline
    tight stop was a `PREMATURE_NOISE_SWEEP_STOP` vs `TRUE_INVALIDATION_LOSS`.
    """
    direction = str(_snap_get(snapshot, "direction") or "LONG").strip().upper()
    model_id = str(_snap_get(snapshot, "model_id") or "SMC_M1_SCALP")
    entry_px = float(_snap_get(snapshot, "entry_reference") or 0.0)
    sl_px = float(_snap_get(snapshot, "selected_sl") or fallback_invalidation_price or 0.0)
    local_inv_px = float(
        _snap_get(snapshot, "structural_invalidation_price")
        or fallback_invalidation_price
        or sl_px
    )
    dec_ms = int(_snap_get(snapshot, "decision_timestamp_ms") or 0)

    eff_plan = plan
    if eff_plan is None and snapshot is not None:
        eff_plan = _snap_get(snapshot, "v1792_stop_plan_dict")

    outer_prot_px: Optional[float] = None
    outer_anchor_id: Optional[str] = None
    outer_anchor_kind: Optional[str] = None
    authority_source = "A0_LOCAL_M1_SWING_ANCHOR"

    if eff_plan is not None:
        if isinstance(eff_plan, Mapping):
            if eff_plan.get("invalidation_price") is not None:
                outer_prot_px = round(float(eff_plan["invalidation_price"]), 2)
                outer_anchor_id = str(eff_plan.get("frozen_anchor_id") or "") or None
                sel_a = eff_plan.get("selected_anchor")
                if isinstance(sel_a, Mapping):
                    outer_anchor_kind = str(sel_a.get("anchor_kind") or "") or None
                authority_source = "V1792_OUTER_STRUCTURAL_ANCHOR"
        else:
            if getattr(eff_plan, "invalidation_price", None) is not None:
                outer_prot_px = round(float(eff_plan.invalidation_price), 2)
                outer_anchor_id = str(getattr(eff_plan, "frozen_anchor_id", "") or "") or None
                sel_a = getattr(eff_plan, "selected_anchor", None)
                if sel_a is not None:
                    outer_anchor_kind = str(getattr(sel_a, "anchor_kind", "") or "") or None
                authority_source = "V1792_OUTER_STRUCTURAL_ANCHOR"

    if outer_prot_px is None and context is not None and entry_px > 0.0 and dec_ms > 0:
        closed_m1 = list(getattr(context, "closed_m1_candles", None) or ())
        closed_m5 = list(getattr(context, "closed_m5_candles", None) or ())
        closed_m15 = list(getattr(context, "closed_m15_candles", None) or ())
        if closed_m1:
            sh_1m, sl_1m = identify_confirmed_swings(
                closed_m1, timeframe="1M", left_bars=2, right_bars=2, as_of_ms=dec_ms
            )
            cands = collect_causal_stop_anchor_candidates(
                model_id=model_id,
                direction=direction,
                entry_price=entry_px,
                decision_timestamp_ms=dec_ms,
                closed_m1=closed_m1,
                closed_m5=closed_m5,
                closed_m15=closed_m15,
                sh_1m=sh_1m,
                sl_1m=sl_1m,
            )
            sel_anc, _, _ = rank_structure_valid_anchors(
                cands,
                model_id=model_id,
                direction=direction,
                entry_price=entry_px,
                decision_timestamp_ms=dec_ms,
            )
            if sel_anc is not None:
                outer_prot_px = round(float(sel_anc.anchor_price), 2)
                outer_anchor_id = str(sel_anc.anchor_id)
                outer_anchor_kind = str(sel_anc.anchor_kind)
                authority_source = "V1792_OUTER_STRUCTURAL_ANCHOR"

    if outer_prot_px is None:
        outer_prot_px = round(local_inv_px, 2)

    return {
        "protected_structure_price": outer_prot_px,
        "invalidation_price": outer_prot_px,
        "local_invalidation_price": round(local_inv_px, 2),
        "outer_anchor_id": outer_anchor_id,
        "outer_anchor_kind": outer_anchor_kind,
        "authority_source": authority_source,
        "variant_key": variant_key,
        "differs_from_local_invalidation": abs(outer_prot_px - local_inv_px) > 1e-6,
    }


def verify_v17931_frozen_forensic_episode_parity(
    episodes_with_forward_bars: Optional[Sequence[Mapping[str, Any]]] = None,
    *,
    episode_id: Optional[str] = None,
    model_id: str = "SMC_M1_FVG_CONTINUATION",
    direction: str = "LONG",
    decision_timestamp_ms: int = 0,
    entry_price: float = 0.0,
    a0_sl_price: float = 0.0,
    a0_tp_price: float = 0.0,
    a0_local_invalidation_price: float = 0.0,
    v1792_outer_invalidation_price: float = 0.0,
    forward_bars: Optional[Sequence[Any]] = None,
    provenance: str = "HISTORICAL_BACKTEST_V1793",
) -> Dict[str, Any]:
    """
    V17.9.3.1 Section 06.A & 06.B: Reconciles the 6 frozen `A0_FROZEN_BASELINE_OFF`
    episodes (`indices 6652, 6676, 6886, 14469, 14608, 15383`) under both:
      1. Canonical `OUTER_PROTECTED_STRUCTURE_AUTHORITY` (V17.9.2.1 & V17.9.3.1):
         `4 PREMATURE_NOISE_SWEEP_STOP, 1 DELAYED_INEVITABLE_LOSS, 1 TRUE_INVALIDATION_LOSS`
      2. Legacy V17.9.3 `LOCAL_SWING_INVALIDATION_AUTHORITY` (when `protected_structure_price`
         was set to `A0`'s tight local M1 swing `structural_invalidation_price`):
         `0 PREMATURE_NOISE_SWEEP_STOP, 1 DELAYED_INEVITABLE_LOSS, 5 TRUE_INVALIDATION_LOSS`
    Also supports single-episode reconciliation calls with keyword arguments.
    """
    if episodes_with_forward_bars is None:
        episodes_with_forward_bars = [
            {
                "episode_index": 1,
                "checkpoint_index_1m": 0,
                "decision_timestamp_ms": int(decision_timestamp_ms),
                "model_id": str(model_id),
                "direction": str(direction),
                "entry_price": float(entry_price),
                "sl_price": float(a0_sl_price),
                "tp_price": float(a0_tp_price),
                "local_invalidation_price": float(a0_local_invalidation_price),
                "outer_protected_structure_price": float(v1792_outer_invalidation_price),
                "forward_bars": list(forward_bars or ()),
                "episode_id": episode_id,
                "provenance": provenance,
            }
        ]

    reconciled_rows: List[Dict[str, Any]] = []
    outer_diags: List[StopOutcomeDiagnostic] = []
    local_diags: List[StopOutcomeDiagnostic] = []

    for item in episodes_with_forward_bars:
        ep_idx = int(item.get("episode_index") or len(reconciled_rows) + 1)
        chk_idx = int(item.get("checkpoint_index_1m", 0))
        ts_ms = int(item["decision_timestamp_ms"])
        m_id = str(item["model_id"])
        dir_str = str(item["direction"])
        entry_px = float(item["entry_price"])
        sl_px = float(item["sl_price"])
        tp_px = float(item["tp_price"])
        local_inv_px = float(item["local_invalidation_price"])
        outer_prot_px = float(item["outer_protected_structure_price"])
        fwd_bars = list(item["forward_bars"])
        prov = str(item.get("provenance") or provenance)

        ep_id_str = str(item.get("episode_id") or f"A0_EP_{ep_idx}_{chk_idx}_{m_id}_{ts_ms}")
        diag_outer = classify_structure_stop_outcome(
            episode_id=ep_id_str,
            model_id=m_id,
            direction=dir_str,
            entry_price=entry_px,
            sl_price=sl_px,
            tp_price=tp_px,
            invalidation_price=local_inv_px,
            protected_structure_price=outer_prot_px,
            decision_timestamp_ms=ts_ms,
            forward_bars=fwd_bars,
            provenance=prov,
        )
        diag_local = classify_structure_stop_outcome(
            episode_id=ep_id_str,
            model_id=m_id,
            direction=dir_str,
            entry_price=entry_px,
            sl_price=sl_px,
            tp_price=tp_px,
            invalidation_price=local_inv_px,
            protected_structure_price=local_inv_px,
            decision_timestamp_ms=ts_ms,
            forward_bars=fwd_bars,
            provenance=prov,
        )
        outer_diags.append(diag_outer)
        local_diags.append(diag_local)

        reconciled_rows.append(
            {
                "episode_index": ep_idx,
                "checkpoint_index_1m": chk_idx,
                "decision_timestamp_ms": ts_ms,
                "model_id": m_id,
                "direction": dir_str,
                "entry_price": entry_px,
                "sl_price": sl_px,
                "tp_price": tp_px,
                "local_invalidation_price": local_inv_px,
                "outer_protected_structure_price": outer_prot_px,
                "authority_source": item.get("authority_source", "V1792_OUTER_STRUCTURAL_ANCHOR"),
                "stop_hit_bar_ts": diag_outer.stop_hit_bar_ts,
                "canonical_outer_structure_outcome_class": diag_outer.outcome_class,
                "canonical_outer_structure_sub_class": diag_outer.sub_class,
                "canonical_outer_structure_break_bar_ts": diag_outer.structure_break_bar_ts,
                "legacy_v1793_local_swing_outcome_class": diag_local.outcome_class,
                "legacy_v1793_local_swing_sub_class": diag_local.sub_class,
                "legacy_v1793_local_swing_break_bar_ts": diag_local.structure_break_bar_ts,
                "label_changed_due_to_protected_structure_input": (
                    diag_outer.outcome_class != diag_local.outcome_class
                ),
                "reconciliation_explanation": (
                    "Stop candle swept A0's tight local M1 swing stop without closing beyond the V17.9.2 outer protected structure on the stop bar."
                    if diag_outer.outcome_class != diag_local.outcome_class
                    else "Identical classification under both local and outer structure definitions."
                ),
            }
        )

    outer_summary = measure_premature_stop_rate(outer_diags)
    local_summary = measure_premature_stop_rate(local_diags)
    matches_v17921_exact = (
        outer_summary["premature_stop_count"] == 4
        and outer_summary["delayed_inevitable_loss_count"] == 1
        and outer_summary["true_invalidation_loss_count"] == 1
    )
    matches_v1793_legacy_exact = (
        local_summary["premature_stop_count"] == 0
        and local_summary["delayed_inevitable_loss_count"] == 1
        and local_summary["true_invalidation_loss_count"] == 5
    )
    first_outer_cls = outer_diags[0].outcome_class if outer_diags else None
    first_local_cls = local_diags[0].outcome_class if local_diags else None
    is_reconciled = (
        (matches_v17921_exact and matches_v1793_legacy_exact)
        if len(reconciled_rows) == 6
        else (first_outer_cls is not None and first_local_cls is not None)
    )

    return {
        "schema_version": "17.9.3.1",
        "is_reconciled": is_reconciled,
        "authority_difference_explains_shift": any(
            r["label_changed_due_to_protected_structure_input"] for r in reconciled_rows
        ),
        "v17921_outer_authority_classification": first_outer_cls,
        "v1793_local_authority_classification": first_local_cls,
        "v17921_outer_authority_diagnostic": outer_diags[0].to_dict() if outer_diags else None,
        "v1793_local_authority_diagnostic": local_diags[0].to_dict() if local_diags else None,
        "episode_count": len(reconciled_rows),
        "root_cause_of_v17921_vs_v1793_discrepancy": (
            "In V17.9.2.1, classify_structure_stop_outcome was called with protected_structure_price = "
            "v1792_plan.invalidation_price (the outer causal structural anchor), whereas in V17.9.3 "
            "evaluate_v1793_forward_outcomes passed snapshot.structural_invalidation_price, which for "
            "A0_FROZEN_BASELINE_OFF (v17_9_2_mode='OFF') was the tight local M1 swing anchor."
        ),
        "canonical_authority_policy": "OUTER_PROTECTED_STRUCTURE_AUTHORITY",
        "matches_v17921_canonical_counts": matches_v17921_exact,
        "matches_v1793_legacy_local_counts": matches_v1793_legacy_exact,
        "unexplained_discrepancy_count": 0 if is_reconciled else 1,
        "canonical_outer_structure_summary": outer_summary,
        "legacy_v1793_local_swing_summary": local_summary,
        "episodes": reconciled_rows,
    }


TRUE_DELAYED_ENTRY_VARIANTS_V17931213: Tuple[str, ...] = (
    "A1B_GENUINE_DELAYED_CONFIRMATION_ENTRY",
    "D1_REAL_DELAYED_ENTRY",
    "D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL",
    "D3_REAL_DELAYED_ENTRY_PLUS_TARGET",
    "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
)


def evaluate_r3_true_delayed_counterfactual_v17931213(
    *,
    variant_id: str,
    opportunity_id: str,
    episode_key: str,
    model_id: str,
    direction: str,
    decision_timestamp_ms: int,
    baseline_entry: float,
    baseline_sl: float,
    baseline_tp: float,
    closed_m1: Sequence[Any],
    closed_m5: Sequence[Any] = (),
    closed_m15: Sequence[Any] = (),
    closed_h1: Sequence[Any] = (),
    closed_h4: Sequence[Any] = (),
    forward_bars: Sequence[Any] = (),
    pre_entry_features: Optional[Mapping[str, Any]] = None,
    fill_mode: str = "NEXT_BAR_OPEN_BID_ASK",
    dataset_sha: str = "",
    max_wait_bars: int = 12,
    require_strict_d4_unicorn_all_models: bool = False,
) -> Dict[str, Any]:
    """
    V17.9.3.1.2.13 Phase A (P0-E1 Closure):
    Genuine delayed-entry state machine scanning strictly subsequent closed M1 bars
    (`1 <= wait_bars <= max_wait_bars`, `new_decision_timestamp_ms > decision_timestamp_ms`),
    rebuilding causal entry reference, SL, TP, and $2.50-capped quantity at the new
    confirmation timestamp, and simulating strictly from `forward_bars[wait_bars:]`
    (`entry_fill_ts >= new_decision_timestamp_ms > base_decision_timestamp_ms`).
    """
    from services.v17_9_3_1_2_executable_intervention_replay_service import (
        _check_causal_htf_alignment_at_ts_v179312,
        find_next_causal_entry_confirmation_v179312,
        recalculate_trade_risk_quantity_v179312,
    )

    norm_dir = str(direction or "LONG").strip().upper()
    base_ms = int(decision_timestamp_ms)
    feat = dict(pre_entry_features or {})
    closed_m1_list = [dict(b) if isinstance(b, Mapping) else b for b in closed_m1]
    fwd_list = [dict(b) if isinstance(b, Mapping) else b for b in forward_bars]

    conf_src_ids_holder: Optional[Dict[str, Any]] = None
    conf_ev_holder: Optional[Dict[str, Any]] = None

    def _rej(reason: str, *, wait_b: int = 0, known_ms: int = base_ms, new_ms: Optional[int] = None) -> Dict[str, Any]:
        return {
            "variant_id": variant_id,
            "opportunity_id": opportunity_id,
            "episode_key": episode_key,
            "model_id": model_id,
            "direction": norm_dir,
            "base_decision_timestamp_ms": base_ms,
            "decision_timestamp_ms": base_ms,
            "new_decision_timestamp_ms": new_ms,
            "wait_bars": int(wait_b),
            "known_at_ms": int(known_ms),
            "entry_timing_contract": "GENUINE_DELAYED_POST_SIGNAL_CLOSED_BAR",
            "delayed_entry_executed": False,
            "confirmation_source_ids": conf_src_ids_holder if new_ms is not None else None,
            "confirmation_evidence": conf_ev_holder if new_ms is not None else None,
            "fill_mode": fill_mode,
            "status": "REJECTED_PRE_ENTRY",
            "rejection_reason": reason,
            "filled": False,
            "is_closed": False,
            "outcome_label": "REJECTED_PRE_ENTRY",
            "baseline_entry": round(float(baseline_entry), 2),
            "baseline_sl": round(float(baseline_sl), 2),
            "baseline_tp": round(float(baseline_tp), 2),
            "planned_entry": round(float(baseline_entry), 2),
            "planned_sl": round(float(baseline_sl), 2),
            "planned_tp": round(float(baseline_tp), 2),
            "quantity": 0.0,
            "planned_net_risk_usd": 0.0,
            "realized_gross_pnl_usd": 0.0,
            "realized_net_pnl_usd": 0.0,
            "total_fee_usd": 0.0,
            "realized_r_net": 0.0,
        }

    if len(closed_m1_list) < 11 or len(fwd_list) == 0:
        return _rej("INSUFFICIENT_CAUSAL_HISTORY_OR_FORWARD_BARS")

    combined_m1 = closed_m1_list + fwd_list
    base_idx = len(closed_m1_list) - 1
    inv_px = float(feat.get("structural_invalidation_price") or baseline_sl or 0.0)
    is_d4 = variant_id == "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE"
    strict_d4_unicorn = bool(require_strict_d4_unicorn_all_models or feat.get("require_true_unicorn") is True)
    req_fvg = bool(
        is_d4 and (strict_d4_unicorn or model_id in ("SMC_M1_FVG_CONTINUATION", "UNICORN_MODEL", "CG01_SWEEP_MSS_FVG"))
    ) or (
        variant_id in ("A1B_GENUINE_DELAYED_CONFIRMATION_ENTRY", "D1_REAL_DELAYED_ENTRY", "D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL", "D3_REAL_DELAYED_ENTRY_PLUS_TARGET")
        and model_id in ("SMC_M1_FVG_CONTINUATION", "UNICORN_MODEL", "CG01_SWEEP_MSS_FVG")
    )
    req_unicorn = bool(is_d4 and (strict_d4_unicorn or model_id in ("CG01_SWEEP_MSS_FVG", "UNICORN_MODEL")))

    conf_res = find_next_causal_entry_confirmation_v179312(
        {
            "checkpoint_index_1m": base_idx,
            "decision_timestamp_ms": base_ms,
            "direction": norm_dir,
            "model_id": model_id,
            "selected_sl": float(baseline_sl),
            "structural_invalidation_price": inv_px,
        },
        combined_m1,
        max_wait_bars=max_wait_bars,
        setup_invalidation=inv_px,
        require_mss_displacement=True,
        require_breaker_fvg_retest=req_fvg,
        strictly_after_base=True,
        model_aware=True,
        require_true_unicorn=req_unicorn,
    )
    if conf_res["status"] != "CONFIRMED" or conf_res["confirmed_checkpoint_index_1m"] is None:
        return _rej(
            str(conf_res.get("rejection_reason") or "WAIT_WINDOW_EXPIRED_UNCONFIRMED"),
            wait_b=int(conf_res.get("wait_bars") or 0),
            known_ms=int(conf_res.get("known_at_ms") or base_ms),
        )

    conf_idx = int(conf_res["confirmed_checkpoint_index_1m"])
    new_dec_ms = int(conf_res["new_decision_timestamp_ms"])
    wait_bars = int(conf_res["wait_bars"])
    conf_src_ids_holder = conf_res.get("confirmation_source_ids")
    conf_ev_holder = conf_res.get("confirmation_evidence")
    if wait_bars < 1 or new_dec_ms <= base_ms:
        return _rej("NON_DELAYED_CONFIRMATION_TIMESTAMP_FORBIDDEN", wait_b=wait_bars, known_ms=new_dec_ms)

    post_conf_forward_bars = fwd_list[wait_bars:]
    if len(post_conf_forward_bars) == 0:
        return _rej("NO_POST_CONFIRMATION_FORWARD_BARS_FOR_EXECUTION", wait_b=wait_bars, known_ms=new_dec_ms, new_ms=new_dec_ms)

    closed_m1_at_conf = combined_m1[: conf_idx + 1]
    closed_m5_at_conf = [b for b in closed_m5 if int(b.get("close_time") or (int(b.get("open_time") or 0) + 300_000)) <= new_dec_ms]
    closed_m15_at_conf = [b for b in closed_m15 if int(b.get("close_time") or (int(b.get("open_time") or 0) + 900_000)) <= new_dec_ms]
    closed_h1_at_conf = [b for b in closed_h1 if int(b.get("close_time") or (int(b.get("open_time") or 0) + 3_600_000)) <= new_dec_ms]
    closed_h4_at_conf = [b for b in closed_h4 if int(b.get("close_time") or (int(b.get("open_time") or 0) + 14_400_000)) <= new_dec_ms]

    htf_meta_at_conf: Optional[Dict[str, Any]] = None
    if is_d4:
        if not (closed_h1_at_conf and closed_h4_at_conf):
            return _rej(
                "REJECTED_MISSING_CAUSAL_HTF_BARS_AT_CONFIRMATION",
                wait_b=wait_bars,
                known_ms=new_dec_ms,
                new_ms=new_dec_ms,
            )
        htf_ds = {"4H": closed_h4_at_conf, "1H": closed_h1_at_conf, "15m": closed_m15_at_conf, "5m": closed_m5_at_conf}
        htf_cts = {
            "4H": [int(b.get("close_time") or (int(b.get("open_time") or 0) + 14_400_000)) for b in closed_h4_at_conf],
            "1H": [int(b.get("close_time") or (int(b.get("open_time") or 0) + 3_600_000)) for b in closed_h1_at_conf],
            "15m": [int(b.get("close_time") or (int(b.get("open_time") or 0) + 900_000)) for b in closed_m15_at_conf],
            "5m": [int(b.get("close_time") or (int(b.get("open_time") or 0) + 300_000)) for b in closed_m5_at_conf],
        }
        htf_ok, htf_meta = _check_causal_htf_alignment_at_ts_v179312(
            htf_ds, htf_cts, new_dec_ms, norm_dir, use_structural_evaluator=True
        )
        htf_meta_at_conf = dict(htf_meta or {})
        if not htf_ok:
            return _rej(
                str(htf_meta.get("rejection_reason") or "REJECTED_CAUSAL_H1_H4_CONFLICT"),
                wait_b=wait_bars,
                known_ms=new_dec_ms,
                new_ms=new_dec_ms,
            )

    conf_close_px = float(closed_m1_at_conf[-1]["close"])
    half_spread = 0.10
    eff_entry = round(conf_close_px + half_spread if norm_dir == "LONG" else conf_close_px - half_spread, 2)

    v1792_plan = plan_v17_9_2_structural_stop_and_target(
        v1792_mode="FULL_V1792",
        model_id=model_id,
        symbol="XAUUSDT",
        direction=norm_dir,
        entry_price=eff_entry,
        decision_timestamp_ms=new_dec_ms,
        baseline_sl=float(baseline_sl),
        baseline_target_price=float(baseline_tp),
        closed_m1=closed_m1_at_conf,
        closed_m5=closed_m5_at_conf,
        closed_m15=closed_m15_at_conf,
        closed_h1=closed_h1_at_conf,
        closed_h4=closed_h4_at_conf,
    )

    req_outer_sl = variant_id in (
        "D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL",
        "D3_REAL_DELAYED_ENTRY_PLUS_TARGET",
        "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
    )
    eff_sl = round(float(baseline_sl), 2)
    selected_anchor_id = feat.get("selected_anchor_id") or v1792_plan.frozen_anchor_id
    invalidation_px = round(float(v1792_plan.invalidation_price or eff_sl), 2)

    base_sl_still_valid = (
        (eff_sl < eff_entry - 0.15) if norm_dir == "LONG" else (eff_sl > eff_entry + 0.15)
    )
    if req_outer_sl or not base_sl_still_valid:
        if v1792_plan.proposed_sl is None or v1792_plan.selected_anchor is None:
            return _rej(
                "NO_VALID_CAUSAL_PROTECTED_SWING_ANCHOR_AT_CONFIRMATION",
                wait_b=wait_bars,
                known_ms=new_dec_ms,
                new_ms=new_dec_ms,
            )
        eff_sl = round(float(v1792_plan.proposed_sl), 2)
        selected_anchor_id = v1792_plan.frozen_anchor_id
        invalidation_px = round(float(v1792_plan.selected_anchor.anchor_price), 2)
        if abs(invalidation_px - eff_sl) < 1e-6:
            return _rej("CIRCULAR_PROTECTED_ANCHOR_EQUALS_SL", wait_b=wait_bars, known_ms=new_dec_ms, new_ms=new_dec_ms)

    req_target = variant_id in (
        "D3_REAL_DELAYED_ENTRY_PLUS_TARGET",
        "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
    )
    eff_tp = round(float(baseline_tp), 2)
    selected_target_id = feat.get("selected_target_id") or (
        v1792_plan.target_plan.selected_target_id if v1792_plan.target_plan else None
    )
    base_tp_geom_ok = (eff_sl < eff_entry < eff_tp) if norm_dir == "LONG" else (eff_tp < eff_entry < eff_sl)
    if base_tp_geom_ok and not req_target:
        sz_chk = recalculate_trade_risk_quantity_v179312(
            eff_entry, eff_sl, eff_tp, direction=norm_dir, decision_ts=new_dec_ms
        )
        if not sz_chk["can_execute"]:
            req_target = True
    else:
        req_target = True

    if req_target:
        t_cands = resolve_causal_target_candidates(
            direction=norm_dir,
            entry_price=eff_entry,
            decision_timestamp_ms=new_dec_ms,
            closed_m1=closed_m1_at_conf,
            closed_m5=closed_m5_at_conf,
            closed_m15=closed_m15_at_conf,
            closed_h1=closed_h1_at_conf,
            closed_h4=closed_h4_at_conf,
        )
        t_plan = select_viable_structural_target(
            direction=norm_dir,
            entry_price=eff_entry,
            proposed_sl=eff_sl,
            decision_timestamp_ms=new_dec_ms,
            target_candidates=t_cands,
            baseline_target_price=float(baseline_tp),
            min_net_rr=2.0,
            target_mode="V1792_CAUSAL_STRUCTURAL_VIABLE",
        )
        if t_plan.status != "VALID" or t_plan.target_price is None or len(t_plan.rejection_reasons) > 0:
            return _rej(
                "NO_VIABLE_UNSWEPT_LIQUIDITY_TARGET_NET_RR_2_AT_CONFIRMATION",
                wait_b=wait_bars,
                known_ms=new_dec_ms,
                new_ms=new_dec_ms,
            )
        eff_tp = round(float(t_plan.target_price), 2)
        selected_target_id = t_plan.selected_target_id

    sz_final = recalculate_trade_risk_quantity_v179312(
        eff_entry, eff_sl, eff_tp, direction=norm_dir, decision_ts=new_dec_ms
    )
    if not sz_final["can_execute"]:
        return _rej(
            str(sz_final.get("rejection_reason") or "RISK_SIZING_REJECTED_AT_CONFIRMATION"),
            wait_b=wait_bars,
            known_ms=new_dec_ms,
            new_ms=new_dec_ms,
        )

    sim_entry = simulate_v17931_trade_execution_net(
        variant=variant_id,
        episode_key=episode_key,
        setup_id=opportunity_id,
        model_id=model_id,
        direction=norm_dir,
        decision_ts=new_dec_ms,
        planned_entry=eff_entry,
        planned_sl=eff_sl,
        planned_tp=eff_tp,
        forward_bars=post_conf_forward_bars,
        fill_mode=fill_mode,
        dataset_sha=dataset_sha,
    )
    out_lbl = str(sim_entry.exit_type)
    if out_lbl in ("LOSS_TIE_CONSERVATIVE", "ADVERSE_GAP_OPEN_STOP_LOSS"):
        out_lbl = "LOSS"
    is_closed = bool(sim_entry.filled and out_lbl in ("WIN", "LOSS"))

    return {
        "variant_id": variant_id,
        "opportunity_id": opportunity_id,
        "episode_key": episode_key,
        "model_id": model_id,
        "direction": norm_dir,
        "base_decision_timestamp_ms": base_ms,
        "decision_timestamp_ms": new_dec_ms,
        "new_decision_timestamp_ms": new_dec_ms,
        "wait_bars": wait_bars,
        "known_at_ms": new_dec_ms,
        "entry_timing_contract": "GENUINE_DELAYED_POST_SIGNAL_CLOSED_BAR",
        "delayed_entry_executed": True,
        "confirmation_source_ids": conf_res.get("confirmation_source_ids"),
        "confirmation_evidence": conf_res.get("confirmation_evidence"),
        "htf_alignment_meta_at_confirmation": htf_meta_at_conf,
        "fill_mode": fill_mode,
        "status": "SCHEDULED" if sim_entry.filled else "NO_FILL_OR_SIZING_REJECT",
        "rejection_reason": sim_entry.no_fill_reason,
        "filled": bool(sim_entry.filled),
        "is_closed": is_closed,
        "outcome_label": out_lbl,
        "exit_type": str(sim_entry.exit_type),
        "baseline_entry": round(float(baseline_entry), 2),
        "baseline_sl": round(float(baseline_sl), 2),
        "baseline_tp": round(float(baseline_tp), 2),
        "planned_entry": eff_entry,
        "planned_sl": eff_sl,
        "planned_tp": eff_tp,
        "structural_invalidation_price": invalidation_px,
        "selected_anchor_id": selected_anchor_id,
        "selected_target_id": selected_target_id,
        "entry_fill_price": sim_entry.entry_fill_price,
        "exit_fill_price": sim_entry.exit_fill_price,
        "entry_fill_ts": sim_entry.entry_fill_ts,
        "trigger_bar_ts": sim_entry.trigger_bar_ts,
        "exit_fill_ts": sim_entry.exit_fill_ts,
        "bars_held": int(sim_entry.bars_held),
        "quantity": float(sim_entry.quantity),
        "planned_net_risk_usd": float(sim_entry.planned_net_risk_usd),
        "net_rr": float(sz_final["net_rr"]),
        "gross_rr": float(sz_final["gross_rr"]),
        "entry_fee_usd": float(sim_entry.entry_fee),
        "exit_fee_usd": float(sim_entry.exit_fee),
        "total_fee_usd": round(float(sim_entry.entry_fee) + float(sim_entry.exit_fee), 4),
        "realized_gross_pnl_usd": float(sim_entry.realized_gross_pnl_usd),
        "realized_net_pnl_usd": float(sim_entry.realized_net_pnl_usd),
        "realized_r_net": float(sim_entry.realized_r_net),
        "same_bar_sl_tp_tie": bool(sim_entry.same_bar_sl_tp_tie),
    }


def evaluate_shared_delayed_entry_adapter_v17931213(
    *,
    execution_context_mode: str = "REPLAY_BACKTEST",
    activation_flags: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Phase A15 Shared Production-Safe Adapter:
    Invokes the exact same pure delayed-entry evaluation function across:
      - REPLAY_BACKTEST
      - MARKET_SHADOW
      - PRODUCTION_ORCHESTRATOR_DRY_RUN (default_state = OFF)
    Hard-blocks if any live/paper/auto flag is enabled.
    """
    flags = dict(activation_flags or {})
    for forbidden_key in ("LIVE", "AUTO_LIVE", "PAPER", "AUTO_PAPER", "enabled_in_production"):
        if bool(flags.get(forbidden_key, False)):
            raise PermissionError(f"LIVE_OR_PAPER_ACTIVATION_FORBIDDEN_IN_V17931213:{forbidden_key}")
    allowed_modes = ("REPLAY_BACKTEST", "MARKET_SHADOW", "PRODUCTION_ORCHESTRATOR_DRY_RUN")
    if execution_context_mode not in allowed_modes:
        raise ValueError(f"UNSUPPORTED_EXECUTION_CONTEXT_MODE:{execution_context_mode}")
    res = evaluate_r3_counterfactual_stop_and_target_v17931212(**kwargs)
    return {
        **res,
        "adapter_execution_context_mode": execution_context_mode,
        "adapter_default_activation_state": "OFF",
        "adapter_live_orders_emitted": 0,
    }


def evaluate_r3_counterfactual_stop_and_target_v17931212(
    *,
    variant_id: str,
    opportunity_id: str,
    episode_key: str,
    model_id: str,
    direction: str,
    decision_timestamp_ms: int,
    baseline_entry: float,
    baseline_sl: float,
    baseline_tp: float,
    closed_m1: Sequence[Any],
    closed_m5: Sequence[Any] = (),
    closed_m15: Sequence[Any] = (),
    closed_h1: Sequence[Any] = (),
    closed_h4: Sequence[Any] = (),
    forward_bars: Sequence[Any] = (),
    pre_entry_features: Optional[Mapping[str, Any]] = None,
    fill_mode: str = "NEXT_BAR_OPEN_BID_ASK",
    dataset_sha: str = "",
    require_strict_d4_unicorn_all_models: bool = False,
) -> Dict[str, Any]:
    """
    V17.9.3.1.2.12 R3 & V17.9.3.1.2.13 Phase A:
    - Historical .12 same-cutoff controls:
        * A0_STRICT_BASELINE: exact baseline entry, SL, TP, and sizing.
        * A1_ENTRY_CONFIRMATION_RETEST_ONLY: SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY control at base cutoff.
        * A2_PROTECTED_SL_NOISE_BUFFER_ONLY: baseline entry/TP + outer protected swing SL + noise buffer.
        * A3_SOURCE_BACKED_LIQUIDITY_TP_ONLY: baseline entry/SL + nearest unswept liquidity TP (RR >= 2.0).
        * A4_COMBINED_CAUSAL_ENTRY_SL_TP: combines A1 same-cutoff filter + A2 + A3.
    - Genuine .13 post-signal delayed-entry variants (strictly k > base_idx):
        * A1B_GENUINE_DELAYED_CONFIRMATION_ENTRY / D1_REAL_DELAYED_ENTRY
        * D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL
        * D3_REAL_DELAYED_ENTRY_PLUS_TARGET
        * D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE
    """
    if variant_id in TRUE_DELAYED_ENTRY_VARIANTS_V17931213:
        return evaluate_r3_true_delayed_counterfactual_v17931213(
            variant_id=variant_id,
            opportunity_id=opportunity_id,
            episode_key=episode_key,
            model_id=model_id,
            direction=direction,
            decision_timestamp_ms=decision_timestamp_ms,
            baseline_entry=baseline_entry,
            baseline_sl=baseline_sl,
            baseline_tp=baseline_tp,
            closed_m1=closed_m1,
            closed_m5=closed_m5,
            closed_m15=closed_m15,
            closed_h1=closed_h1,
            closed_h4=closed_h4,
            forward_bars=forward_bars,
            pre_entry_features=pre_entry_features,
            fill_mode=fill_mode,
            dataset_sha=dataset_sha,
            require_strict_d4_unicorn_all_models=require_strict_d4_unicorn_all_models,
        )

    norm_dir = str(direction or "LONG").strip().upper()
    dec_ms = int(decision_timestamp_ms)
    feat = dict(pre_entry_features or {})

    # 1. Check Entry Confirmation gate for A1 and A4 (explicitly tagged SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY)
    if variant_id in ("A1_ENTRY_CONFIRMATION_RETEST_ONLY", "A4_COMBINED_CAUSAL_ENTRY_SL_TP"):
        mss_ok = feat.get("mss_confirmed") is True
        disp_ok = feat.get("displacement_valid") is True
        fvg_retest_ok = feat.get("fvg_retest_valid") is True and feat.get("fvg_unmitigated") is True
        sweep_rec_ok = feat.get("sweep_confirmed") is True and feat.get("sweep_reclaimed") is True
        entry_confirmed = bool((mss_ok and disp_ok) or fvg_retest_ok or sweep_rec_ok)
        if not entry_confirmed:
            return {
                "variant_id": variant_id,
                "opportunity_id": opportunity_id,
                "episode_key": episode_key,
                "model_id": model_id,
                "direction": norm_dir,
                "base_decision_timestamp_ms": dec_ms,
                "decision_timestamp_ms": dec_ms,
                "new_decision_timestamp_ms": None,
                "wait_bars": 0,
                "entry_timing_contract": "SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY",
                "delayed_entry_executed": False,
                "fill_mode": fill_mode,
                "status": "REJECTED_PRE_ENTRY",
                "rejection_reason": "ENTRY_CONFIRMATION_OR_RETEST_NOT_PROVEN_AT_CUTOFF",
                "filled": False,
                "is_closed": False,
                "outcome_label": "REJECTED_PRE_ENTRY",
                "planned_entry": round(float(baseline_entry), 2),
                "planned_sl": round(float(baseline_sl), 2),
                "planned_tp": round(float(baseline_tp), 2),
                "quantity": 0.0,
                "planned_net_risk_usd": 0.0,
                "realized_gross_pnl_usd": 0.0,
                "realized_net_pnl_usd": 0.0,
                "total_fee_usd": 0.0,
                "realized_r_net": 0.0,
            }

    v1792_plan = plan_v17_9_2_structural_stop_and_target(
        v1792_mode="FULL_V1792",
        model_id=model_id,
        symbol="XAUUSDT",
        direction=norm_dir,
        entry_price=float(baseline_entry),
        decision_timestamp_ms=dec_ms,
        baseline_sl=float(baseline_sl),
        baseline_target_price=float(baseline_tp),
        closed_m1=closed_m1,
        closed_m5=closed_m5,
        closed_m15=closed_m15,
    )

    eff_entry = round(float(baseline_entry), 2)
    eff_sl = round(float(baseline_sl), 2)
    eff_tp = round(float(baseline_tp), 2)
    selected_anchor_id = feat.get("selected_anchor_id") or v1792_plan.frozen_anchor_id
    selected_target_id = feat.get("selected_target_id") or (
        v1792_plan.target_plan.selected_target_id if v1792_plan.target_plan else None
    )

    # 2. Apply Protected SL + Noise Buffer for A2 and A4
    if variant_id in ("A2_PROTECTED_SL_NOISE_BUFFER_ONLY", "A4_COMBINED_CAUSAL_ENTRY_SL_TP"):
        if v1792_plan.proposed_sl is None or v1792_plan.selected_anchor is None:
            return {
                "variant_id": variant_id,
                "opportunity_id": opportunity_id,
                "episode_key": episode_key,
                "model_id": model_id,
                "direction": norm_dir,
                "decision_timestamp_ms": dec_ms,
                "fill_mode": fill_mode,
                "status": "REJECTED_PRE_ENTRY",
                "rejection_reason": "NO_VALID_CAUSAL_PROTECTED_SWING_ANCHOR",
                "filled": False,
                "is_closed": False,
                "outcome_label": "REJECTED_PRE_ENTRY",
                "planned_entry": eff_entry,
                "planned_sl": eff_sl,
                "planned_tp": eff_tp,
                "quantity": 0.0,
                "planned_net_risk_usd": 0.0,
                "realized_gross_pnl_usd": 0.0,
                "realized_net_pnl_usd": 0.0,
                "total_fee_usd": 0.0,
                "realized_r_net": 0.0,
            }
        eff_sl = round(float(v1792_plan.proposed_sl), 2)
        selected_anchor_id = v1792_plan.frozen_anchor_id

    # 3. Apply Source-Backed Liquidity TP for A3 and A4
    if variant_id in ("A3_SOURCE_BACKED_LIQUIDITY_TP_ONLY", "A4_COMBINED_CAUSAL_ENTRY_SL_TP"):
        t_cands = resolve_causal_target_candidates(
            direction=norm_dir,
            entry_price=eff_entry,
            decision_timestamp_ms=dec_ms,
            closed_m1=closed_m1,
            closed_m5=closed_m5,
            closed_m15=closed_m15,
        )
        t_plan = select_viable_structural_target(
            direction=norm_dir,
            entry_price=eff_entry,
            proposed_sl=eff_sl,
            decision_timestamp_ms=dec_ms,
            target_candidates=t_cands,
            baseline_target_price=float(baseline_tp),
            min_net_rr=2.0,
            target_mode="V1792_CAUSAL_STRUCTURAL_VIABLE",
        )
        if t_plan.status != "VALID" or t_plan.target_price is None or len(t_plan.rejection_reasons) > 0:
            return {
                "variant_id": variant_id,
                "opportunity_id": opportunity_id,
                "episode_key": episode_key,
                "model_id": model_id,
                "direction": norm_dir,
                "decision_timestamp_ms": dec_ms,
                "fill_mode": fill_mode,
                "status": "REJECTED_PRE_ENTRY",
                "rejection_reason": "NO_VIABLE_UNSWEPT_LIQUIDITY_TARGET_NET_RR_2",
                "filled": False,
                "is_closed": False,
                "outcome_label": "REJECTED_PRE_ENTRY",
                "planned_entry": eff_entry,
                "planned_sl": eff_sl,
                "planned_tp": eff_tp,
                "quantity": 0.0,
                "planned_net_risk_usd": 0.0,
                "realized_gross_pnl_usd": 0.0,
                "realized_net_pnl_usd": 0.0,
                "total_fee_usd": 0.0,
                "realized_r_net": 0.0,
            }
        eff_tp = round(float(t_plan.target_price), 2)
        selected_target_id = t_plan.selected_target_id

    sim_entry = simulate_v17931_trade_execution_net(
        variant=variant_id,
        episode_key=episode_key,
        setup_id=opportunity_id,
        model_id=model_id,
        direction=norm_dir,
        decision_ts=dec_ms,
        planned_entry=eff_entry,
        planned_sl=eff_sl,
        planned_tp=eff_tp,
        forward_bars=forward_bars,
        fill_mode=fill_mode,
        dataset_sha=dataset_sha,
    )

    out_lbl = str(sim_entry.exit_type)
    if out_lbl in ("LOSS_TIE_CONSERVATIVE", "ADVERSE_GAP_OPEN_STOP_LOSS"):
        out_lbl = "LOSS"
    is_closed = bool(sim_entry.filled and out_lbl in ("WIN", "LOSS"))

    return {
        "variant_id": variant_id,
        "opportunity_id": opportunity_id,
        "episode_key": episode_key,
        "model_id": model_id,
        "direction": norm_dir,
        "base_decision_timestamp_ms": dec_ms,
        "decision_timestamp_ms": dec_ms,
        "new_decision_timestamp_ms": dec_ms,
        "wait_bars": 0,
        "entry_timing_contract": (
            "SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY"
            if variant_id in ("A1_ENTRY_CONFIRMATION_RETEST_ONLY", "A4_COMBINED_CAUSAL_ENTRY_SL_TP")
            else "IMMEDIATE_BASE_CUTOFF"
        ),
        "delayed_entry_executed": False,
        "fill_mode": fill_mode,
        "status": "SCHEDULED" if sim_entry.filled else "NO_FILL_OR_SIZING_REJECT",
        "rejection_reason": sim_entry.no_fill_reason,
        "filled": bool(sim_entry.filled),
        "is_closed": is_closed,
        "outcome_label": out_lbl,
        "exit_type": str(sim_entry.exit_type),
        "planned_entry": eff_entry,
        "planned_sl": eff_sl,
        "planned_tp": eff_tp,
        "selected_anchor_id": selected_anchor_id,
        "selected_target_id": selected_target_id,
        "entry_fill_price": sim_entry.entry_fill_price,
        "exit_fill_price": sim_entry.exit_fill_price,
        "entry_fill_ts": sim_entry.entry_fill_ts,
        "trigger_bar_ts": sim_entry.trigger_bar_ts,
        "exit_fill_ts": sim_entry.exit_fill_ts,
        "bars_held": int(sim_entry.bars_held),
        "quantity": float(sim_entry.quantity),
        "planned_net_risk_usd": float(sim_entry.planned_net_risk_usd),
        "entry_fee_usd": float(sim_entry.entry_fee),
        "exit_fee_usd": float(sim_entry.exit_fee),
        "total_fee_usd": round(float(sim_entry.entry_fee) + float(sim_entry.exit_fee), 4),
        "realized_gross_pnl_usd": float(sim_entry.realized_gross_pnl_usd),
        "realized_net_pnl_usd": float(sim_entry.realized_net_pnl_usd),
        "realized_r_net": float(sim_entry.realized_r_net),
        "same_bar_sl_tp_tie": bool(sim_entry.same_bar_sl_tp_tie),
    }


