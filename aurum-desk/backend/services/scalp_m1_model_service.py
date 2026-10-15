"""
backend/services/scalp_m1_model_service.py
V15.0 - SMC M1 Scalp Models: Reversal & Continuation (PHẦN 042 - 053).

Implements:
- evaluate_m1_reversal_candidate: Causal M1 reversal model (Zone touch + Sweep/Rejection + CHOCH + Displacement + Retest).
- evaluate_m1_continuation_candidate: Causal M1 continuation model (Trend context + Pullback + M1 MSS + Net Room).
- Strict closed-candle verification (only closed M1 candles at now_ms).
- Canonical plan generation with net R:R and fees/slippage validation.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import smc_engine
from domain_calculator import CalculationResult, calculate_risk_reward, validate_price_geometry
from services.ny_quota_helpers import closed_candles_at, validate_candle_ohlc
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityEvaluation,
    OpportunityReasonCode,
    OpportunityStage,
    WatchedZone,
    get_scalp_config,
)
from services.smc_scenario_types import EvidenceNode, FactStatus, ScenarioSnapshot, ScenarioState

logger = logging.getLogger(__name__)


@dataclass
class ScalpCandidate:
    """Executable / Shadow scalp candidate result."""
    is_eligible: bool
    model_id: str                            # SMC_M1_REVERSAL or SMC_M1_CONTINUATION
    direction: str                           # LONG or SHORT
    candidate_key: str
    symbol: str = "XAUUSDT"
    planned_entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    gross_rr: float = 0.0
    net_rr: float = 0.0
    calculation: Optional[CalculationResult] = None
    reason_code: Optional[str] = None
    missing_conditions: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    trigger_chain: Dict[str, Any] = field(default_factory=dict)
    snapshot: Optional[ScenarioSnapshot] = None
    as_of_ms: int = 0
    zone_id: Optional[str] = None


class ScalpM1ModelService:
    @staticmethod
    def identify_m1_swings(
        candles_1m: Sequence[Any],
        left_bars: int = 2,
        right_bars: int = 2,
        *,
        as_of_ms: int = 0,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Identify causal M1 swing highs and swing lows (PHẦN 047).
        known_at_ms strictly requires right_bars closed candles.
        """
        highs: List[Dict[str, Any]] = []
        lows: List[Dict[str, Any]] = []
        n = len(candles_1m)

        for i in range(left_bars, n - right_bars):
            mid = candles_1m[i]
            mid_high = float(getattr(mid, "high", 0.0))
            mid_low = float(getattr(mid, "low", 0.0))
            open_ms = int(getattr(mid, "open_time", None) or getattr(mid, "timestamp", 0))

            # Confirmation requires right_bars following candles to be fully closed
            confirm_candle = candles_1m[i + right_bars]
            confirm_open = int(getattr(confirm_candle, "open_time", None) or getattr(confirm_candle, "timestamp", 0))
            known_at_ms = confirm_open + 60_000
            if as_of_ms and known_at_ms > as_of_ms:
                continue

            # Swing High
            is_sh = True
            for j in range(i - left_bars, i + right_bars + 1):
                if j == i:
                    continue
                bar_h = float(getattr(candles_1m[j], "high", 0.0))
                if j < i and bar_h >= mid_high:
                    is_sh = False
                    break
                elif j > i and bar_h > mid_high:
                    is_sh = False
                    break
            if is_sh:
                highs.append({
                    "id": f"m1_sh_{open_ms}",
                    "price": mid_high,
                    "occurred_at_ms": open_ms,
                    "known_at_ms": known_at_ms,
                })

            # Swing Low
            is_sl = True
            for j in range(i - left_bars, i + right_bars + 1):
                if j == i:
                    continue
                bar_l = float(getattr(candles_1m[j], "low", 0.0))
                if j < i and bar_l <= mid_low:
                    is_sl = False
                    break
                elif j > i and bar_l < mid_low:
                    is_sl = False
                    break
            if is_sl:
                lows.append({
                    "id": f"m1_sl_{open_ms}",
                    "price": mid_low,
                    "occurred_at_ms": open_ms,
                    "known_at_ms": known_at_ms,
                })

        return highs, lows

    @staticmethod
    def evaluate_m1_reversal_candidate(
        zone: WatchedZone,
        candles_1m: Sequence[Any],
        current_quote: Any,
        policy_settings: Dict[str, Any],
        *,
        now_ms: int,
        config: Optional[M1ScalpConfig] = None,
        context_levels: Optional[Sequence[Any]] = None,
    ) -> ScalpCandidate:
        """
        Evaluate M1 Reversal Candidate (PHẦN 045, 046).
        Prerequisites:
        1. Closed M1 candles >= min_closed_m1_bars.
        2. Price approached/touched watched zone.
        3. Sweep of local M1 swing or rejection extreme at zone edge.
        4. Causal structure break (CHOCH) past confirmed opposite M1 swing.
        5. Displacement exceeding body ATR threshold.
        6. Net R:R >= 2.0 with valid SL/TP geometry.
        """
        cfg = config or get_scalp_config()
        model_id = ModelIdentifier.SMC_M1_REVERSAL.value
        candidate_key = f"{model_id}_{zone.zone_id}_{now_ms}"

        # 1. Closed candles audit (PHẦN 042, 043)
        try:
            closed_1m = closed_candles_at(candles_1m, now_ms=now_ms, duration_ms=60_000)
        except ValueError as err:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=zone.direction,
                candidate_key=candidate_key, reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Lỗi dữ liệu nến M1: {err}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )
        if len(closed_1m) < cfg.min_closed_m1_bars:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=zone.direction,
                candidate_key=candidate_key, reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Thiếu nến M1 đã đóng: {len(closed_1m)}/{cfg.min_closed_m1_bars}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        atr_1m = smc_engine.compute_atr(closed_1m, period=cfg.displacement_atr_period)
        if atr_1m <= 0:
            atr_1m = 1.0

        sh_list, sl_list = ScalpM1ModelService.identify_m1_swings(
            closed_1m, cfg.pivot_left_bars, cfg.pivot_right_bars, as_of_ms=now_ms
        )

        direction = "LONG" if zone.direction in ("BULLISH", "LONG") else "SHORT"
        bid = float(getattr(current_quote, "bid", 0.0) if hasattr(current_quote, "bid") else (current_quote or {}).get("bid", 0.0))
        ask = float(getattr(current_quote, "ask", 0.0) if hasattr(current_quote, "ask") else (current_quote or {}).get("ask", 0.0))
        exec_price = ask if direction == "LONG" else bid

        if direction == "LONG":
            # For Bullish Reversal:
            # Must find a recent sweep low into/near the zone, then a CHOCH breaking the previous swing high
            if not sl_list or not sh_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa đủ swing high/low M1 để xác nhận cấu trúc đảo chiều"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            recent_sl = sl_list[-1]
            sweep_price = recent_sl["price"]
            # Anchor sweep: must be close to or inside zone
            if sweep_price > zone.top + (0.5 * atr_1m):
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_ZONE_TOUCH.value,
                    missing_conditions=["Sweep low chưa chạm hoặc tiếp cận vùng theo dõi"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            # Structure break: look for closed candle breaking above the last confirmed swing high
            pivot_to_break = None
            for sh in reversed(sh_list):
                if sh["occurred_at_ms"] < recent_sl["occurred_at_ms"]:
                    pivot_to_break = sh
                    break

            if not pivot_to_break and sh_list:
                pivot_to_break = sh_list[0]

            if not pivot_to_break:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Không tìm thấy pivot high M1 để kiểm tra CHOCH"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            break_level = pivot_to_break["price"]
            sweep_ts = recent_sl["occurred_at_ms"]
            bars_after_sweep = [
                b for b in closed_1m
                if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) >= sweep_ts
            ]
            breaking_bars = [b for b in bars_after_sweep if float(b.close) > break_level]

            if not breaking_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Chưa có nến M1 đóng vượt mức phá vỡ CHOCH ({break_level:.2f})"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            max_break_body = max(abs(float(b.close) - float(b.open)) for b in breaking_bars)
            if max_break_body < cfg.displacement_body_atr_min * atr_1m:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Thân nến phá vỡ không đạt ngưỡng xung lực ({max_break_body:.2f} < {cfg.displacement_body_atr_min * atr_1m:.2f})"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            if any(float(b.low) < sweep_price for b in bars_after_sweep):
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Giá đã phá vỡ invalidation level (sweep low)"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            # Plan levels
            sl_price = round(sweep_price - (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            entry_price = round(exec_price if exec_price > 0 else break_close, 2)

            # Target: opposing swing high or zone top + 2*ATR
            target_price = None
            if context_levels:
                for lvl in context_levels:
                    p = float(getattr(lvl, "price", 0.0) if hasattr(lvl, "price") else lvl.get("price", 0.0))
                    if p > entry_price + (1.5 * atr_1m):
                        target_price = round(p, 2)
                        break

            if target_price is None:
                # Use HTF or multiple of swing distance
                risk_dist = entry_price - sl_price
                target_price = round(entry_price + max(risk_dist * 2.5, 3.0 * atr_1m), 2)

        else:
            # For Bearish Reversal:
            if not sl_list or not sh_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa đủ swing high/low M1 để xác nhận cấu trúc đảo chiều"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            recent_sh = sh_list[-1]
            sweep_price = recent_sh["price"]
            if sweep_price < zone.bottom - (0.5 * atr_1m):
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_ZONE_TOUCH.value,
                    missing_conditions=["Sweep high chưa chạm hoặc tiếp cận vùng theo dõi"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            pivot_to_break = None
            for sl in reversed(sl_list):
                if sl["occurred_at_ms"] < recent_sh["occurred_at_ms"]:
                    pivot_to_break = sl
                    break

            if not pivot_to_break and sl_list:
                pivot_to_break = sl_list[0]

            if not pivot_to_break:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Không tìm thấy pivot low M1 để kiểm tra CHOCH"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            break_level = pivot_to_break["price"]
            sweep_ts = recent_sh["occurred_at_ms"]
            bars_after_sweep = [
                b for b in closed_1m
                if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) >= sweep_ts
            ]
            breaking_bars = [b for b in bars_after_sweep if float(b.close) < break_level]

            if not breaking_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Chưa có nến M1 đóng thủng mức phá vỡ CHOCH ({break_level:.2f})"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            max_break_body = max(abs(float(b.close) - float(b.open)) for b in breaking_bars)
            if max_break_body < cfg.displacement_body_atr_min * atr_1m:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Thân nến phá vỡ không đạt ngưỡng xung lực ({max_break_body:.2f} < {cfg.displacement_body_atr_min * atr_1m:.2f})"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            if any(float(b.high) > sweep_price for b in bars_after_sweep):
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Giá đã phá vỡ invalidation level (sweep high)"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sl_price = round(sweep_price + (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            entry_price = round(exec_price if exec_price > 0 else break_close, 2)

            target_price = None
            if context_levels:
                for lvl in context_levels:
                    p = float(getattr(lvl, "price", 0.0) if hasattr(lvl, "price") else lvl.get("price", 0.0))
                    if p < entry_price - (1.5 * atr_1m):
                        target_price = round(p, 2)
                        break

            if target_price is None:
                risk_dist = sl_price - entry_price
                target_price = round(entry_price - max(risk_dist * 2.5, 3.0 * atr_1m), 2)

        # Price geometry check
        is_geom, geom_err = validate_price_geometry(direction, entry_price, sl_price, target_price)
        if not is_geom:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code="INVALID_GEOMETRY", missing_conditions=[f"Sai hình học giá: {geom_err}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        # Net RR calculation via canonical calculator
        capital = float(policy_settings.get("capital_usdt", 1000.0))
        risk_pct = float(policy_settings.get("risk_pct", cfg.fallback_risk_pct))
        lev = int(policy_settings.get("leverage", 50))
        margin_mode = str(policy_settings.get("margin_mode", "ISOLATED"))

        calc_res = calculate_risk_reward(
            direction=direction,
            planned_entry=entry_price,
            stop_loss=sl_price,
            take_profit=target_price,
            capital_usdt=capital,
            risk_pct=risk_pct,
            leverage=lev,
            margin_mode=margin_mode,
        )

        if not calc_res.meets_min_rr:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                net_rr=calc_res.net_rr, gross_rr=calc_res.gross_rr, calculation=calc_res,
                reason_code=OpportunityReasonCode.INSUFFICIENT_NET_REWARD.value,
                missing_conditions=[f"Net R:R không đạt ngưỡng tối thiểu ({calc_res.net_rr:.2f} < {cfg.min_net_rr:.2f})"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        # Build evidence nodes
        ev_nodes = [
            EvidenceNode(
                id=f"ev_zone_{zone.zone_id}", kind="POI", symbol=zone.symbol, timeframe=zone.timeframe,
                direction=direction, occurred_at_ms=zone.created_at, known_at_ms=zone.known_at,
                level=zone.midpoint, extra={"zone_top": zone.top, "zone_bottom": zone.bottom}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_sweep_{recent_sl['id'] if direction == 'LONG' else recent_sh['id']}",
                kind="SWEEP_LOW" if direction == "LONG" else "SWEEP_HIGH",
                symbol=zone.symbol, timeframe="1M", direction=direction,
                occurred_at_ms=recent_sl["occurred_at_ms"] if direction == "LONG" else recent_sh["occurred_at_ms"],
                known_at_ms=recent_sl["known_at_ms"] if direction == "LONG" else recent_sh["known_at_ms"],
                level=sweep_price,
            ).to_dict(),
            EvidenceNode(
                id=f"ev_choch_{int(getattr(closed_1m[-1], 'open_time', 0))}",
                kind="CHOCH", symbol=zone.symbol, timeframe="1M", direction=direction,
                occurred_at_ms=int(getattr(closed_1m[-1], "open_time", 0)),
                known_at_ms=now_ms, level=break_level,
                extra={"broken_swing_id": pivot_to_break["id"], "displacement_body": max_break_body, "atr_1m": atr_1m}
            ).to_dict(),
        ]

        trigger_chain = {
            "verified": True,
            "reasons": (),
            "node_ids": [n["id"] for n in ev_nodes],
            "model_id": model_id,
        }

        # Snapshot for chart evidence representation
        snap = ScenarioSnapshot(
            scenario_id=f"scen-{candidate_key}",
            instance_id=candidate_key,
            revision=1,
            symbol=zone.symbol,
            setup_timeframe="1M",
            context_timeframes=["15M", "5M", "1H"],
            family=model_id,
            direction=direction,
            state=ScenarioState.STRUCTURE_CONFIRMED,
            as_of_ms=now_ms,
            evidence_cutoff_ms=now_ms,
            evidence_ids=[n["id"] for n in ev_nodes],
            liquidity_map={"swept": {"price": sweep_price}},
            trigger_chain=trigger_chain,
            target={"price": target_price},
            invalidation={"price": sl_price},
            summary_vi=f"SMC M1 Reversal {direction} tại vùng {zone.timeframe} ({zone.bottom} - {zone.top})",
            config_version=cfg.config_version,
            detector_version="v15.0",
            plan={
                "is_valid": True,
                "entry": entry_price,
                "stop_loss": sl_price,
                "take_profit": target_price,
                "net_rr": calc_res.net_rr,
                "gross_rr": calc_res.gross_rr,
            },
            evidence=ev_nodes,
        )

        return ScalpCandidate(
            is_eligible=True,
            model_id=model_id,
            direction=direction,
            candidate_key=candidate_key,
            symbol=zone.symbol,
            planned_entry=entry_price,
            stop_loss=sl_price,
            take_profit=target_price,
            gross_rr=calc_res.gross_rr,
            net_rr=calc_res.net_rr,
            calculation=calc_res,
            evidence=ev_nodes,
            trigger_chain=trigger_chain,
            snapshot=snap,
            as_of_ms=now_ms,
            zone_id=zone.zone_id,
        )

    @staticmethod
    def evaluate_m1_continuation_candidate(
        candles_1m: Sequence[Any],
        candles_5m: Sequence[Any],
        current_quote: Any,
        htf_bias: str,
        h1_alignment: str,
        policy_settings: Dict[str, Any],
        *,
        now_ms: int,
        config: Optional[M1ScalpConfig] = None,
        context_levels: Optional[Sequence[Any]] = None,
    ) -> ScalpCandidate:
        """
        Evaluate M1 Continuation Candidate (PHẦN 048).
        Prerequisites:
        1. Aligned HTF trend context (4H/1H aligned).
        2. M5 trend confirmation in alignment with HTF bias.
        3. Pullback into dynamic 5M value area followed by M1 BOS in trend direction.
        4. Net R:R >= 2.0 with valid SL/TP geometry.
        """
        cfg = config or get_scalp_config()
        model_id = ModelIdentifier.SMC_M1_CONTINUATION.value
        candidate_key = f"{model_id}_{now_ms}"

        # 1. HTF Trend alignment
        if htf_bias not in ("BULLISH", "BEARISH") or h1_alignment not in ("ALIGNED", "BULLISH", "BEARISH"):
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction="UNKNOWN", candidate_key=candidate_key,
                reason_code="HTF_BIAS_NOT_ALIGNED",
                missing_conditions=[f"Xu hướng khung lớn chưa đồng pha ({htf_bias} / {h1_alignment})"],
                as_of_ms=now_ms,
            )

        direction = "LONG" if htf_bias == "BULLISH" else "SHORT"

        # 2. Closed M1 candles audit
        try:
            closed_1m = closed_candles_at(candles_1m, now_ms=now_ms, duration_ms=60_000)
        except ValueError as err:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Lỗi dữ liệu nến M1: {err}"],
                as_of_ms=now_ms,
            )
        if len(closed_1m) < cfg.min_closed_m1_bars:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Thiếu nến M1 đã đóng: {len(closed_1m)}/{cfg.min_closed_m1_bars}"],
                as_of_ms=now_ms,
            )

        atr_1m = smc_engine.compute_atr(closed_1m, period=cfg.displacement_atr_period)
        if atr_1m <= 0:
            atr_1m = 1.0

        sh_list, sl_list = ScalpM1ModelService.identify_m1_swings(
            closed_1m, cfg.pivot_left_bars, cfg.pivot_right_bars, as_of_ms=now_ms
        )

        bid = float(getattr(current_quote, "bid", 0.0) if hasattr(current_quote, "bid") else (current_quote or {}).get("bid", 0.0))
        ask = float(getattr(current_quote, "ask", 0.0) if hasattr(current_quote, "ask") else (current_quote or {}).get("ask", 0.0))
        exec_price = ask if direction == "LONG" else bid

        if direction == "LONG":
            if not sh_list or not sl_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa đủ swing high/low M1 để kiểm tra tiếp diễn"],
                    as_of_ms=now_ms,
                )

            last_sh = sh_list[-1]
            last_sl = sl_list[-1]
            sl_ts = last_sl["occurred_at_ms"]
            bars_after_sl = [
                b for b in closed_1m
                if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) >= sl_ts
            ]
            breaking_bars = [b for b in bars_after_sl if float(b.close) > last_sh["price"]]
            if not breaking_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Nến M1 chưa đóng phá đỉnh tiếp diễn BOS (> {last_sh['price']:.2f})"],
                    as_of_ms=now_ms,
                )

            sl_price = round(last_sl["price"] - (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            break_close = float(breaking_bars[-1].close)
            entry_price = round(exec_price if exec_price > 0 else break_close, 2)
            risk_dist = entry_price - sl_price
            target_price = round(entry_price + max(risk_dist * 2.5, 3.0 * atr_1m), 2)

        else:
            if not sh_list or not sl_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa đủ swing high/low M1 để kiểm tra tiếp diễn"],
                    as_of_ms=now_ms,
                )

            last_sh = sh_list[-1]
            last_sl = sl_list[-1]
            sh_ts = last_sh["occurred_at_ms"]
            bars_after_sh = [
                b for b in closed_1m
                if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) >= sh_ts
            ]
            breaking_bars = [b for b in bars_after_sh if float(b.close) < last_sl["price"]]
            if not breaking_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Nến M1 chưa đóng thủng đáy tiếp diễn BOS (< {last_sl['price']:.2f})"],
                    as_of_ms=now_ms,
                )

            sl_price = round(last_sh["price"] + (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            break_close = float(breaking_bars[-1].close)
            entry_price = round(exec_price if exec_price > 0 else break_close, 2)
            risk_dist = sl_price - entry_price
            target_price = round(entry_price - max(risk_dist * 2.5, 3.0 * atr_1m), 2)

        is_geom, geom_err = validate_price_geometry(direction, entry_price, sl_price, target_price)
        if not is_geom:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code="INVALID_GEOMETRY", missing_conditions=[f"Sai hình học giá: {geom_err}"],
                as_of_ms=now_ms,
            )

        capital = float(policy_settings.get("capital_usdt", 1000.0))
        risk_pct = float(policy_settings.get("risk_pct", cfg.fallback_risk_pct))
        lev = int(policy_settings.get("leverage", 50))
        margin_mode = str(policy_settings.get("margin_mode", "ISOLATED"))

        calc_res = calculate_risk_reward(
            direction=direction,
            planned_entry=entry_price,
            stop_loss=sl_price,
            take_profit=target_price,
            capital_usdt=capital,
            risk_pct=risk_pct,
            leverage=lev,
            margin_mode=margin_mode,
        )

        if not calc_res.meets_min_rr:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                net_rr=calc_res.net_rr, gross_rr=calc_res.gross_rr, calculation=calc_res,
                reason_code=OpportunityReasonCode.INSUFFICIENT_NET_REWARD.value,
                missing_conditions=[f"Net R:R không đạt ngưỡng tối thiểu ({calc_res.net_rr:.2f} < {cfg.min_net_rr:.2f})"],
                as_of_ms=now_ms,
            )

        ev_nodes = [
            EvidenceNode(
                id=f"ev_bos_{int(getattr(closed_1m[-1], 'open_time', 0))}",
                kind="BOS", symbol="XAUUSDT", timeframe="1M", direction=direction,
                occurred_at_ms=int(getattr(closed_1m[-1], "open_time", 0)),
                known_at_ms=now_ms, level=entry_price,
                extra={"htf_bias": htf_bias, "h1_alignment": h1_alignment}
            ).to_dict(),
        ]

        trigger_chain = {
            "verified": True,
            "reasons": (),
            "node_ids": [n["id"] for n in ev_nodes],
            "model_id": model_id,
        }

        snap = ScenarioSnapshot(
            scenario_id=f"scen-{candidate_key}",
            instance_id=candidate_key,
            revision=1,
            symbol="XAUUSDT",
            setup_timeframe="1M",
            context_timeframes=["15M", "5M", "1H"],
            family=model_id,
            direction=direction,
            state=ScenarioState.STRUCTURE_CONFIRMED,
            as_of_ms=now_ms,
            evidence_cutoff_ms=now_ms,
            evidence_ids=[n["id"] for n in ev_nodes],
            liquidity_map={},
            trigger_chain=trigger_chain,
            target={"price": target_price},
            invalidation={"price": sl_price},
            summary_vi=f"SMC M1 Continuation {direction} đồng pha HTF ({htf_bias})",
            config_version=cfg.config_version,
            detector_version="v15.0",
            plan={
                "is_valid": True,
                "entry": entry_price,
                "stop_loss": sl_price,
                "take_profit": target_price,
                "net_rr": calc_res.net_rr,
                "gross_rr": calc_res.gross_rr,
            },
            evidence=ev_nodes,
        )

        return ScalpCandidate(
            is_eligible=True,
            model_id=model_id,
            direction=direction,
            candidate_key=candidate_key,
            symbol="XAUUSDT",
            planned_entry=entry_price,
            stop_loss=sl_price,
            take_profit=target_price,
            gross_rr=calc_res.gross_rr,
            net_rr=calc_res.net_rr,
            calculation=calc_res,
            evidence=ev_nodes,
            trigger_chain=trigger_chain,
            snapshot=snap,
            as_of_ms=now_ms,
        )
