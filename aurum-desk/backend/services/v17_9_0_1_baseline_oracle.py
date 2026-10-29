"""
Aurum Desk V17.9.0.1 — Independent Pre-Refactor Baseline Oracle & Characterization Harness.

This module recovers and executes the pre-V17.9.0 production decision logic (from baseline commit
`c356d6551e13cd6af61cea5c3ab1f4aae1548af2`, V17.8.0.4) WITHOUT importing or calling
`SharedStrategyEvaluator`.

Used for:
  1. Phase B: Independent Pre-Refactor Baseline Recovery (`BASELINE_MANIFEST.json`,
     `BASELINE_DECISIONS.jsonl`, `BASELINE_CHARACTERIZATION.md`, `BASELINE_PROVENANCE.md`)
  2. Non-self-referential baseline parity comparison against `SharedStrategyEvaluator`.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import smc_engine
from domain_calculator import CostAssumptions, calculate_risk_reward
from services.cg01_engine import CG01Config, evaluate_cg01_candidate
from services.liquidity_scenario_service import identify_confirmed_swings
from services.quote_validator import CanonicalQuote
from services.scalp_m1_model_service import (
    ScalpM1ModelService,
    resolve_executable_target,
)
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    WatchedZone,
    get_scalp_config,
)
from services.smc_liquidity_service import build_liquidity_clusters, build_liquidity_map
from services.stop_placement_research_service import build_stop_comparison_plan
from services.stop_placement_service import (
    compute_stop_atr,
    compute_stop_buffer,
    plan_structural_stop,
    resolve_stop_anchor,
)
from services.stop_placement_types import (
    StopPlacementMode,
    StopPlacementPolicy,
    StopStatus,
)
from services.unicorn_evidence_service import detect_breaker_blocks, detect_unicorn_evidence


PRE_REFACTOR_BASELINE_COMMIT_SHA = "c356d6551e13cd6af61cea5c3ab1f4aae1548af2"
PRE_REFACTOR_ORACLE_VERSION = "V17.8.0.4_INDEPENDENT_ORACLE"


def _oracle_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bar_open_ms(bar: Any) -> int:
    if isinstance(bar, dict):
        return int(bar.get("open_time_ms") or bar.get("open_time") or bar.get("timestamp") or 0)
    return int(
        getattr(bar, "open_time_ms", None)
        or getattr(bar, "open_time", None)
        or getattr(bar, "timestamp", 0)
        or 0
    )


def _bar_close_ms(bar: Any, tf_ms: int) -> int:
    if isinstance(bar, dict):
        c = bar.get("close_time_ms") or bar.get("close_time")
        if c is not None:
            return int(c)
    else:
        c = getattr(bar, "close_time_ms", None) or getattr(bar, "close_time", None)
        if c is not None:
            return int(c)
    o = _bar_open_ms(bar)
    return o + tf_ms if o > 0 else 0


class IndependentPreRefactorBaselineOracle:
    """
    Executes the pre-V17.9.0 strategy evaluation pipeline directly against the underlying
    V17.8.0.4 domain services (`ScalpM1ModelService`, `cg01_engine`, `unicorn_evidence_service`,
    `stop_placement_service`, `domain_calculator`) without touching `SharedStrategyEvaluator`.
    """

    @staticmethod
    def _extract_pre_refactor_setup(
        *,
        symbol: str,
        model_id: str,
        direction: str,
        closed_1m: Sequence[Any],
        sh_1m: List[Dict[str, Any]],
        sl_1m: List[Dict[str, Any]],
        cfg: M1ScalpConfig,
        cutoff_ms: int,
    ) -> Dict[str, Any]:
        if len(closed_1m) < 10 or not sh_1m or not sl_1m:
            return {"has_setup": False, "reason_code": "WAITING_M1_STRUCTURE"}

        fvgs = smc_engine.detect_fvgs(list(closed_1m), timeframe="1M")
        valid_fvgs = [
            f
            for f in fvgs
            if isinstance(f, dict)
            and int(f.get("confirmed_at") or f.get("timestamp") or 0) <= cutoff_ms
            and float(f.get("bottom", 0.0)) < float(f.get("top", 0.0))
            and str(f.get("state", "confirmed")).lower() in ("confirmed", "partially_mitigated")
        ]

        if model_id in (
            ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
            ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
            "UNICORN_BREAKER_FVG",
        ):
            target_fvg_type = "BULLISH_FVG" if direction == "LONG" else "BEARISH_FVG"
            dir_fvgs = [f for f in valid_fvgs if str(f.get("type", "")).upper() == target_fvg_type]
            if not dir_fvgs:
                return {"has_setup": False, "reason_code": "NO_VALID_FVG_SETUP"}
            sel_fvg = dir_fvgs[-1]
            f_bot = float(sel_fvg["bottom"])
            f_top = float(sel_fvg["top"])
            last_2_open = _bar_open_ms(closed_1m[-2])
            f_ts = int(sel_fvg.get("timestamp") or sel_fvg.get("middle_bar_ms") or last_2_open)
            f_conf = int(sel_fvg.get("confirmed_at") or (f_ts + 60_000))
            f_id = str(sel_fvg.get("id") or f"fvg_{direction}_{f_ts}")

            ref_swings = sh_1m if direction == "LONG" else sl_1m
            orig_swings = sl_1m if direction == "LONG" else sh_1m
            swing_prefix = "sh" if direction == "LONG" else "sl"

            causal_breakers = detect_breaker_blocks(
                list(closed_1m),
                symbol=symbol,
                direction=direction,
                timeframe="1M",
                as_of_ms=cutoff_ms,
                confirmed_swings=(sh_1m, sl_1m),
            )
            overlapping_breakers = [
                b
                for b in causal_breakers
                if b.confirmed_at_ms <= cutoff_ms
                and abs(int(b.break_bar_open_ms) - f_ts) <= 5 * 60_000
                and min(float(b.high), f_top) > max(float(b.low), f_bot)
            ]
            matched_breaker = overlapping_breakers[-1] if overlapping_breakers else None

            prior_refs = [s for s in ref_swings if int(s.get("occurred_at_ms", 0)) <= f_ts]
            prior_origs = [s for s in orig_swings if int(s.get("occurred_at_ms", 0)) <= f_ts]
            ref_swing = prior_refs[-1] if prior_refs else (ref_swings[-1] if ref_swings else None)
            orig_swing = prior_origs[-1] if prior_origs else (orig_swings[-1] if orig_swings else None)
            if matched_breaker is not None and matched_breaker.broken_swing_id:
                for s in ref_swings:
                    s_id = str(s.get("id") or f"{swing_prefix}_{s.get('occurred_at_ms')}")
                    if s_id == matched_breaker.broken_swing_id:
                        ref_swing = s
                        break
            if ref_swing is None or orig_swing is None:
                return {"has_setup": False, "reason_code": "WAITING_M1_STRUCTURE"}

            broken_swing_id_str = (
                str(matched_breaker.broken_swing_id)
                if (matched_breaker is not None and matched_breaker.broken_swing_id)
                else str(ref_swing.get("id") or f"{swing_prefix}_{ref_swing.get('occurred_at_ms')}")
            )
            eff_break_open_ms = int(matched_breaker.break_bar_open_ms) if matched_breaker is not None else f_ts
            eff_break_known_ms = int(matched_breaker.confirmed_at_ms) if matched_breaker is not None else f_conf

            legacy_sl = (
                round(f_bot - cfg.sl_buffer_ticks * cfg.tick_size, 2)
                if direction == "LONG"
                else round(f_top + cfg.sl_buffer_ticks * cfg.tick_size, 2)
            )
            sel_fvg_desc = {
                "id": f_id,
                "type": target_fvg_type,
                "direction": direction,
                "timeframe": "1M",
                "state": "confirmed",
                "top": f_top,
                "bottom": f_bot,
                "middle_bar_ms": f_ts,
                "timestamp": f_ts,
                "confirmed_at": f_conf,
                "break_open_ms": eff_break_open_ms,
                "broken_swing_id": broken_swing_id_str,
            }
            sel_break_desc = {
                "break_id": f"brk_{direction}_{eff_break_open_ms}",
                "break_open_ms": eff_break_open_ms,
                "broken_at_ms": eff_break_open_ms,
                "known_at_ms": eff_break_known_ms,
                "broken_swing_id": broken_swing_id_str,
                "origin_pivot_id": str(orig_swing.get("id", f"orig_{orig_swing.get('occurred_at_ms')}")),
                "origin_price": float(orig_swing["price"]),
                "origin_occurred_at_ms": int(orig_swing["occurred_at_ms"]),
                "episode_start_ms": min(int(orig_swing["occurred_at_ms"]), int(ref_swing.get("occurred_at_ms", f_ts))),
            }
            sel_sweep = None
            if model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value:
                sel_sweep = {
                    "id": f"sweep_cg01_{orig_swing.get('occurred_at_ms')}",
                    "price": float(orig_swing["price"]),
                    "occurred_at_ms": int(orig_swing["occurred_at_ms"]),
                    "known_at_ms": int(orig_swing.get("known_at_ms") or orig_swing["occurred_at_ms"]),
                }
            return {
                "has_setup": True,
                "direction": direction,
                "legacy_sl": legacy_sl,
                "episode_id": f"ep_{model_id}_{direction}_{f_ts}",
                "selected_fvg": sel_fvg_desc,
                "selected_break": sel_break_desc,
                "selected_sweep": sel_sweep,
                "selected_reference": ref_swing,
                "entry_zone_low": round(f_bot, 2),
                "entry_zone_high": round(f_top, 2),
            }

        if model_id == ModelIdentifier.SMC_M1_CONTINUATION.value:
            last_sh = sh_1m[-1]
            last_sl = sl_1m[-1]
            last_bar = closed_1m[-1]
            last_close = float(last_bar.get("close") if isinstance(last_bar, dict) else getattr(last_bar, "close", 0.0))
            last_open_ms = _bar_open_ms(last_bar)
            last_close_ms = _bar_close_ms(last_bar, 60_000)
            if direction == "LONG" and last_close > float(last_sh["price"]):
                legacy_sl = round(float(last_sl["price"]) - cfg.sl_buffer_ticks * cfg.tick_size, 2)
                orig, ref = last_sl, last_sh
            elif direction == "SHORT" and last_close < float(last_sl["price"]):
                legacy_sl = round(float(last_sh["price"]) + cfg.sl_buffer_ticks * cfg.tick_size, 2)
                orig, ref = last_sh, last_sl
            else:
                return {"has_setup": False, "reason_code": "WAITING_M1_BOS"}
            return {
                "has_setup": True,
                "direction": direction,
                "legacy_sl": legacy_sl,
                "episode_id": f"ep_cont_{direction}_{last_open_ms}",
                "selected_fvg": None,
                "selected_break": {
                    "break_id": f"bos_{direction}_{last_open_ms}",
                    "break_open_ms": last_open_ms,
                    "broken_at_ms": last_open_ms,
                    "known_at_ms": last_close_ms,
                    "broken_swing_id": str(ref.get("id")),
                    "origin_pivot_id": str(orig.get("id")),
                    "origin_price": float(orig["price"]),
                    "origin_occurred_at_ms": int(orig["occurred_at_ms"]),
                    "episode_start_ms": int(orig["occurred_at_ms"]),
                },
                "selected_sweep": {
                    "id": f"anchor_cont_{orig.get('occurred_at_ms')}",
                    "price": float(orig["price"]),
                    "occurred_at_ms": int(orig["occurred_at_ms"]),
                    "known_at_ms": int(orig.get("known_at_ms") or orig["occurred_at_ms"]),
                },
                "selected_reference": ref,
                "entry_zone_low": None,
                "entry_zone_high": None,
            }

        last_sh = sh_1m[-1]
        last_sl = sl_1m[-1]
        recent_bars = closed_1m[-5:]
        lows = [float(b.get("low") if isinstance(b, dict) else getattr(b, "low", 0.0)) for b in recent_bars]
        highs = [float(b.get("high") if isinstance(b, dict) else getattr(b, "high", 0.0)) for b in recent_bars]
        last_close = float(closed_1m[-1].get("close") if isinstance(closed_1m[-1], dict) else getattr(closed_1m[-1], "close", 0.0))
        swept_low = min(lows)
        swept_high = max(highs)
        if direction == "LONG" and swept_low <= float(last_sl["price"]) and last_close > float(last_sl["price"]):
            legacy_sl = round(swept_low - cfg.sl_buffer_ticks * cfg.tick_size, 2)
            sweep_px = swept_low
            sweep_ts = min(_bar_open_ms(b) for b, l_val in zip(recent_bars, lows) if l_val == swept_low)
        elif direction == "SHORT" and swept_high >= float(last_sh["price"]) and last_close < float(last_sh["price"]):
            legacy_sl = round(swept_high + cfg.sl_buffer_ticks * cfg.tick_size, 2)
            sweep_px = swept_high
            sweep_ts = min(_bar_open_ms(b) for b, h_val in zip(recent_bars, highs) if h_val == swept_high)
        else:
            return {"has_setup": False, "reason_code": "WAITING_SWEEP_RECLAIM"}

        return {
            "has_setup": True,
            "direction": direction,
            "legacy_sl": legacy_sl,
            "episode_id": f"ep_{model_id}_{direction}_{sweep_ts}",
            "selected_fvg": None,
            "selected_break": None,
            "selected_sweep": {
                "id": f"sweep_{model_id}_{sweep_ts}",
                "price": sweep_px,
                "occurred_at_ms": sweep_ts,
                "known_at_ms": sweep_ts + 60_000,
            },
            "selected_reference": last_sl if direction == "LONG" else last_sh,
            "entry_zone_low": None,
            "entry_zone_high": None,
        }

    @classmethod
    def evaluate_baseline_dict(
        cls,
        context: Any,
        model_id: str,
        direction: str,
        *,
        target_resolver: Optional[Callable[..., Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes the independent pre-refactor baseline evaluation and returns a raw dict
        of all 20+ parity fields and fingerprints.
        """
        now_ms = int(context.decision_timestamp_ms)
        resolve_target_fn = target_resolver or resolve_executable_target
        cfg = get_scalp_config()
        policy_dict = context.get_policy_dict()
        risk_dict = context.get_risk_dict()
        cost_dict = context.get_cost_dict()

        def _compute_fps(
            setup_validity: str,
            entry_reference: Optional[float],
            selected_fvg_id: Optional[str],
            selected_break_id: Optional[str],
            selected_sweep_id: Optional[str],
            setup_episode_id: Optional[str],
            unicorn_validity: str,
            selected_anchor_id: Optional[str],
            structural_invalidation_price: Optional[float],
            selected_sl: Optional[float],
            take_profit: Optional[float],
            hypothetical_quantity: Optional[float],
            net_rr: Optional[float],
            decision_status: str,
            rejection_reason_codes: Sequence[str],
        ) -> Tuple[str, str, str]:
            ev_fp = _oracle_hash({
                "source_fingerprint": context.source_fingerprint,
                "model_id": model_id,
                "direction": direction,
                "selected_fvg_id": selected_fvg_id,
                "selected_break_id": selected_break_id,
                "selected_sweep_id": selected_sweep_id,
                "setup_episode_id": setup_episode_id,
                "unicorn_validity": unicorn_validity,
            })
            pol_fp = _oracle_hash({
                "policy": context.policy_config_snapshot,
                "risk": context.risk_config_snapshot,
                "cost": context.cost_config_snapshot,
            })
            dec_fp = _oracle_hash({
                "model_id": model_id,
                "direction": direction,
                "setup_validity": setup_validity,
                "entry_reference": entry_reference,
                "selected_fvg_id": selected_fvg_id,
                "selected_break_id": selected_break_id,
                "selected_sweep_id": selected_sweep_id,
                "setup_episode_id": setup_episode_id,
                "unicorn_validity": unicorn_validity,
                "selected_anchor_id": selected_anchor_id,
                "structural_invalidation_price": structural_invalidation_price,
                "selected_sl": selected_sl,
                "take_profit": take_profit,
                "atr_m1": context.atr_m1,
                "atr_m5": context.atr_m5,
                "spread": context.spread,
                "hypothetical_quantity": hypothetical_quantity,
                "net_rr": net_rr,
                "decision_status": decision_status,
                "rejection_reason_codes": list(rejection_reason_codes),
            })
            return ev_fp, pol_fp, dec_fp

        if not context.is_model_enabled(model_id):
            reasons = ("MODEL_DISABLED",)
            ev_fp, pol_fp, dec_fp = _compute_fps(
                "INVALID", None, None, None, None, None, "NOT_APPLICABLE",
                None, None, None, None, None, None, "MODEL_DISABLED", reasons,
            )
            return {
                "model_id": model_id,
                "symbol": context.symbol,
                "decision_timestamp_ms": now_ms,
                "direction": direction,
                "setup_validity": "INVALID",
                "entry_reference": None,
                "entry_zone_low": None,
                "entry_zone_high": None,
                "selected_fvg_id": None,
                "selected_break_id": None,
                "selected_sweep_id": None,
                "setup_episode_id": None,
                "unicorn_validity": "NOT_APPLICABLE",
                "unicorn_failure_stage": "MODEL_DISABLED",
                "selected_anchor_id": None,
                "anchor_kind": None,
                "structural_invalidation_price": None,
                "buffer_value": None,
                "selected_sl": None,
                "legacy_sl": None,
                "take_profit": None,
                "selected_target_id": None,
                "atr_m1": context.atr_m1,
                "atr_m5": context.atr_m5,
                "spread": context.spread,
                "hypothetical_quantity": None,
                "stop_distance_price": None,
                "target_distance_price": None,
                "gross_rr": None,
                "net_rr": None,
                "decision_status": "MODEL_DISABLED",
                "rejection_reason_codes": reasons,
                "evidence_fingerprint": ev_fp,
                "policy_fingerprint": pol_fp,
                "decision_fingerprint": dec_fp,
                "adapter_origin": "BASELINE_DIRECT_PRODUCTION",
                "raw_candidate_reason_code": "MODEL_DISABLED",
                "oracle_commit_sha": PRE_REFACTOR_BASELINE_COMMIT_SHA,
            }

        min_h1_bars = int(policy_dict.get("min_h1_bars", 0) or 0)
        min_h4_bars = int(policy_dict.get("min_h4_bars", 0) or 0)
        require_htf = bool(policy_dict.get("require_htf_history", False))
        h1_bars = getattr(context, "closed_h1_candles", ())
        h4_bars = getattr(context, "closed_h4_candles", ())
        htf_insufficient = (
            (require_htf and (len(h1_bars) < max(1, min_h1_bars) or len(h4_bars) < max(1, min_h4_bars)))
            or (min_h1_bars > 0 and len(h1_bars) < min_h1_bars)
            or (min_h4_bars > 0 and len(h4_bars) < min_h4_bars)
        )

        if (
            len(context.closed_m1_candles) < 15
            or len(context.closed_m5_candles) < 3
            or context.spread is None
            or not context.causal_valid
            or htf_insufficient
        ):
            reasons_list = list(context.causal_rejection_reasons)
            if htf_insufficient and "INSUFFICIENT_HTF_HISTORY" not in reasons_list:
                reasons_list.append("INSUFFICIENT_HTF_HISTORY")
            if not reasons_list:
                reasons_list.append("INSUFFICIENT_CLOSED_CANDLES")
            reasons_tuple = tuple(reasons_list)
            ev_fp, pol_fp, dec_fp = _compute_fps(
                "INVALID", None, None, None, None, None, "INVALID",
                None, None, None, None, None, None, "MISSING_MARKET_DATA", reasons_tuple,
            )
            return {
                "model_id": model_id,
                "symbol": context.symbol,
                "decision_timestamp_ms": now_ms,
                "direction": direction,
                "setup_validity": "INVALID",
                "entry_reference": None,
                "entry_zone_low": None,
                "entry_zone_high": None,
                "selected_fvg_id": None,
                "selected_break_id": None,
                "selected_sweep_id": None,
                "setup_episode_id": None,
                "unicorn_validity": "INVALID",
                "unicorn_failure_stage": "MISSING_MARKET_DATA",
                "selected_anchor_id": None,
                "anchor_kind": None,
                "structural_invalidation_price": None,
                "buffer_value": None,
                "selected_sl": None,
                "legacy_sl": None,
                "take_profit": None,
                "selected_target_id": None,
                "atr_m1": context.atr_m1,
                "atr_m5": context.atr_m5,
                "spread": context.spread,
                "hypothetical_quantity": None,
                "stop_distance_price": None,
                "target_distance_price": None,
                "gross_rr": None,
                "net_rr": None,
                "decision_status": "MISSING_MARKET_DATA",
                "rejection_reason_codes": reasons_tuple,
                "evidence_fingerprint": ev_fp,
                "policy_fingerprint": pol_fp,
                "decision_fingerprint": dec_fp,
                "adapter_origin": "BASELINE_DIRECT_PRODUCTION",
                "raw_candidate_reason_code": reasons_tuple[0],
                "oracle_commit_sha": PRE_REFACTOR_BASELINE_COMMIT_SHA,
            }

        closed_1m = list(context.closed_m1_candles)
        bid = float(context.bid or 0.0)
        ask = float(context.ask or 0.0)
        entry_ref = round(ask if direction == "LONG" else bid, 2)
        min_net_rr = float(risk_dict.get("min_net_rr", cfg.min_net_rr))

        # Check explicit HTF structure conflict if configured
        ctx_htf_bias = str(getattr(context, "htf_bias", None) or policy_dict.get("htf_bias") or "UNKNOWN")
        if bool(policy_dict.get("enforce_htf_bias_alignment", False)) and ctx_htf_bias in ("BULLISH", "BEARISH"):
            expected_dir = "LONG" if ctx_htf_bias == "BULLISH" else "SHORT"
            if direction != expected_dir or str(policy_dict.get("h1_alignment", "")) in ("COUNTER", "CONFLICT"):
                reasons_tuple = ("HTF_STRUCTURE_CONFLICT",)
                ev_fp, pol_fp, dec_fp = _compute_fps(
                    "INVALID", entry_ref, None, None, None, None, "INVALID",
                    None, None, None, None, None, None, "NO_VALID_SETUP", reasons_tuple,
                )
                return {
                    "model_id": model_id,
                    "symbol": context.symbol,
                    "decision_timestamp_ms": now_ms,
                    "direction": direction,
                    "setup_validity": "INVALID",
                    "entry_reference": entry_ref,
                    "entry_zone_low": None,
                    "entry_zone_high": None,
                    "selected_fvg_id": None,
                    "selected_break_id": None,
                    "selected_sweep_id": None,
                    "setup_episode_id": None,
                    "unicorn_validity": "INVALID",
                    "unicorn_failure_stage": "HTF_STRUCTURE_CONFLICT",
                    "selected_anchor_id": None,
                    "anchor_kind": None,
                    "structural_invalidation_price": None,
                    "buffer_value": None,
                    "selected_sl": None,
                    "legacy_sl": None,
                    "take_profit": None,
                    "selected_target_id": None,
                    "atr_m1": context.atr_m1,
                    "atr_m5": context.atr_m5,
                    "spread": context.spread,
                    "hypothetical_quantity": None,
                    "stop_distance_price": None,
                    "target_distance_price": None,
                    "gross_rr": None,
                    "net_rr": None,
                    "decision_status": "NO_VALID_SETUP",
                    "rejection_reason_codes": reasons_tuple,
                    "evidence_fingerprint": ev_fp,
                    "policy_fingerprint": pol_fp,
                    "decision_fingerprint": dec_fp,
                    "adapter_origin": "BASELINE_DIRECT_PRODUCTION",
                    "raw_candidate_reason_code": "HTF_STRUCTURE_CONFLICT",
                    "oracle_commit_sha": PRE_REFACTOR_BASELINE_COMMIT_SHA,
                }

        atr_m1_obj = context.atr_m1_obj
        atr_m5_obj = context.atr_m5_obj
        if (
            atr_m1_obj is None
            or getattr(atr_m1_obj, "status", None) != "AVAILABLE"
            or float(getattr(atr_m1_obj, "value", 0.0) or 0.0) <= 0.0
        ):
            atr_reasons = (
                "ATR_MISSING",
                str(getattr(atr_m1_obj, "reason_code", "ATR_UNAVAILABLE") or "ATR_UNAVAILABLE"),
            )
            ep_id = f"ep_{model_id}_{now_ms}"
            ev_fp, pol_fp, dec_fp = _compute_fps(
                "VALID", entry_ref, None, None, None, ep_id, "ATR_MISSING",
                None, None, None, None, 0.0, 0.0, "VALID_SETUP_REJECTED_BY_RISK", atr_reasons,
            )
            return {
                "model_id": model_id,
                "symbol": context.symbol,
                "decision_timestamp_ms": now_ms,
                "direction": direction,
                "setup_validity": "VALID",
                "entry_reference": entry_ref,
                "entry_zone_low": None,
                "entry_zone_high": None,
                "selected_fvg_id": None,
                "selected_break_id": None,
                "selected_sweep_id": None,
                "setup_episode_id": ep_id,
                "unicorn_validity": "ATR_MISSING",
                "unicorn_failure_stage": "ATR_MISSING",
                "selected_anchor_id": None,
                "anchor_kind": None,
                "structural_invalidation_price": None,
                "buffer_value": None,
                "selected_sl": None,
                "legacy_sl": 0.0,
                "take_profit": None,
                "selected_target_id": None,
                "atr_m1": None,
                "atr_m5": context.atr_m5,
                "spread": context.spread,
                "hypothetical_quantity": 0.0,
                "stop_distance_price": None,
                "target_distance_price": None,
                "gross_rr": 0.0,
                "net_rr": 0.0,
                "decision_status": "VALID_SETUP_REJECTED_BY_RISK",
                "rejection_reason_codes": atr_reasons,
                "evidence_fingerprint": ev_fp,
                "policy_fingerprint": pol_fp,
                "decision_fingerprint": dec_fp,
                "adapter_origin": "BASELINE_DIRECT_PRODUCTION",
                "raw_candidate_reason_code": "ATR_MISSING",
                "candidate_key": f"shopp_{model_id}_{context.symbol}_{now_ms}_no_atr",
                "oracle_commit_sha": PRE_REFACTOR_BASELINE_COMMIT_SHA,
            }

        atr_m1_val = round(float(atr_m1_obj.value), 6)
        atr_m5_val = (
            round(float(atr_m5_obj.value), 6)
            if (atr_m5_obj is not None and getattr(atr_m5_obj, "status", None) == "AVAILABLE" and atr_m5_obj.value is not None)
            else None
        )

        # Invoke pre-refactor production model evaluator (ScalpM1ModelService / evaluate_cg01_candidate)
        scalp_cand = None
        cg01_res = None
        closed_5m = list(context.closed_m5_candles)
        closed_15m = list(context.closed_m15_candles)
        pools = list(context.liquidity_pools)
        zones = list(context.htf_zones)
        mid_px = round((bid + ask) / 2.0, 2) if (bid > 0 and ask >= bid) else 0.0
        quote_obj = CanonicalQuote(
            symbol=context.symbol,
            last=mid_px,
            bid=bid,
            ask=ask,
            mark_price=mid_px,
            spread=round(ask - bid, 4) if ask >= bid else 0.0,
            exchange_ts_ms=context.quote_timestamp_ms or now_ms,
            received_at_ms=now_ms,
            source="PRE_REFACTOR_ORACLE",
            connection_epoch=1,
            status="VALID" if (bid > 0 and ask >= bid) else "MALFORMED",
            freshness_sec=0.1,
        )
        raw_policy_settings = {
            "capital_usdt": float(risk_dict.get("capital_usdt", 1000.0)),
            "risk_pct": float(risk_dict.get("risk_pct", 0.25)),
            "leverage": int(risk_dict.get("leverage", 20)),
            "margin_mode": str(risk_dict.get("margin_mode", "ISOLATED")),
            "min_net_rr": min_net_rr,
            "costs": CostAssumptions(
                slippage_usd=float(cost_dict.get("slippage_usd", 0.10)),
                maker_fee_rate=float(cost_dict.get("maker_fee_rate", 0.0002)),
                taker_fee_rate=float(cost_dict.get("taker_fee_rate", 0.0004)),
            ),
        }
        raw_stop_policy = StopPlacementPolicy(
            mode=StopPlacementMode.PAPER_STRUCTURAL.value,
            v18_structural_intelligence_enabled=True,
            v18_unicorn_detection_enabled=True,
            v18_noise_research_enabled=True,
        )
        raw_stop_context = {
            "closed_5m": closed_5m,
            "atr_m1_obj": context.atr_m1_obj,
            "atr_m5_obj": context.atr_m5_obj,
        }
        if closed_1m:
            lows_z = [float(b.get("low") if isinstance(b, dict) else getattr(b, "low", 0.0)) for b in closed_1m[-15:]]
            highs_z = [float(b.get("high") if isinstance(b, dict) else getattr(b, "high", 0.0)) for b in closed_1m[-15:]]
            if direction == "LONG":
                bot_z = min(lows_z)
                top_z = round(bot_z + max(0.5, (max(highs_z) - bot_z) * 0.25), 2)
            else:
                top_z = max(highs_z)
                bot_z = round(top_z - max(0.5, (top_z - min(lows_z)) * 0.25), 2)
        else:
            bot_z, top_z = 2640.0, 2642.0
        default_zone = WatchedZone(
            zone_id=f"shared_zone_{direction}_{now_ms}",
            symbol=context.symbol,
            timeframe="15M",
            zone_type="OB",
            direction="BULLISH" if direction == "LONG" else "BEARISH",
            top=round(top_z, 2),
            bottom=round(bot_z, 2),
            created_at=max(0, now_ms - 900_000),
            known_at=max(0, now_ms - 300_000),
        )
        htf_bias_val = "BULLISH" if direction == "LONG" else "BEARISH"
        if model_id == ModelIdentifier.SMC_M1_REVERSAL.value:
            matching_zones = [
                z for z in zones
                if getattr(z, "direction", "") in (("BULLISH", "LONG") if direction == "LONG" else ("BEARISH", "SHORT"))
            ]
            zone = matching_zones[0] if matching_zones else default_zone
            scalp_cand = ScalpM1ModelService.evaluate_m1_reversal_candidate(
                zone=zone,
                candles_1m=closed_1m,
                current_quote=quote_obj,
                policy_settings=raw_policy_settings,
                now_ms=now_ms,
                config=cfg,
                context_levels=pools,
                stop_policy=raw_stop_policy,
                stop_context=raw_stop_context,
            )
        elif model_id == ModelIdentifier.SMC_M1_CONTINUATION.value:
            scalp_cand = ScalpM1ModelService.evaluate_m1_continuation_candidate(
                candles_1m=closed_1m,
                candles_5m=closed_5m,
                current_quote=quote_obj,
                htf_bias=htf_bias_val,
                h1_alignment=htf_bias_val,
                policy_settings=raw_policy_settings,
                now_ms=now_ms,
                config=cfg,
                context_levels=pools,
                stop_policy=raw_stop_policy,
                stop_context=raw_stop_context,
            )
        elif model_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value:
            scalp_cand = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                closed_1m=closed_1m,
                closed_5m=closed_5m,
                quote=quote_obj,
                htf_bias=htf_bias_val,
                h1_alignment=htf_bias_val,
                policy_settings=raw_policy_settings,
                now_ms=now_ms,
                config=cfg,
                context_levels=pools,
                stop_policy=raw_stop_policy,
                stop_context=raw_stop_context,
            )
        elif model_id == ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value:
            clusters = build_liquidity_clusters(
                levels=pools,
                tolerance_price=max(0.25, 0.25 * (context.atr_m1 or 1.0)),
                now_ms=now_ms,
                timeframe="1M",
                symbol=context.symbol,
            )
            target_side = "SELL_SIDE" if direction == "LONG" else "BUY_SIDE"
            matching_clusters = [c for c in clusters if getattr(c, "side", "") == target_side]
            cluster = matching_clusters[-1] if matching_clusters else (clusters[-1] if clusters else None)
            scalp_cand = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
                closed_1m=closed_1m,
                cluster=cluster,
                quote=quote_obj,
                policy_settings=raw_policy_settings,
                now_ms=now_ms,
                config=cfg,
                context_levels=pools,
                stop_policy=raw_stop_policy,
                stop_context=raw_stop_context,
            )
        elif model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value:
            c_cfg = CG01Config()
            m15_for_cg01 = closed_15m if len(closed_15m) >= 5 else (closed_5m if len(closed_5m) >= 5 else closed_1m)
            m5_for_cg01 = closed_5m if len(closed_5m) >= 5 else closed_1m
            h1_for_cg01 = list(getattr(context, "closed_h1_candles", ())) or m15_for_cg01
            h4_for_cg01 = list(getattr(context, "closed_h4_candles", ())) or h1_for_cg01
            try:
                cg01_res = evaluate_cg01_candidate(
                    context={
                        "1M": closed_1m,
                        "5M": m5_for_cg01,
                        "15M": m15_for_cg01,
                        "1H": h1_for_cg01,
                        "4H": h4_for_cg01,
                        "1D": list(getattr(context, "closed_d1_candles", ())),
                    },
                    current_quote={"bid": bid, "ask": ask, "timestamp": context.quote_timestamp_ms or now_ms},
                    config=c_cfg,
                    now_ms=now_ms,
                    capital=float(risk_dict.get("capital_usdt", 1000.0)),
                    risk_pct=float(risk_dict.get("risk_pct", 0.25)),
                )
            except Exception:
                cg01_res = None

        sh_1m, sl_1m = identify_confirmed_swings(
            closed_1m, timeframe="1M", left_bars=2, right_bars=2, as_of_ms=now_ms
        )
        setup_info = cls._extract_pre_refactor_setup(
            symbol=context.symbol,
            model_id=model_id,
            direction=direction,
            closed_1m=closed_1m,
            sh_1m=sh_1m,
            sl_1m=sl_1m,
            cfg=cfg,
            cutoff_ms=now_ms,
        )

        prod_has_setup = bool(
            (scalp_cand is not None and (scalp_cand.is_eligible or scalp_cand.planned_entry > 0))
            or (cg01_res is not None and getattr(cg01_res, "candidate", None) is not None)
            or setup_info.get("has_setup", False)
        )

        if not prod_has_setup:
            raw_code = str(
                setup_info.get("reason_code")
                or (scalp_cand.reason_code if scalp_cand is not None else None)
                or (getattr(cg01_res, "reason_code", None) if cg01_res is not None else None)
                or "NO_VALID_SETUP"
            )
            waiting_codes = {"WAITING_M1_RETEST", "WAITING_M1_BOS", "WAITING_SWEEP_RECLAIM"}
            dec_status = "WAITING_CONFIRMATION" if raw_code in waiting_codes else "NO_VALID_SETUP"
            reasons = (raw_code,)
            ev_fp, pol_fp, dec_fp = _compute_fps(
                "INVALID", entry_ref, None, None, None, None, "INVALID",
                None, None, None, None, None, None, dec_status, reasons,
            )
            return {
                "model_id": model_id,
                "symbol": context.symbol,
                "decision_timestamp_ms": now_ms,
                "direction": direction,
                "setup_validity": "INVALID",
                "entry_reference": entry_ref,
                "entry_zone_low": None,
                "entry_zone_high": None,
                "selected_fvg_id": None,
                "selected_break_id": None,
                "selected_sweep_id": None,
                "setup_episode_id": None,
                "unicorn_validity": "INVALID",
                "unicorn_failure_stage": raw_code,
                "selected_anchor_id": None,
                "anchor_kind": None,
                "structural_invalidation_price": None,
                "buffer_value": None,
                "selected_sl": None,
                "legacy_sl": None,
                "take_profit": None,
                "selected_target_id": None,
                "atr_m1": atr_m1_val,
                "atr_m5": atr_m5_val,
                "spread": context.spread,
                "hypothetical_quantity": None,
                "stop_distance_price": None,
                "target_distance_price": None,
                "gross_rr": None,
                "net_rr": None,
                "decision_status": dec_status,
                "rejection_reason_codes": reasons,
                "evidence_fingerprint": ev_fp,
                "policy_fingerprint": pol_fp,
                "decision_fingerprint": dec_fp,
                "adapter_origin": "BASELINE_DIRECT_PRODUCTION",
                "raw_candidate_reason_code": raw_code,
                "oracle_commit_sha": PRE_REFACTOR_BASELINE_COMMIT_SHA,
            }

        selected_fvg = setup_info.get("selected_fvg")
        selected_break = setup_info.get("selected_break")
        selected_sweep = setup_info.get("selected_sweep")
        selected_reference = setup_info.get("selected_reference")
        legacy_sl = float(setup_info.get("legacy_sl") or (scalp_cand.stop_loss if scalp_cand and scalp_cand.stop_loss else round(entry_ref - 1.5, 2)))
        episode_id = str(setup_info.get("episode_id") or (scalp_cand.origin_episode_id if scalp_cand and scalp_cand.origin_episode_id else f"ep_{model_id}_{direction}_{now_ms}"))
        entry_zone_low = setup_info.get("entry_zone_low")
        entry_zone_high = setup_info.get("entry_zone_high")

        unicorn_ev = detect_unicorn_evidence(
            symbol=context.symbol,
            direction=direction,
            closed_bars=closed_1m,
            as_of_ms=now_ms,
            selected_fvg=selected_fvg,
            selected_break=selected_break,
            selected_reference=selected_reference,
            confirmed_swings=(sh_1m, sl_1m),
            tick_size=cfg.tick_size,
            strict_causal_binding=True,
        )
        unicorn_dict = unicorn_ev.to_dict() if hasattr(unicorn_ev, "to_dict") else {}

        stop_policy = StopPlacementPolicy(
            mode=StopPlacementMode.PAPER_STRUCTURAL.value,
            v18_structural_intelligence_enabled=True,
            v18_unicorn_detection_enabled=True,
            v18_noise_research_enabled=True,
        )
        anchor_res = resolve_stop_anchor(
            model_id=model_id,
            direction=direction,
            closed_bars=closed_1m,
            as_of_ms=now_ms,
            selected_break=selected_break,
            selected_fvg=selected_fvg,
            selected_sweep=selected_sweep,
            confirmed_swings=(sh_1m, sl_1m),
            tick_size=cfg.tick_size,
            unicorn_evidence=unicorn_ev if unicorn_ev.is_valid else None,
            policy=stop_policy,
        )
        buffer_res = compute_stop_buffer(
            atr_m1=atr_m1_obj,
            atr_m5=atr_m5_obj if (atr_m5_obj and getattr(atr_m5_obj, "status", None) == "AVAILABLE") else None,
            bid=bid,
            ask=ask,
            policy=stop_policy,
            tick_size=cfg.tick_size,
            symbol=context.symbol,
            decision_cutoff=now_ms,
        )

        target_dec = resolve_target_fn(
            list(context.liquidity_pools),
            direction,
            entry_ref,
            as_of_ms=now_ms,
            atr=atr_m1_val,
            model_id=model_id,
        )
        has_valid_target = bool(
            getattr(target_dec, "status", None) == "CONFIRMED"
            and getattr(target_dec, "price", None) is not None
            and (
                (direction == "LONG" and float(target_dec.price) > entry_ref)
                or (direction == "SHORT" and float(target_dec.price) < entry_ref)
            )
        )
        target_price = round(float(target_dec.price), 2) if has_valid_target else None
        target_ev_id = str(target_dec.source_level_id) if has_valid_target and getattr(target_dec, "source_level_id", None) else None

        sl_eval_tp = target_price if target_price is not None else (
            round(entry_ref + 3.0 * atr_m1_val, 2)
            if direction == "LONG"
            else round(max(0.01, entry_ref - 3.0 * atr_m1_val), 2)
        )

        stop_dec = plan_structural_stop(
            direction=direction,
            planned_entry=entry_ref,
            take_profit=sl_eval_tp,
            legacy_sl=legacy_sl,
            anchor_result=anchor_res,
            buffer_result=buffer_res,
            policy=stop_policy,
            as_of_ms=now_ms,
            tick_size=cfg.tick_size,
            atr_m1_val=atr_m1_val,
            atr_m5_val=atr_m5_val,
            spread=context.spread,
            atr_m1_obj=atr_m1_obj,
            atr_m5_obj=atr_m5_obj if (atr_m5_obj and getattr(atr_m5_obj, "status", None) == "AVAILABLE") else None,
            symbol=context.symbol,
            unicorn_evidence=unicorn_ev if unicorn_ev.is_valid else None,
        )

        selected_sl = float(stop_dec.selected_sl) if stop_dec.selected_sl is not None else legacy_sl
        structural_sl = float(stop_dec.structural_sl) if stop_dec.structural_sl is not None else (
            selected_sl if anchor_res.anchor is not None else None
        )
        unicorn_sl_cand = float(stop_dec.unicorn_sl_candidate) if stop_dec.unicorn_sl_candidate is not None else None

        rejection_reasons: List[str] = []
        if stop_dec.status != StopStatus.VALID.value:
            rejection_reasons.extend(list(stop_dec.reasons or (stop_dec.status,)))
        if not has_valid_target or target_price is None:
            rejection_reasons.append("SHADOW_REJECTED_NO_VALID_TARGET")
        if bool(policy_dict.get("session_restricted", False)) or bool(getattr(context, "session_restricted", False)):
            rejection_reasons.append(
                str(policy_dict.get("session_restriction_reason") or getattr(context, "session_restriction_reason", None) or "SESSION_TIME_RESTRICTED")
            )

        policy_settings = {
            "capital_usdt": float(risk_dict.get("capital_usdt", 1000.0)),
            "risk_pct": float(risk_dict.get("risk_pct", 0.25)),
            "leverage": int(risk_dict.get("leverage", 20)),
            "margin_mode": str(risk_dict.get("margin_mode", "ISOLATED")),
            "min_net_rr": min_net_rr,
            "min_buffer_ticks": stop_policy.min_buffer_ticks,
            "costs": CostAssumptions(
                slippage_usd=float(cost_dict.get("slippage_usd", 0.10)),
                maker_fee_rate=float(cost_dict.get("maker_fee_rate", 0.0002)),
                taker_fee_rate=float(cost_dict.get("taker_fee_rate", 0.0004)),
            ),
        }

        hypothetical_qty = 0.0
        gross_rr = 0.0
        net_rr = 0.0
        comp_plan_dict: Optional[Dict[str, Any]] = None

        if has_valid_target and target_price is not None:
            calc_res = calculate_risk_reward(
                direction=direction,
                planned_entry=entry_ref,
                stop_loss=selected_sl,
                take_profit=target_price,
                capital_usdt=policy_settings["capital_usdt"],
                risk_pct=policy_settings["risk_pct"],
                leverage=policy_settings["leverage"],
                margin_mode=policy_settings["margin_mode"],
                min_net_rr=min_net_rr,
                costs=policy_settings["costs"],
                now_ms=now_ms,
            )
            hypothetical_qty = round(float(calc_res.quantity or 0.0), 6)
            gross_rr = round(float(calc_res.gross_rr or 0.0), 4)
            net_rr = round(float(calc_res.net_rr or 0.0), 4)

            if not calc_res.is_valid:
                rejection_reasons.append(calc_res.invalid_reason or "CALCULATOR_INVALID")
            elif not calc_res.can_execute:
                rejection_reasons.append(calc_res.skip_reason or "CANNOT_EXECUTE")
            elif not calc_res.meets_min_rr or net_rr < min_net_rr:
                rejection_reasons.append("SHADOW_REJECTED_INSUFFICIENT_NET_RR")

            comp_plan = build_stop_comparison_plan(
                opportunity_key=f"sh_comp_{model_id}_{direction}_{now_ms}",
                model_id=model_id,
                symbol=context.symbol,
                direction=direction,
                planned_entry=entry_ref,
                take_profit=target_price,
                legacy_sl=legacy_sl,
                anchor=anchor_res.anchor,
                buffer_result=buffer_res,
                policy_settings=policy_settings,
                tick_size=cfg.tick_size,
                min_net_rr=min_net_rr,
                atr_m1=atr_m1_val,
                atr_m5=atr_m5_val,
                spread=context.spread,
                now_ms=now_ms,
                decision_cutoff_ms=now_ms,
                context_status="VERIFIED",
                unicorn_evidence=unicorn_dict,
            )
            comp_plan_dict = comp_plan.to_dict() if hasattr(comp_plan, "to_dict") else None

        is_eligible = len(rejection_reasons) == 0 and has_valid_target
        decision_status = "ELIGIBLE" if is_eligible else "VALID_SETUP_REJECTED_BY_RISK"

        selected_fvg_id = str(selected_fvg.get("id")) if isinstance(selected_fvg, dict) and selected_fvg.get("id") else None
        selected_break_id = str(selected_break.get("break_id")) if isinstance(selected_break, dict) and selected_break.get("break_id") else None
        selected_sweep_id = str(selected_sweep.get("id")) if isinstance(selected_sweep, dict) and selected_sweep.get("id") else None
        selected_anchor_id = str(anchor_res.anchor.id) if anchor_res.anchor else None
        anchor_kind = str(anchor_res.anchor.kind) if anchor_res.anchor else None
        invalidation_px = round(float(anchor_res.anchor.price), 2) if anchor_res.anchor else None
        buffer_px = round(float(buffer_res.buffer_price), 4) if buffer_res and getattr(buffer_res, "buffer_price", None) is not None else None
        stop_dist_px = round(abs(entry_ref - selected_sl), 4) if selected_sl is not None else None
        target_dist_px = round(abs(target_price - entry_ref), 4) if target_price is not None else None
        opp_id = f"shopp_{model_id}_{context.symbol}_{direction}_{episode_id}"

        final_reasons = tuple(rejection_reasons)
        ev_fp, pol_fp, dec_fp = _compute_fps(
            "VALID",
            entry_ref,
            selected_fvg_id,
            selected_break_id,
            selected_sweep_id,
            episode_id,
            str(unicorn_ev.validity_status),
            selected_anchor_id,
            invalidation_px,
            structural_sl,
            target_price,
            hypothetical_qty,
            net_rr,
            decision_status,
            final_reasons,
        )

        return {
            "model_id": model_id,
            "symbol": context.symbol,
            "decision_timestamp_ms": now_ms,
            "direction": direction,
            "setup_validity": "VALID",
            "entry_reference": entry_ref,
            "entry_zone_low": entry_zone_low,
            "entry_zone_high": entry_zone_high,
            "selected_fvg_id": selected_fvg_id,
            "selected_break_id": selected_break_id,
            "selected_sweep_id": selected_sweep_id,
            "setup_episode_id": episode_id,
            "unicorn_validity": str(unicorn_ev.validity_status),
            "unicorn_failure_stage": None if unicorn_ev.is_valid else str(unicorn_ev.validity_status),
            "selected_anchor_id": selected_anchor_id,
            "anchor_kind": anchor_kind,
            "structural_invalidation_price": invalidation_px,
            "buffer_value": buffer_px,
            "selected_sl": structural_sl,
            "legacy_sl": legacy_sl,
            "take_profit": target_price,
            "selected_target_id": target_ev_id,
            "atr_m1": atr_m1_val,
            "atr_m5": atr_m5_val,
            "spread": context.spread,
            "hypothetical_quantity": hypothetical_qty,
            "stop_distance_price": stop_dist_px,
            "target_distance_price": target_dist_px,
            "gross_rr": gross_rr,
            "net_rr": net_rr,
            "decision_status": decision_status,
            "rejection_reason_codes": final_reasons,
            "evidence_fingerprint": ev_fp,
            "policy_fingerprint": pol_fp,
            "decision_fingerprint": dec_fp,
            "adapter_origin": "BASELINE_DIRECT_PRODUCTION",
            "raw_candidate_reason_code": "ELIGIBLE" if is_eligible else (final_reasons[0] if final_reasons else "REJECTED"),
            "candidate_key": opp_id,
            "unicorn_evidence_dict": unicorn_dict,
            "stop_comparison_plan_dict": comp_plan_dict,
            "unicorn_sl_candidate": unicorn_sl_cand,
            "protective_distance": stop_dec.protective_distance,
            "selected_breaker_id": unicorn_ev.breaker_id,
            "oracle_commit_sha": PRE_REFACTOR_BASELINE_COMMIT_SHA,
        }


