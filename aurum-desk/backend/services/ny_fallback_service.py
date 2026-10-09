"""
Aurum Desk V7 - New York Session Paper Quota Fallback Engine
Deterministic candidate generator for NY quota fulfillment when standard SMC sweep is absent.
Label: NY_QUOTA_PAPER (Never fake STRICT_SMC).
"""
import time
from typing import List, Dict, Any, Optional, Tuple
from models import TradingPolicy
from domain_calculator import calculate_risk_reward, validate_price_geometry
from smc_engine import identify_pivots, detect_fvgs, compute_atr


class NYFallbackService:
    @staticmethod
    def evaluate_fallback_setup(
        symbol: str,
        candles_5m: list,
        current_price: float,
        htf_bias: str,
        h1_alignment: str,
        policy: TradingPolicy,
        account_equity: float,
        remaining_risk_allowance_usdt: float,
        leverage: int = 50,
        margin_mode: str = "ISOLATED",
        ticker_data: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
        """
        Deterministic Fallback v1 Model (NY_QUOTA_PAPER):
        1. Confirmed 4H Bias + H1 Aligned (Not Opposing/Ranging/Unknown).
        2. Closed 5M structure & active unmitigated FVG in direction.
        3. Real retest into 5M POI zone.
        4. Real liquidity target (nearest swing level), strictly Net RR >= 2.0.
        5. Effective risk capped at min(policy.ny_fallback_risk_pct_cap, budget %, 0.10%).
        6. Explicit evidence documenting missing sweep and rationale.
        """
        now_ms = int(time.time() * 1000)

        if len(candles_5m) < 15:
            return {
                "is_eligible": False,
                "strategy_family": "NY_QUOTA_PAPER",
                "reason_code": "FALLBACK_INSUFFICIENT_DATA",
                "missing_conditions": ["Cần tối thiểu 15 nến 5M để phân tích fallback"],
                "candidate": None
            }

        # 1. Bias & Alignment Check
        htf_bias_norm = (htf_bias or "UNKNOWN").upper()
        h1_align_norm = (h1_alignment or "UNKNOWN").upper()

        if htf_bias_norm not in ("BULLISH", "BEARISH"):
            return {
                "is_eligible": False,
                "strategy_family": "NY_QUOTA_PAPER",
                "reason_code": "FALLBACK_NO_ELIGIBLE_CANDIDATE",
                "missing_conditions": [f"HTF Bias ({htf_bias_norm}) không có hướng xác nhận rõ ràng"],
                "candidate": None
            }

        if h1_align_norm != "ALIGNED":
            return {
                "is_eligible": False,
                "strategy_family": "NY_QUOTA_PAPER",
                "reason_code": "FALLBACK_NO_ELIGIBLE_CANDIDATE",
                "missing_conditions": [f"Khung H1 ({h1_align_norm}) chưa đồng thuận với xu hướng"],
                "candidate": None
            }

        direction = "LONG" if htf_bias_norm == "BULLISH" else "SHORT"

        # 2. 5M Structure & Swings
        current_candle = candles_5m[-1]
        atr = compute_atr(candles_5m, 14)
        swing_highs, swing_lows = identify_pivots(candles_5m, "5M")
        recent_high = max(c.high for c in candles_5m[-15:])
        recent_low = min(c.low for c in candles_5m[-15:])
        last_sh = swing_highs[-1]["price"] if swing_highs else recent_high
        last_sl = swing_lows[-1]["price"] if swing_lows else recent_low

        # 3. 5M Active FVGs
        all_fvgs = detect_fvgs(candles_5m, "5M")
        fvg_type = "BULLISH_FVG" if direction == "LONG" else "BEARISH_FVG"
        active_fvgs = [f for f in all_fvgs if f["type"] == fvg_type and not f.get("mitigated", False)]

        if not active_fvgs:
            return {
                "is_eligible": False,
                "strategy_family": "NY_QUOTA_PAPER",
                "reason_code": "FALLBACK_NO_ELIGIBLE_CANDIDATE",
                "missing_conditions": ["Không tìm thấy vùng FVG/POI 5M hợp lệ cùng hướng"],
                "candidate": None
            }

        target_fvg = active_fvgs[-1]

        # 4. Retest Check
        in_fvg_zone = target_fvg["bottom"] <= current_price <= target_fvg["top"]
        if direction == "LONG":
            has_retested = in_fvg_zone or (current_candle.low <= target_fvg["top"] and current_price >= target_fvg["bottom"])
            if not has_retested:
                return {
                    "is_eligible": False,
                    "strategy_family": "NY_QUOTA_PAPER",
                    "reason_code": "FALLBACK_WAITING_RETRACE",
                    "missing_conditions": [f"Chờ giá retest vùng FVG 5M [{target_fvg['bottom']:.2f} - {target_fvg['top']:.2f}]"],
                    "candidate": None
                }
            # Levels
            sl = round(min(target_fvg["bottom"], last_sl) - 0.2 * atr, 2)
            tp = round(last_sh, 2)
        else:
            has_retested = in_fvg_zone or (current_candle.high >= target_fvg["bottom"] and current_price <= target_fvg["top"])
            if not has_retested:
                return {
                    "is_eligible": False,
                    "strategy_family": "NY_QUOTA_PAPER",
                    "reason_code": "FALLBACK_WAITING_RETRACE",
                    "missing_conditions": [f"Chờ giá retest vùng FVG 5M [{target_fvg['bottom']:.2f} - {target_fvg['top']:.2f}]"],
                    "candidate": None
                }
            # Levels
            sl = round(max(target_fvg["top"], last_sh) + 0.2 * atr, 2)
            tp = round(last_sl, 2)

        # 5. Price Geometry
        is_geom_valid, geom_err = validate_price_geometry(direction, current_price, sl, tp)
        if not is_geom_valid:
            return {
                "is_eligible": False,
                "strategy_family": "NY_QUOTA_PAPER",
                "reason_code": "INVALID_GEOMETRY",
                "missing_conditions": [f"Hình học giá không hợp lệ: {geom_err}"],
                "candidate": None
            }

        # 6. Sizing & Net RR calculation
        # Risk percentage cap: min(policy.ny_fallback_risk_pct_cap, budget %, 0.10%)
        budget_pct = (remaining_risk_allowance_usdt / account_equity * 100.0) if account_equity > 0 else 0.10
        effective_risk_pct = min(
            getattr(policy, 'ny_fallback_risk_pct_cap', 0.10) or 0.10,
            budget_pct,
            0.10
        )
        effective_risk_pct = round(max(effective_risk_pct, 0.01), 4)

        calc_res = calculate_risk_reward(
            direction=direction,
            planned_entry=current_price,
            stop_loss=sl,
            take_profit=tp,
            capital_usdt=account_equity,
            risk_pct=effective_risk_pct,
            min_net_rr=policy.min_net_rr or 2.0,
            leverage=leverage,
            margin_mode=margin_mode
        )

        if not calc_res.meets_min_rr:
            return {
                "is_eligible": False,
                "strategy_family": "NY_QUOTA_PAPER",
                "reason_code": "NET_RR_TOO_LOW",
                "missing_conditions": [
                    f"Mục tiêu thanh khoản 5M chỉ đạt Net R:R 1:{calc_res.net_rr:.2f} (Yêu cầu >= {policy.min_net_rr:.1f})"
                ],
                "candidate": None
            }

        if not calc_res.can_execute:
            return {
                "is_eligible": False,
                "strategy_family": "NY_QUOTA_PAPER",
                "reason_code": calc_res.reason_code or "CALCULATOR_REJECTED",
                "missing_conditions": [f"Tính toán khối lượng bị từ chối: {calc_res.reason_code}"],
                "candidate": None
            }

        # Deterministic Ranking Score (Heuristic ranking only, NOT probability)
        ranking_score = 70.0
        if calc_res.net_rr >= 2.5:
            ranking_score += 15.0
        elif calc_res.net_rr >= 2.0:
            ranking_score += 10.0
        if in_fvg_zone:
            ranking_score += 10.0

        evidence_snapshot = {
            "strategy_family": "NY_QUOTA_PAPER",
            "fallback_reason": "MISSING_STANDARD_SWEEP_FALLBACK_TO_5M_POI",
            "htf_bias": htf_bias_norm,
            "h1_alignment": h1_align_norm,
            "timeframe": "5M",
            "fvg_id": target_fvg.get("id"),
            "fvg_zone": [target_fvg.get("bottom"), target_fvg.get("top")],
            "invalidation_price": sl,
            "liquidity_target": tp,
            "ranking_score": ranking_score,
            "is_experimental": True,
            "evaluated_at": now_ms
        }

        candidate = {
            "id": f"fallback-{symbol}-5M-{current_candle.timestamp}",
            "setup_id": f"fallback-{current_candle.timestamp}",
            "signal_id": f"sig-fallback-{current_candle.timestamp}",
            "symbol": symbol,
            "instrument": symbol,
            "direction": direction,
            "strategy_family": "NY_QUOTA_PAPER",
            "strategy_version": "7.0.0-fallback",
            "state": "candidate",
            "planned_entry": calc_res.planned_entry,
            "actual_entry": None,
            "stop_loss": calc_res.stop_loss,
            "targets": [
                {
                    "price": calc_res.take_profit,
                    "close_fraction": 1.0,
                    "gross_rr": calc_res.gross_rr
                }
            ],
            "quantity": calc_res.quantity,
            "initial_risk_usdt": calc_res.net_risk_usdt,
            "risk_pct": effective_risk_pct,
            "requested_risk_pct": effective_risk_pct,
            "effective_risk_pct": effective_risk_pct,
            "risk_profile": "QUOTA",
            "gross_rr": calc_res.gross_rr,
            "estimated_net_rr": calc_res.net_rr,
            "fees_assumption": calc_res.fees_total_usdt,
            "slippage_assumption": calc_res.slippage_total_usdt,
            "leverage": calc_res.leverage,
            "margin_mode": calc_res.margin_mode,
            "estimated_liquidation": calc_res.estimated_liquidation,
            "initial_margin": calc_res.initial_margin_usdt,
            "created_at": now_ms,
            "expires_at": now_ms + (45 * 60 * 1000),  # 45 min expiry
            "evidence": evidence_snapshot,
            "ranking_score": ranking_score
        }

        return {
            "is_eligible": True,
            "strategy_family": "NY_QUOTA_PAPER",
            "reason_code": "FALLBACK_CANDIDATE_READY",
            "missing_conditions": [],
            "candidate": candidate
        }
