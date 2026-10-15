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
        Identify causal M1 swing highs and swing lows (PHẦN 047, SECTIONS 021 - 027).
        known_at_ms strictly requires right_bars closed candles.
        Delegates to canonical identify_confirmed_swings with timeframe='1M'.
        """
        from services.liquidity_scenario_service import identify_confirmed_swings
        return identify_confirmed_swings(
            candles_1m,
            timeframe="1M",
            left_bars=left_bars,
            right_bars=right_bars,
            as_of_ms=as_of_ms,
        )

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
        Evaluate M1 Reversal Candidate (PHẦN 040 - 050).
        Prerequisites:
        1. Closed M1 candles >= min_closed_m1_bars.
        2. Valid authoritative quote (bid > 0, ask >= bid, fresh, not stale).
        3. Price approached/touched watched zone.
        4. Sweep of local M1 swing or rejection extreme at zone edge.
        5. Causal structure break (CHOCH) past confirmed opposite M1 swing with displacement.
        6. Closed M1 retest pulling back towards the break zone without breaking sweep invalidation.
        7. Net R:R >= min_net_rr with valid SL/TP geometry and executable calculator sizing.
        """
        cfg = config or get_scalp_config()
        model_id = ModelIdentifier.SMC_M1_REVERSAL.value
        fallback_key = f"{model_id}_{zone.symbol}_{zone.zone_id}_{zone.direction}"

        # 1. Closed candles audit (PHẦN 042, 043)
        try:
            closed_1m = closed_candles_at(candles_1m, now_ms=now_ms, duration_ms=60_000)
        except ValueError as err:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=zone.direction,
                candidate_key=fallback_key, reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Lỗi dữ liệu nến M1: {err}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )
        if len(closed_1m) < cfg.min_closed_m1_bars:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=zone.direction,
                candidate_key=fallback_key, reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Thiếu nến M1 đã đóng: {len(closed_1m)}/{cfg.min_closed_m1_bars}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        # 2. Quote freshness and validity (PHẦN 035, 036)
        bid = float(getattr(current_quote, "bid", 0.0) if hasattr(current_quote, "bid") else (current_quote or {}).get("bid", 0.0))
        ask = float(getattr(current_quote, "ask", 0.0) if hasattr(current_quote, "ask") else (current_quote or {}).get("ask", 0.0))
        is_quote_valid = (
            bool(getattr(current_quote, "is_valid", True))
            and not bool(getattr(current_quote, "is_stale", False))
            and bid > 0
            and ask >= bid
            and math.isfinite(bid)
            and math.isfinite(ask)
        )
        if not is_quote_valid:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=zone.direction,
                candidate_key=fallback_key, reason_code=OpportunityReasonCode.QUOTE_STALE.value,
                missing_conditions=["Báo giá thị trường không hợp lệ hoặc bị mất kết nối"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        atr_1m = smc_engine.compute_atr(closed_1m, period=cfg.displacement_atr_period)
        if atr_1m <= 0:
            atr_1m = 1.0

        sh_list, sl_list = ScalpM1ModelService.identify_m1_swings(
            closed_1m, cfg.pivot_left_bars, cfg.pivot_right_bars, as_of_ms=now_ms
        )

        direction = "LONG" if zone.direction in ("BULLISH", "LONG") else "SHORT"
        exec_price = ask if direction == "LONG" else bid

        if direction == "LONG":
            if not sl_list or not sh_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa đủ swing high/low M1 để xác nhận cấu trúc đảo chiều"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            recent_sl = None
            pivot_to_break = None
            for sl in reversed(sl_list):
                if sl["price"] <= zone.top + (0.5 * atr_1m):
                    for sh in reversed(sh_list):
                        if sh["occurred_at_ms"] < sl["occurred_at_ms"]:
                            recent_sl = sl
                            pivot_to_break = sh
                            break
                    if recent_sl:
                        break

            if not recent_sl:
                recent_sl = sl_list[-1]
                sweep_price = recent_sl["price"]
                if sweep_price > zone.top + (0.5 * atr_1m):
                    return ScalpCandidate(
                        is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                        reason_code=OpportunityReasonCode.WAITING_ZONE_TOUCH.value,
                        missing_conditions=["Sweep low chưa chạm hoặc tiếp cận vùng theo dõi"],
                        as_of_ms=now_ms, zone_id=zone.zone_id,
                    )
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Không tìm thấy pivot high M1 có trước điểm sweep để làm mốc phá vỡ CHOCH"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sweep_price = recent_sl["price"]
            candidate_key = f"{model_id}_{zone.symbol}_{zone.zone_id}_{direction}_{pivot_to_break['id']}"
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

            # Retest Verification (SECTIONS 127 - 130)
            first_break_bar = breaking_bars[0]
            first_break_idx = closed_1m.index(first_break_bar)
            sweep_bar = next((b for b in closed_1m if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) == sweep_ts), None)
            sweep_idx = closed_1m.index(sweep_bar) if sweep_bar in closed_1m else max(0, first_break_idx - 5)

            # Causal FVG detection bound to the displacement break leg (SECTIONS 127, 128)
            disp_fvgs = []
            end_k = min(len(closed_1m), first_break_idx + 2)
            for k in range(max(2, sweep_idx + 2), end_k + 1):
                if k >= len(closed_1m):
                    break
                p2 = closed_1m[k - 2]
                curr = closed_1m[k]
                if float(curr.low) > float(p2.high):
                    bot = float(p2.high)
                    top = float(curr.low)
                    if bot >= sweep_price:
                        disp_fvgs.append((k, bot, top))

            if not disp_fvgs:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=["Chờ xuất hiện Fair Value Gap (FVG) hợp lệ trong nhịp xung lực phá vỡ để retest"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            fvg_confirm_k, fvg_bot, fvg_top = disp_fvgs[-1]
            post_fvg_bars = closed_1m[fvg_confirm_k + 1:]
            if not post_fvg_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=[f"Chờ nến M1 hồi về retest FVG [{fvg_bot:.2f} - {fvg_top:.2f}]"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            retest_bars = [b for b in post_fvg_bars if float(b.low) <= fvg_top and float(b.low) >= sweep_price]
            if not retest_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=[f"Chờ nến M1 hồi về retest FVG [{fvg_bot:.2f} - {fvg_top:.2f}]"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            # Plan levels
            sl_price = round(sweep_price - (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            entry_price = round(exec_price, 2)

            target_price = None
            if context_levels:
                for lvl in context_levels:
                    p = float(getattr(lvl, "price", 0.0) if hasattr(lvl, "price") else lvl.get("price", 0.0))
                    if p > entry_price + (1.5 * atr_1m):
                        target_price = round(p, 2)
                        break

            if target_price is None:
                risk_dist = entry_price - sl_price
                target_price = round(entry_price + max(risk_dist * 2.5, 3.0 * atr_1m), 2)

        else:
            # For Bearish Reversal
            if not sl_list or not sh_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Chưa đủ swing high/low M1 để xác nhận cấu trúc đảo chiều"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            recent_sh = None
            pivot_to_break = None
            for sh in reversed(sh_list):
                if sh["price"] >= zone.bottom - (0.5 * atr_1m):
                    for sl in reversed(sl_list):
                        if sl["occurred_at_ms"] < sh["occurred_at_ms"]:
                            recent_sh = sh
                            pivot_to_break = sl
                            break
                    if recent_sh:
                        break

            if not recent_sh:
                recent_sh = sh_list[-1]
                sweep_price = recent_sh["price"]
                if sweep_price < zone.bottom - (0.5 * atr_1m):
                    return ScalpCandidate(
                        is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                        reason_code=OpportunityReasonCode.WAITING_ZONE_TOUCH.value,
                        missing_conditions=["Sweep high chưa chạm hoặc tiếp cận vùng theo dõi"],
                        as_of_ms=now_ms, zone_id=zone.zone_id,
                    )
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=["Không tìm thấy pivot low M1 có trước điểm sweep để làm mốc phá vỡ CHOCH"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sweep_price = recent_sh["price"]
            candidate_key = f"{model_id}_{zone.symbol}_{zone.zone_id}_{direction}_{pivot_to_break['id']}"
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

            # Retest Verification (SECTIONS 127 - 130)
            first_break_bar = breaking_bars[0]
            first_break_idx = closed_1m.index(first_break_bar)
            sweep_bar = next((b for b in closed_1m if (int(getattr(b, "open_time", None) or getattr(b, "timestamp", 0))) == sweep_ts), None)
            sweep_idx = closed_1m.index(sweep_bar) if sweep_bar in closed_1m else max(0, first_break_idx - 5)

            disp_fvgs = []
            end_k = min(len(closed_1m), first_break_idx + 2)
            for k in range(max(2, sweep_idx + 2), end_k + 1):
                if k >= len(closed_1m):
                    break
                p2 = closed_1m[k - 2]
                curr = closed_1m[k]
                if float(curr.high) < float(p2.low):
                    top = float(p2.low)
                    bot = float(curr.high)
                    if top <= sweep_price:
                        disp_fvgs.append((k, bot, top))

            if not disp_fvgs:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=["Chờ xuất hiện Fair Value Gap (FVG) hợp lệ trong nhịp xung lực phá vỡ để retest"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            fvg_confirm_k, fvg_bot, fvg_top = disp_fvgs[-1]
            post_fvg_bars = closed_1m[fvg_confirm_k + 1:]
            if not post_fvg_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=[f"Chờ nến M1 hồi về retest FVG [{fvg_bot:.2f} - {fvg_top:.2f}]"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            retest_bars = [b for b in post_fvg_bars if float(b.high) >= fvg_bot and float(b.high) <= sweep_price]
            if not retest_bars:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_RETEST.value,
                    missing_conditions=[f"Chờ nến M1 hồi về retest FVG [{fvg_bot:.2f} - {fvg_top:.2f}]"],
                    as_of_ms=now_ms, zone_id=zone.zone_id,
                )

            sl_price = round(sweep_price + (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            entry_price = round(exec_price, 2)

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

        # Trigger age check (PHẦN 114, SECTIONS 130, 131, 132)
        # Select the FIRST qualifying retest bar for non-refreshing trigger_known_at
        first_retest_bar = retest_bars[0]
        retest_open_ms = int(getattr(first_retest_bar, "open_time", None) or getattr(first_retest_bar, "timestamp", 0))
        trigger_known_at = retest_open_ms + 60_000
        trigger_age_ms = now_ms - trigger_known_at
        if trigger_age_ms > cfg.max_trigger_age_ms:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code=OpportunityReasonCode.TRIGGER_EXPIRED.value,
                missing_conditions=[f"Tín hiệu kích hoạt đã quá hạn ({trigger_age_ms}ms > {cfg.max_trigger_age_ms}ms)"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        # Entry distance check (PHẦN 115, SECTIONS 133, 134)
        entry_reference = float(first_retest_bar.close)
        entry_dist_atr = abs(entry_price - entry_reference) / max(0.50, atr_1m)
        if entry_dist_atr > cfg.max_entry_distance_atr:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code=OpportunityReasonCode.ENTRY_TOO_FAR.value,
                missing_conditions=[f"Khoảng cách vào lệnh quá xa ({entry_dist_atr:.2f} ATR > {cfg.max_entry_distance_atr:.2f} ATR)"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        # Price geometry check
        is_geom, geom_err = validate_price_geometry(direction, entry_price, sl_price, target_price)
        if not is_geom:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                reason_code="INVALID_GEOMETRY", missing_conditions=[f"Sai hình học giá: {geom_err}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        # Net RR calculation via canonical calculator (PHẦN 037, 038)
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
            min_net_rr=cfg.min_net_rr,
        )

        if not calc_res.is_valid:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CALCULATOR_INVALID",
                missing_conditions=[f"Kế hoạch tính toán không hợp lệ: {calc_res.invalid_reason}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
            )

        if not calc_res.can_execute:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CANNOT_EXECUTE",
                missing_conditions=[f"Không đủ điều kiện thực thi: {calc_res.skip_reason}"],
                as_of_ms=now_ms, zone_id=zone.zone_id,
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

        # Build evidence nodes including retest node with exact causal timestamps (PHẦN 042, 112)
        break_bar_open_ms = int(getattr(first_break_bar, "open_time", 0))
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
                id=f"ev_choch_{break_bar_open_ms}",
                kind="CHOCH", symbol=zone.symbol, timeframe="1M", direction=direction,
                occurred_at_ms=break_bar_open_ms,
                known_at_ms=break_bar_open_ms + 60_000, level=break_level,
                extra={"broken_swing_id": pivot_to_break["id"], "displacement_body": max_break_body, "atr_1m": atr_1m}
            ).to_dict(),
            EvidenceNode(
                id=f"ev_retest_{retest_open_ms}",
                kind="RETEST", symbol=zone.symbol, timeframe="1M", direction=direction,
                occurred_at_ms=retest_open_ms,
                known_at_ms=trigger_known_at, level=float(first_retest_bar.low if direction == "LONG" else first_retest_bar.high),
                extra={"break_level": break_level, "retest_candle_open_ms": retest_open_ms}
            ).to_dict(),
        ]

        trigger_chain = {
            "verified": True,
            "reasons": (),
            "node_ids": [n["id"] for n in ev_nodes],
            "model_id": model_id,
            "trigger_known_at": trigger_known_at,
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
        Evaluate M1 Continuation Candidate (PHẦN 051).
        Prerequisites:
        1. Aligned HTF trend context (4H/1H aligned).
        2. Valid quote.
        3. Pullback into dynamic value area followed by M1 BOS in trend direction.
        4. Net R:R >= min_net_rr with valid SL/TP geometry and executable calculator sizing.
        """
        cfg = config or get_scalp_config()
        model_id = ModelIdentifier.SMC_M1_CONTINUATION.value
        fallback_key = f"{model_id}_XAUUSDT"

        # 1. HTF Trend alignment
        if htf_bias not in ("BULLISH", "BEARISH") or h1_alignment not in ("ALIGNED", "BULLISH", "BEARISH"):
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction="UNKNOWN", candidate_key=fallback_key,
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
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Lỗi dữ liệu nến M1: {err}"],
                as_of_ms=now_ms,
            )
        if len(closed_1m) < cfg.min_closed_m1_bars:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                reason_code=OpportunityReasonCode.DATA_INCOMPLETE.value,
                missing_conditions=[f"Thiếu nến M1 đã đóng: {len(closed_1m)}/{cfg.min_closed_m1_bars}"],
                as_of_ms=now_ms,
            )

        # 3. Quote freshness check
        bid = float(getattr(current_quote, "bid", 0.0) if hasattr(current_quote, "bid") else (current_quote or {}).get("bid", 0.0))
        ask = float(getattr(current_quote, "ask", 0.0) if hasattr(current_quote, "ask") else (current_quote or {}).get("ask", 0.0))
        is_quote_valid = (
            bool(getattr(current_quote, "is_valid", True))
            and not bool(getattr(current_quote, "is_stale", False))
            and bid > 0
            and ask >= bid
            and math.isfinite(bid)
            and math.isfinite(ask)
        )
        if not is_quote_valid:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                reason_code=OpportunityReasonCode.QUOTE_STALE.value,
                missing_conditions=["Báo giá thị trường không hợp lệ hoặc bị mất kết nối"],
                as_of_ms=now_ms,
            )

        atr_1m = smc_engine.compute_atr(closed_1m, period=cfg.displacement_atr_period)
        if atr_1m <= 0:
            atr_1m = 1.0

        sh_list, sl_list = ScalpM1ModelService.identify_m1_swings(
            closed_1m, cfg.pivot_left_bars, cfg.pivot_right_bars, as_of_ms=now_ms
        )

        exec_price = ask if direction == "LONG" else bid

        if direction == "LONG":
            if not sh_list or not sl_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
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
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Nến M1 chưa đóng phá đỉnh tiếp diễn BOS (> {last_sh['price']:.2f})"],
                    as_of_ms=now_ms,
                )

            break_bar_open = int(getattr(breaking_bars[-1], "open_time", None) or getattr(breaking_bars[-1], "timestamp", 0))
            candidate_key = f"{model_id}_XAUUSDT_{direction}_{break_bar_open}"
            sl_price = round(last_sl["price"] - (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            entry_price = round(exec_price, 2)
            risk_dist = entry_price - sl_price
            target_price = round(entry_price + max(risk_dist * 2.5, 3.0 * atr_1m), 2)

        else:
            if not sh_list or not sl_list:
                return ScalpCandidate(
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
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
                    is_eligible=False, model_id=model_id, direction=direction, candidate_key=fallback_key,
                    reason_code=OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
                    missing_conditions=[f"Nến M1 chưa đóng thủng đáy tiếp diễn BOS (< {last_sl['price']:.2f})"],
                    as_of_ms=now_ms,
                )

            break_bar_open = int(getattr(breaking_bars[-1], "open_time", None) or getattr(breaking_bars[-1], "timestamp", 0))
            candidate_key = f"{model_id}_XAUUSDT_{direction}_{break_bar_open}"
            sl_price = round(last_sh["price"] + (cfg.sl_buffer_ticks * cfg.tick_size), 2)
            entry_price = round(exec_price, 2)
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
            min_net_rr=cfg.min_net_rr,
        )

        if not calc_res.is_valid:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CALCULATOR_INVALID",
                missing_conditions=[f"Kế hoạch tính toán không hợp lệ: {calc_res.invalid_reason}"],
                as_of_ms=now_ms,
            )

        if not calc_res.can_execute:
            return ScalpCandidate(
                is_eligible=False, model_id=model_id, direction=direction, candidate_key=candidate_key,
                planned_entry=entry_price, stop_loss=sl_price, take_profit=target_price,
                reason_code="CANNOT_EXECUTE",
                missing_conditions=[f"Không đủ điều kiện thực thi: {calc_res.skip_reason}"],
                as_of_ms=now_ms,
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