REQUIRED_BASELINE_CATEGORIES: Tuple[str, ...] = (
    "LONG",
    "SHORT",
    "NO_TRADE",
    "Rejected entry",
    "Missing market data",
    "Insufficient HTF history",
    "Conflicting structure",
    "Invalid SL/TP",
    "Risk rejection",
    "Session/time restrictions",
    "Boundary conditions",
)

SUPPORTED_BASELINE_MODELS: Tuple[str, ...] = (
    ModelIdentifier.SMC_M1_REVERSAL.value,
    ModelIdentifier.SMC_M1_CONTINUATION.value,
    ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
    ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
    ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
)


def _make_synthetic_candle_series(
    *,
    base_open_ms: int,
    direction: str = "LONG",
    pattern: str = "TREND_IMPULSE",
) -> Dict[str, Any]:
    """
    Constructs deterministic closed M1/M5/M15/H1/H4 candle series for baseline scenario characterization.
    All timestamps are strictly closed (`close_time <= decision_ms`) and aligned to UTC boundaries.
    """
    # Align base_open_ms to a 4H UTC boundary
    aligned_base_ms = (base_open_ms // 14_400_000) * 14_400_000
    decision_ms = aligned_base_ms + 30 * 60_000
    m1_bars: List[Dict[str, Any]] = []

    if pattern == "FLAT_CHOP":
        for i in range(30):
            o_ms = aligned_base_ms + i * 60_000
            c_ms = o_ms + 60_000
            px = 2650.0 + (0.05 if i % 2 == 0 else -0.05)
            m1_bars.append({
                "timestamp": o_ms,
                "open_time": o_ms,
                "close_time": c_ms,
                "open": round(px, 2),
                "high": round(px + 0.10, 2),
                "low": round(px - 0.10, 2),
                "close": round(px, 2),
                "volume": 100.0,
                "is_closed": True,
            })
    else:
        is_long = direction == "LONG"
        base_px = 2640.0 if is_long else 2660.0
        sign = 1.0 if is_long else -1.0
        for i in range(30):
            o_ms = aligned_base_ms + i * 60_000
            c_ms = o_ms + 60_000
            if i < 10:
                # Initial wave forming confirmed swing high and low
                wave = (i if i <= 5 else (10 - i)) * 0.6 * sign
                o_px = base_px + wave
                c_px = o_px + 0.35 * sign
                h_px = max(o_px, c_px) + 0.45
                l_px = min(o_px, c_px) - 0.45
            elif i < 18:
                # Counter-pullback and second confirmed swing
                wave = ((i - 10) if i <= 14 else (18 - i)) * 0.5 * (-sign)
                o_px = base_px + 1.2 * sign + wave
                c_px = o_px - 0.25 * sign
                h_px = max(o_px, c_px) + 0.40
                l_px = min(o_px, c_px) - 0.40
            elif i == 25:
                # Sweep bar
                o_px = base_px + 0.5 * sign
                c_px = base_px + 0.8 * sign
                if is_long:
                    l_px = base_px - 1.8
                    h_px = c_px + 0.4
                else:
                    h_px = base_px + 1.8
                    l_px = c_px - 0.4
            elif i in (26, 27, 28):
                # Strong displacement creating 3-candle FVG and BOS
                step = i - 25
                o_px = base_px + (0.8 + (step - 1) * 1.6) * sign
                c_px = base_px + (0.8 + step * 1.8) * sign
                if is_long:
                    l_px = o_px - 0.10
                    h_px = c_px + 0.15
                else:
                    h_px = o_px + 0.10
                    l_px = c_px - 0.15
            else:
                # Retest/continuation bar at i == 29
                o_px = base_px + 4.8 * sign
                c_px = base_px + 5.4 * sign
                if is_long:
                    l_px = base_px + 2.5 * sign
                    h_px = c_px + 0.35
                else:
                    h_px = base_px + 2.5 * sign
                    l_px = c_px - 0.35
            m1_bars.append({
                "timestamp": o_ms,
                "open_time": o_ms,
                "close_time": c_ms,
                "open": round(o_px, 2),
                "high": round(h_px, 2),
                "low": round(l_px, 2),
                "close": round(c_px, 2),
                "volume": 150.0 + i * 5.0,
                "is_closed": True,
            })

    m5_bars: List[Dict[str, Any]] = []
    for k in range(6):
        slice_1m = m1_bars[k * 5 : (k + 1) * 5]
        o_ms = int(slice_1m[0]["open_time"])
        c_ms = int(slice_1m[-1]["close_time"])
        m5_bars.append({
            "timestamp": o_ms,
            "open_time": o_ms,
            "close_time": c_ms,
            "open": float(slice_1m[0]["open"]),
            "high": max(float(b["high"]) for b in slice_1m),
            "low": min(float(b["low"]) for b in slice_1m),
            "close": float(slice_1m[-1]["close"]),
            "volume": sum(float(b["volume"]) for b in slice_1m),
            "is_closed": True,
        })

    m15_bars: List[Dict[str, Any]] = []
    for k in range(6):
        o_ms = aligned_base_ms - (5 - k) * 900_000
        c_ms = o_ms + 900_000
        if c_ms <= decision_ms:
            m15_bars.append({
                "timestamp": o_ms,
                "open_time": o_ms,
                "close_time": c_ms,
                "open": 2640.0 + k * 0.5,
                "high": 2643.0 + k * 0.5,
                "low": 2638.0 + k * 0.5,
                "close": 2641.5 + k * 0.5,
                "volume": 900.0,
                "is_closed": True,
            })

    h1_bars: List[Dict[str, Any]] = []
    for k in range(6):
        o_ms = aligned_base_ms - (6 - k) * 3_600_000
        c_ms = o_ms + 3_600_000
        h1_bars.append({
            "timestamp": o_ms,
            "open_time": o_ms,
            "close_time": c_ms,
            "open": 2635.0 + k * 1.2,
            "high": 2642.0 + k * 1.2,
            "low": 2632.0 + k * 1.2,
            "close": 2639.0 + k * 1.2,
            "volume": 3600.0,
            "is_closed": True,
        })

    h4_bars: List[Dict[str, Any]] = []
    for k in range(4):
        o_ms = aligned_base_ms - (4 - k) * 14_400_000
        c_ms = o_ms + 14_400_000
        h4_bars.append({
            "timestamp": o_ms,
            "open_time": o_ms,
            "close_time": c_ms,
            "open": 2625.0 + k * 3.0,
            "high": 2645.0 + k * 3.0,
            "low": 2620.0 + k * 3.0,
            "close": 2638.0 + k * 3.0,
            "volume": 14400.0,
            "is_closed": True,
        })

    last_close = float(m1_bars[-1]["close"])
    return {
        "decision_ms": decision_ms,
        "m1_candles": m1_bars,
        "m5_candles": m5_bars,
        "m15_candles": m15_bars,
        "h1_candles": h1_bars,
        "h4_candles": h4_bars,
        "bid": round(last_close - 0.10, 2),
        "ask": round(last_close + 0.10, 2),
    }


def build_phase_b_characterization_scenarios(
    context_builder_fn: Callable[..., Any],
) -> List[Dict[str, Any]]:
    """
    Builds the frozen Phase B scenario matrix covering all 11 mandatory categories
    across the 5 supported production models (`SMC_M1_REVERSAL`, `SMC_M1_CONTINUATION`,
    `SMC_M1_FVG_CONTINUATION`, `SMC_M1_SWEEP_RECLAIM`, `CG01`).
    """
    base_ts = 1_761_696_000_000  # 2025-10-29 00:00:00 UTC (aligned to 4H boundary)
    long_series = _make_synthetic_candle_series(base_open_ms=base_ts, direction="LONG", pattern="TREND_IMPULSE")
    short_series = _make_synthetic_candle_series(base_open_ms=base_ts + 14_400_000, direction="SHORT", pattern="TREND_IMPULSE")
    chop_series = _make_synthetic_candle_series(base_open_ms=base_ts + 28_800_000, direction="LONG", pattern="FLAT_CHOP")

    scenarios: List[Dict[str, Any]] = []
    for model_id in SUPPORTED_BASELINE_MODELS:
        # 1. LONG
        ctx_long = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=long_series["decision_ms"],
            m1_candles=long_series["m1_candles"],
            m5_candles=long_series["m5_candles"],
            m15_candles=long_series["m15_candles"],
            h1_candles=long_series["h1_candles"],
            h4_candles=long_series["h4_candles"],
            bid=long_series["bid"],
            ask=long_series["ask"],
            policy_config={"min_net_rr": 1.2, "tick_size": 0.01},
            risk_config={"min_net_rr": 1.2},
            htf_bias="BULLISH",
            h1_alignment="ALIGNED",
        )
        scenarios.append({
            "scenario_id": f"SCEN_01_LONG_{model_id}",
            "category": "LONG",
            "model_id": model_id,
            "direction": "LONG",
            "context": ctx_long,
        })

        # 2. SHORT
        ctx_short = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=short_series["decision_ms"],
            m1_candles=short_series["m1_candles"],
            m5_candles=short_series["m5_candles"],
            m15_candles=short_series["m15_candles"],
            h1_candles=short_series["h1_candles"],
            h4_candles=short_series["h4_candles"],
            bid=short_series["bid"],
            ask=short_series["ask"],
            policy_config={"min_net_rr": 1.2, "tick_size": 0.01},
            risk_config={"min_net_rr": 1.2},
            htf_bias="BEARISH",
            h1_alignment="ALIGNED",
        )
        scenarios.append({
            "scenario_id": f"SCEN_02_SHORT_{model_id}",
            "category": "SHORT",
            "model_id": model_id,
            "direction": "SHORT",
            "context": ctx_short,
        })

        # 3. NO_TRADE
        ctx_notrade = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=chop_series["decision_ms"],
            m1_candles=chop_series["m1_candles"],
            m5_candles=chop_series["m5_candles"],
            m15_candles=chop_series["m15_candles"],
            h1_candles=chop_series["h1_candles"],
            h4_candles=chop_series["h4_candles"],
            bid=chop_series["bid"],
            ask=chop_series["ask"],
            policy_config={"min_net_rr": 1.8, "tick_size": 0.01},
            risk_config={"min_net_rr": 1.8},
        )
        scenarios.append({
            "scenario_id": f"SCEN_03_NO_TRADE_{model_id}",
            "category": "NO_TRADE",
            "model_id": model_id,
            "direction": "LONG",
            "context": ctx_notrade,
        })

        # 4. Rejected entry (Model Disabled)
        ctx_disabled = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=long_series["decision_ms"],
            m1_candles=long_series["m1_candles"],
            m5_candles=long_series["m5_candles"],
            m15_candles=long_series["m15_candles"],
            h1_candles=long_series["h1_candles"],
            h4_candles=long_series["h4_candles"],
            bid=long_series["bid"],
            ask=long_series["ask"],
            model_enablement={model_id: False},
        )
        scenarios.append({
            "scenario_id": f"SCEN_04_REJECTED_ENTRY_{model_id}",
            "category": "Rejected entry",
            "model_id": model_id,
            "direction": "LONG",
            "context": ctx_disabled,
        })

        # 5. Missing market data (Insufficient M1 candles / None bid/ask)
        ctx_missing = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=long_series["decision_ms"],
            m1_candles=long_series["m1_candles"][:5],
            m5_candles=long_series["m5_candles"][:1],
            m15_candles=(),
            bid=None,
            ask=None,
        )
        scenarios.append({
            "scenario_id": f"SCEN_05_MISSING_MARKET_DATA_{model_id}",
            "category": "Missing market data",
            "model_id": model_id,
            "direction": "LONG",
            "context": ctx_missing,
        })

        # 6. Insufficient HTF history
        ctx_insuff_htf = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=long_series["decision_ms"],
            m1_candles=long_series["m1_candles"],
            m5_candles=long_series["m5_candles"],
            m15_candles=long_series["m15_candles"],
            h1_candles=long_series["h1_candles"][:1],
            h4_candles=(),
            bid=long_series["bid"],
            ask=long_series["ask"],
            policy_config={"require_htf_history": True, "min_h1_bars": 4, "min_h4_bars": 2},
        )
        scenarios.append({
            "scenario_id": f"SCEN_06_INSUFFICIENT_HTF_HISTORY_{model_id}",
            "category": "Insufficient HTF history",
            "model_id": model_id,
            "direction": "LONG",
            "context": ctx_insuff_htf,
        })

        # 7. Conflicting structure
        ctx_conflict = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=long_series["decision_ms"],
            m1_candles=long_series["m1_candles"],
            m5_candles=long_series["m5_candles"],
            m15_candles=long_series["m15_candles"],
            h1_candles=long_series["h1_candles"],
            h4_candles=long_series["h4_candles"],
            bid=long_series["bid"],
            ask=long_series["ask"],
            htf_bias="BEARISH",
            h1_alignment="CONFLICT",
            policy_config={"enforce_htf_bias_alignment": True, "htf_bias": "BEARISH", "h1_alignment": "CONFLICT"},
        )
        scenarios.append({
            "scenario_id": f"SCEN_07_CONFLICTING_STRUCTURE_{model_id}",
            "category": "Conflicting structure",
            "model_id": model_id,
            "direction": "LONG",
            "context": ctx_conflict,
        })

        # 8. Invalid SL/TP (M1 gap invalidating ATR M1 / SL calculation)
        gapped_m1 = list(long_series["m1_candles"][:15]) + list(long_series["m1_candles"][17:])
        ctx_invalid_sl = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=long_series["decision_ms"],
            m1_candles=gapped_m1,
            m5_candles=long_series["m5_candles"],
            m15_candles=long_series["m15_candles"],
            h1_candles=long_series["h1_candles"],
            h4_candles=long_series["h4_candles"],
            bid=long_series["bid"],
            ask=long_series["ask"],
        )
        scenarios.append({
            "scenario_id": f"SCEN_08_INVALID_SL_TP_{model_id}",
            "category": "Invalid SL/TP",
            "model_id": model_id,
            "direction": "LONG",
            "context": ctx_invalid_sl,
        })

        # 9. Risk rejection (Excessive min_net_rr = 25.0)
        ctx_risk_rej = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=long_series["decision_ms"],
            m1_candles=long_series["m1_candles"],
            m5_candles=long_series["m5_candles"],
            m15_candles=long_series["m15_candles"],
            h1_candles=long_series["h1_candles"],
            h4_candles=long_series["h4_candles"],
            bid=long_series["bid"],
            ask=long_series["ask"],
            policy_config={"min_net_rr": 25.0, "tick_size": 0.01},
            risk_config={"min_net_rr": 25.0},
        )
        scenarios.append({
            "scenario_id": f"SCEN_09_RISK_REJECTION_{model_id}",
            "category": "Risk rejection",
            "model_id": model_id,
            "direction": "LONG",
            "context": ctx_risk_rej,
        })

        # 10. Session/time restrictions
        ctx_session_rest = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=long_series["decision_ms"],
            m1_candles=long_series["m1_candles"],
            m5_candles=long_series["m5_candles"],
            m15_candles=long_series["m15_candles"],
            h1_candles=long_series["h1_candles"],
            h4_candles=long_series["h4_candles"],
            bid=long_series["bid"],
            ask=long_series["ask"],
            session_restricted=True,
            session_restriction_reason="SESSION_ROLLOVER_RESTRICTED",
            policy_config={"session_restricted": True, "session_restriction_reason": "SESSION_ROLLOVER_RESTRICTED"},
        )
        scenarios.append({
            "scenario_id": f"SCEN_10_SESSION_RESTRICTIONS_{model_id}",
            "category": "Session/time restrictions",
            "model_id": model_id,
            "direction": "LONG",
            "context": ctx_session_rest,
        })

        # 11. Boundary conditions (Exact min_net_rr = 1.8 boundary & tight spread)
        ctx_boundary = context_builder_fn(
            symbol="XAUUSDT",
            decision_timestamp_ms=long_series["decision_ms"],
            m1_candles=long_series["m1_candles"],
            m5_candles=long_series["m5_candles"],
            m15_candles=long_series["m15_candles"],
            h1_candles=long_series["h1_candles"],
            h4_candles=long_series["h4_candles"],
            bid=round(long_series["bid"] + 0.09, 2),
            ask=round(long_series["bid"] + 0.10, 2),
            policy_config={"min_net_rr": 1.8, "tick_size": 0.01},
            risk_config={"min_net_rr": 1.8},
        )
        scenarios.append({
            "scenario_id": f"SCEN_11_BOUNDARY_CONDITIONS_{model_id}",
            "category": "Boundary conditions",
            "model_id": model_id,
            "direction": "LONG",
            "context": ctx_boundary,
        })

    return scenarios


def verify_historical_baseline_commit_provenance(
    commit_sha: str = PRE_REFACTOR_BASELINE_COMMIT_SHA,
) -> Dict[str, Any]:
    """
    Section 6 (V17.9.0.1.1): Inspects git object storage for the pre-refactor historical baseline
    commit (`c356d6551e13cd6af61cea5c3ab1f4aae1548af2`, V17.8.0.4) and computes the exact SHA-256
    digests of the historical domain and runtime files at that commit.
    """
    repo_root = Path(__file__).resolve().parents[2]
    tracked_files = (
        "backend/services/scalp_m1_model_service.py",
        "backend/services/cg01_engine.py",
        "backend/services/unicorn_evidence_service.py",
        "backend/services/stop_placement_service.py",
        "backend/services/scalp_watcher_orchestrator.py",
        "backend/services/market_shadow_service.py",
        "backend/services/v17_8_backtest_runner.py",
        "backend/domain_calculator.py",
    )
    commit_verified = False
    file_hashes: Dict[str, Dict[str, Any]] = {}
    try:
        cat_res = subprocess.run(
            ["git", "cat-file", "-t", commit_sha],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        commit_verified = cat_res.returncode == 0 and cat_res.stdout.strip() == "commit"
        if commit_verified:
            for rel_path in tracked_files:
                show_res = subprocess.run(
                    ["git", "show", f"{commit_sha}:{rel_path}"],
                    cwd=str(repo_root),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                if show_res.returncode == 0 and show_res.stdout:
                    file_hashes[rel_path] = {
                        "sha256": hashlib.sha256(show_res.stdout).hexdigest(),
                        "size_bytes": len(show_res.stdout),
                        "source": f"git:{commit_sha}:{rel_path}",
                    }
    except Exception:
        commit_verified = False

    return {
        "baseline_commit_sha": commit_sha,
        "git_commit_object_verified": commit_verified,
        "verified_files_count": len(file_hashes),
        "historical_file_sha256s": file_hashes,
    }


def generate_phase_b_baseline_artifacts(
    *,
    context_builder_fn: Callable[..., Any],
    refactored_evaluator_fn: Optional[Callable[[Any, str, str], Dict[str, Any]]] = None,
    five_way_evaluators: Optional[Dict[str, Callable[[Any, str, str], Dict[str, Any]]]] = None,
    output_dirs: Optional[Sequence[Path]] = None,
) -> Dict[str, Any]:
    """
    Executes `IndependentPreRefactorBaselineOracle` across all 11 mandatory categories
    and 5 production models (55 scenarios), compares against `refactored_evaluator_fn`
    and optional `five_way_evaluators` (Baseline vs Shared vs Production vs Shadow vs Backtest),
    and writes LF-normalized byte-exact artifacts:
      - `BASELINE_MANIFEST.json`
      - `BASELINE_DECISIONS.jsonl`
      - `BASELINE_CHARACTERIZATION.md`
      - `BASELINE_PROVENANCE.md`
    """
    provenance_info = verify_historical_baseline_commit_provenance(PRE_REFACTOR_BASELINE_COMMIT_SHA)
    scenarios = build_phase_b_characterization_scenarios(context_builder_fn)
    decision_records: List[Dict[str, Any]] = []
    category_counts: Dict[str, int] = {cat: 0 for cat in REQUIRED_BASELINE_CATEGORIES}
    model_counts: Dict[str, int] = {m: 0 for m in SUPPORTED_BASELINE_MODELS}
    parity_matches = 0
    five_way_matches = 0
    parity_divergences: List[Dict[str, Any]] = []

    compare_keys = (
        "decision_status",
        "setup_validity",
        "direction",
        "entry_reference",
        "selected_sl",
        "legacy_sl",
        "take_profit",
        "unicorn_validity",
        "rejection_reason_codes",
        "decision_fingerprint",
    )

    for item in scenarios:
        scen_id = item["scenario_id"]
        cat = item["category"]
        model_id = item["model_id"]
        direction = item["direction"]
        ctx = item["context"]

        oracle_dec = IndependentPreRefactorBaselineOracle.evaluate_baseline_dict(
            context=ctx,
            model_id=model_id,
            direction=direction,
        )
        category_counts[cat] = category_counts.get(cat, 0) + 1
        model_counts[model_id] = model_counts.get(model_id, 0) + 1

        parity_match = True
        field_diffs: Dict[str, Any] = {}
        if refactored_evaluator_fn is not None:
            ref_dec = refactored_evaluator_fn(ctx, model_id, direction)
            for key in compare_keys:
                b_val = oracle_dec.get(key)
                r_val = ref_dec.get(key)
                if isinstance(b_val, tuple):
                    b_val = list(b_val)
                if isinstance(r_val, tuple):
                    r_val = list(r_val)
                if b_val != r_val:
                    parity_match = False
                    field_diffs[key] = {"baseline": b_val, "refactored": r_val}

        five_way_status: Dict[str, str] = {"historical_baseline_oracle": oracle_dec["decision_fingerprint"]}
        five_way_ok = parity_match
        if five_way_evaluators:
            for path_name, eval_fn in five_way_evaluators.items():
                p_dec = eval_fn(ctx, model_id, direction)
                five_way_status[path_name] = str(p_dec.get("decision_fingerprint", ""))
                for key in compare_keys:
                    b_val = oracle_dec.get(key)
                    p_val = p_dec.get(key)
                    if isinstance(b_val, tuple):
                        b_val = list(b_val)
                    if isinstance(p_val, tuple):
                        p_val = list(p_val)
                    if b_val != p_val:
                        five_way_ok = False
                        parity_match = False
                        field_diffs[f"{path_name}.{key}"] = {"baseline": b_val, path_name: p_val}

        if parity_match:
            parity_matches += 1
        else:
            parity_divergences.append({
                "scenario_id": scen_id,
                "category": cat,
                "model_id": model_id,
                "direction": direction,
                "field_diffs": field_diffs,
            })
        if five_way_ok:
            five_way_matches += 1

        decision_records.append({
            "scenario_id": scen_id,
            "category": cat,
            "model_id": model_id,
            "direction": direction,
            "oracle_commit_sha": PRE_REFACTOR_BASELINE_COMMIT_SHA,
            "oracle_version": PRE_REFACTOR_ORACLE_VERSION,
            "input_snapshot_hash": getattr(ctx, "input_snapshot_hash", ""),
            "configuration_hash": getattr(ctx, "configuration_hash", ""),
            "decision_status": oracle_dec["decision_status"],
            "setup_validity": oracle_dec["setup_validity"],
            "entry_reference": oracle_dec["entry_reference"],
            "selected_sl": oracle_dec["selected_sl"],
            "legacy_sl": oracle_dec["legacy_sl"],
            "take_profit": oracle_dec["take_profit"],
            "unicorn_validity": oracle_dec["unicorn_validity"],
            "rejection_reason_codes": list(oracle_dec["rejection_reason_codes"]),
            "evidence_fingerprint": oracle_dec["evidence_fingerprint"],
            "policy_fingerprint": oracle_dec["policy_fingerprint"],
            "decision_fingerprint": oracle_dec["decision_fingerprint"],
            "parity_with_refactored": parity_match,
            "five_way_parity_match": five_way_ok,
            "five_way_fingerprints": five_way_status,
            "field_diffs": field_diffs,
        })

    jsonl_lines = [json.dumps(rec, sort_keys=True) for rec in decision_records]
    jsonl_content = "\n".join(jsonl_lines) + "\n"
    jsonl_bytes = jsonl_content.encode("utf-8")
    decisions_sha256 = hashlib.sha256(jsonl_bytes).hexdigest()

    manifest = {
        "release": "V17.9.0.1.1",
        "baseline_commit_sha": PRE_REFACTOR_BASELINE_COMMIT_SHA,
        "git_commit_object_verified": provenance_info["git_commit_object_verified"],
        "historical_file_sha256s": provenance_info["historical_file_sha256s"],
        "oracle_version": PRE_REFACTOR_ORACLE_VERSION,
        "oracle_module": "backend/services/v17_9_0_1_baseline_oracle.py",
        "oracle_class": "IndependentPreRefactorBaselineOracle",
        "self_referential": False,
        "imports_shared_strategy_evaluator": False,
        "total_scenarios": len(decision_records),
        "required_categories": list(REQUIRED_BASELINE_CATEGORIES),
        "category_counts": category_counts,
        "model_counts": model_counts,
        "parity_matches": parity_matches,
        "five_way_parity_matches": five_way_matches,
        "parity_divergence_count": len(parity_divergences),
        "parity_rate": round(parity_matches / max(1, len(decision_records)), 6),
        "line_ending_normalization": "LF_BYTE_EXACT",
        "baseline_decisions_sha256": decisions_sha256,
        "baseline_decisions_size_bytes": len(jsonl_bytes),
    }

    char_md = [
        "# V17.9.0.1.1 — Independent Pre-Refactor Baseline Characterization",
        "",
        f"- **Baseline Commit SHA (V17.8.0.4 Pre-V17.9.0):** `{PRE_REFACTOR_BASELINE_COMMIT_SHA}`",
        f"- **Git Commit Object Verified:** `{provenance_info['git_commit_object_verified']}`",
        f"- **Independent Oracle Module:** `backend/services/v17_9_0_1_baseline_oracle.py` (`IndependentPreRefactorBaselineOracle`)",
        "- **Self-Referential:** `False` (zero imports or calls to `SharedStrategyEvaluator`)",
        f"- **Total Scenarios Evaluated:** `{len(decision_records)}` (11 categories x 5 production models)",
        f"- **Exact Parity Matches with `SharedStrategyEvaluator`:** `{parity_matches}/{len(decision_records)}` (`{manifest['parity_rate'] * 100:.2f}%`)",
        f"- **5-Way Cross-Runtime Parity Matches:** `{five_way_matches}/{len(decision_records)}`",
        f"- **Baseline Decisions SHA-256 (LF byte-exact):** `{decisions_sha256}`",
        "",
        "## Category Coverage Matrix",
        "",
        "| Category | Scenarios | Parity Matches | Divergences |",
        "|---|---:|---:|---:|",
    ]
    for cat in REQUIRED_BASELINE_CATEGORIES:
        cat_recs = [r for r in decision_records if r["category"] == cat]
        cat_matches = sum(1 for r in cat_recs if r["parity_with_refactored"])
        char_md.append(f"| `{cat}` | {len(cat_recs)} | {cat_matches} | {len(cat_recs) - cat_matches} |")

    char_md.extend([
        "",
        "## Model Coverage Matrix",
        "",
        "| Production Model | Scenarios | Parity Matches | Divergences |",
        "|---|---:|---:|---:|",
    ])
    for m_id in SUPPORTED_BASELINE_MODELS:
        m_recs = [r for r in decision_records if r["model_id"] == m_id]
        m_matches = sum(1 for r in m_recs if r["parity_with_refactored"])
        char_md.append(f"| `{m_id}` | {len(m_recs)} | {m_matches} | {len(m_recs) - m_matches} |")
    char_md_text = "\n".join(char_md) + "\n"

    prov_lines = [
        "# V17.9.0.1.1 — Independent Baseline Provenance",
        "",
        "## 1. Baseline Source Provenance",
        f"- **Pre-Refactor Git Commit SHA:** `{PRE_REFACTOR_BASELINE_COMMIT_SHA}` (V17.8.0.4 — last verified commit prior to V17.9.0 shared evaluator extraction).",
        f"- **Git Commit Object Verified:** `{provenance_info['git_commit_object_verified']}`",
        "- **Recovery Mechanism:** Extracted the direct pre-V17.9.0 evaluation pipeline (`ScalpM1ModelService`, `cg01_engine.evaluate_cg01_candidate`, `unicorn_evidence_service.detect_unicorn_evidence`, `stop_placement_service.resolve_stop_anchor`, `stop_placement_service.plan_structural_stop`, `domain_calculator.calculate_risk_reward`) into `backend/services/v17_9_0_1_baseline_oracle.py` (`IndependentPreRefactorBaselineOracle`).",
        "- **Non-Self-Referential Guarantee:** `backend/services/v17_9_0_1_baseline_oracle.py` is AST-verified in regression tests to contain zero imports or invocations of `SharedStrategyEvaluator` or `shared_strategy_evaluator`.",
        "",
        "## 2. Historical Source File SHA-256 Digests at Commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`",
        "",
        "| Historical File | Size (Bytes) | SHA-256 |",
        "|---|---:|---|",
    ]
    for rel_p, meta_h in provenance_info["historical_file_sha256s"].items():
        prov_lines.append(f"| `{rel_p}` | {meta_h['size_bytes']} | `{meta_h['sha256']}` |")
    prov_lines.extend([
        "",
        "## 3. Frozen Artifact Integrity (LF Byte-Exact)",
        f"- `BASELINE_DECISIONS.jsonl` SHA-256: `{decisions_sha256}`",
        f"- `BASELINE_DECISIONS.jsonl` Size (Bytes): `{len(jsonl_bytes)}`",
        f"- Total Frozen Decisions: `{len(decision_records)}` across all 11 mandatory categories and 5 production models.",
        "",
    ])
    prov_md = "\n".join(prov_lines)

    if output_dirs:
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        char_bytes = char_md_text.encode("utf-8")
        prov_bytes = prov_md.encode("utf-8")
        for out_dir in output_dirs:
            out_path = Path(out_dir)
            out_path.mkdir(parents=True, exist_ok=True)
            (out_path / "BASELINE_MANIFEST.json").write_bytes(manifest_bytes)
            (out_path / "BASELINE_DECISIONS.jsonl").write_bytes(jsonl_bytes)
            (out_path / "BASELINE_CHARACTERIZATION.md").write_bytes(char_bytes)
            (out_path / "BASELINE_PROVENANCE.md").write_bytes(prov_bytes)
            disk_sha = hashlib.sha256((out_path / "BASELINE_DECISIONS.jsonl").read_bytes()).hexdigest()
            if disk_sha != decisions_sha256:
                raise RuntimeError(f"BASELINE_DECISIONS.jsonl checksum mismatch on disk: {disk_sha} != {decisions_sha256}")

    return {
        "manifest": manifest,
        "decisions": decision_records,
        "parity_divergences": parity_divergences,
        "provenance": provenance_info,
    }

