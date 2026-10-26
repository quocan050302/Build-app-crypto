"""
Aurum Desk V7/V14.3 - New York Session Paper Quota Fallback Engine
Deterministic candidate generator for NY quota fulfillment when standard SMC sweep is absent.
Label: NY_QUOTA_PAPER (Never fake STRICT_SMC).

V14.3: the generator is PURE (no DB, no network, no wall clock). All time comes from `now_ms`
(injected clock). Only candles closed at `now_ms` feed ATR / pivots / FVGs. The model is an
experimental FVG-retest fallback; it does NOT claim a sweep or a market-structure break.
"""
import math
from typing import List, Dict, Any, Optional
from models import TradingPolicy
from domain_calculator import calculate_risk_reward, validate_price_geometry
from smc_engine import identify_pivots, detect_fvgs, compute_atr
from services.clock import live_clock
from services.ny_quota_helpers import (
    FIVE_MIN_MS, FALLBACK_MODEL_VERSION, MIN_ATR_WARMUP_BARS,
    closed_candles_at, validate_candle_ohlc, effective_fallback_risk_pct,
    ny_setup_instance_id, ny_signal_id, fallback_expiry_ms, classify_reason,
)

SL_BUFFER_ATR_MULT = 0.2


def _fail(code: str, message: str, *, strategy_candidate: bool = False,
          structure: Optional[Dict[str, Any]] = None, diagnostics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "is_eligible": False,
        "strategy_family": "NY_QUOTA_PAPER",
        "reason_code": code,
        "reason_category": classify_reason(code)[0],
        "missing_conditions": [message],
        "candidate": None,
        # True when the structural setup (bias, FVG, retest, target) exists even if a financial /
        # execution check failed afterwards. Used by the funnel (candidates before safety).
        "strategy_candidate": strategy_candidate,
        "structure": structure,
        "diagnostics": diagnostics or {},
    }


def _quote_view(quote: Any, ticker_data: Optional[Dict[str, Any]], current_price: float, now_ms: int) -> Dict[str, Any]:
    """Normalize a ValidatedQuote / ticker dict / bare price into one view."""
    if quote is not None:
        bid, ask = getattr(quote, "bid", None), getattr(quote, "ask", None)
        valid = bool(getattr(quote, "is_valid", False))
        stale = bool(getattr(quote, "is_stale", False))
        ts = getattr(quote, "exchange_time", None) or getattr(quote, "observed_at", None)
        fresh = getattr(quote, "freshness_sec", None)
        code = getattr(quote, "rejection_code", None)
        source = "VALIDATED_QUOTE"
    elif ticker_data:
        from services.quote_validator import QuoteValidator
        q = QuoteValidator.validate_ticker(ticker_data, now_ms=now_ms)
        return _quote_view(q, None, current_price, now_ms)
    else:
        px = current_price
        return {"valid": bool(px and math.isfinite(px) and px > 0), "bid": px, "ask": px, "timestamp": now_ms,
                "age_ms": 0, "source": "PARAM_PRICE", "stale": False, "code": None}
    return {
        "valid": valid and bid is not None and ask is not None and math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask >= bid,
        "bid": bid, "ask": ask, "timestamp": ts,
        "age_ms": int(round(fresh * 1000)) if fresh is not None and math.isfinite(fresh) else None,
        "source": source, "stale": stale, "code": code,
    }


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
        ticker_data: Optional[Dict[str, float]] = None,
        *,
        now_ms: Optional[int] = None,
        clock: Any = None,
        window: Optional[Dict[str, Any]] = None,
        quote: Any = None,
        requested_risk_pct: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Deterministic Fallback v1 Model (NY_QUOTA_PAPER):
        1. Confirmed 4H Bias + H1 Aligned (Not Opposing/Ranging/Unknown).
        2. Closed 5M bars only; active unmitigated FVG in direction (confirmed before the retest).
        3. Real retest into the 5M POI zone (tick quote after FVG confirmation, or a closed-bar wick).
        4. Real liquidity target = confirmed swing pivot on the correct side of entry.
        5. Effective risk = min(0.10%, policy cap, requested risk, remaining budget %); budget 0 -> no candidate.
        6. Explicit evidence documenting missing sweep / experimental label.
        """
        if now_ms is None:
            now_ms = (clock or live_clock).now_ms()
        now_ms = int(now_ms)
        window_end_ms = (window or {}).get("window_end_ms")
        session_id = (window or {}).get("session_instance_id") or "NY-UNKNOWN"
        diagnostics: Dict[str, Any] = {"now_ms": now_ms, "model_version": FALLBACK_MODEL_VERSION}

        # ---- data: closed bars only, finite OHLC, ATR warmup --------------------------------
        try:
            closed = closed_candles_at(candles_5m, now_ms=now_ms, duration_ms=FIVE_MIN_MS)
        except ValueError as e:
            return _fail("DATA_INVALID_CANDLE", f"Dữ liệu nến 5M không hợp lệ: {e}", diagnostics={**diagnostics, "data_issue": str(e)})
        diagnostics["closed_bars"] = len(closed)
        diagnostics["closed_bar_cutoff_ms"] = closed[-1].timestamp + FIVE_MIN_MS if closed else None
        if len(closed) < MIN_ATR_WARMUP_BARS:
            return _fail("FALLBACK_DATA_MISSING",
                         f"Cần tối thiểu {MIN_ATR_WARMUP_BARS} nến 5M đã đóng (hiện có {len(closed)})",
                         diagnostics=diagnostics)
        bad = validate_candle_ohlc(closed)
        if bad:
            return _fail(bad, "Có nến 5M OHLC không hữu hạn hoặc không nhất quán", diagnostics={**diagnostics, "data_issue": bad})

        # ---- 1. Bias & alignment ------------------------------------------------------------
        htf_bias_norm = (htf_bias or "UNKNOWN").upper()
        h1_align_norm = (h1_alignment or "UNKNOWN").upper()
        if htf_bias_norm not in ("BULLISH", "BEARISH"):
            return _fail("FALLBACK_HTF_NOT_CONFIRMED", f"HTF Bias ({htf_bias_norm}) không có hướng xác nhận rõ ràng",
                         diagnostics=diagnostics)
        if h1_align_norm != "ALIGNED":
            return _fail("FALLBACK_H1_NOT_ALIGNED", f"Khung H1 ({h1_align_norm}) chưa đồng thuận với xu hướng",
                         diagnostics=diagnostics)
        direction = "LONG" if htf_bias_norm == "BULLISH" else "SHORT"

        # ---- 2. structure from CLOSED bars only ---------------------------------------------
        last_closed = closed[-1]
        last_close_ms = last_closed.timestamp + FIVE_MIN_MS
        atr = compute_atr(closed, 14)
        swing_highs, swing_lows = identify_pivots(closed, "5M")
        swing_highs = [s for s in swing_highs if s.get("confirmed_at", 0) <= now_ms]
        swing_lows = [s for s in swing_lows if s.get("confirmed_at", 0) <= now_ms]
        diagnostics["atr"] = atr

        fvg_type = "BULLISH_FVG" if direction == "LONG" else "BEARISH_FVG"
        active_fvgs = [f for f in detect_fvgs(closed, "5M")
                       if f["type"] == fvg_type and not f.get("mitigated", False) and f.get("confirmed_at", 0) <= now_ms]
        if not active_fvgs:
            return _fail("FALLBACK_NO_FVG", "Không tìm thấy vùng FVG 5M đã đóng, chưa lấp, cùng hướng", diagnostics=diagnostics)
        fvg = active_fvgs[-1]
        top, bottom = fvg["top"], fvg["bottom"]

        # ---- 3. Retest: only an FVG that already existed (confirmed) before the retest -------
        q = _quote_view(quote, ticker_data, current_price, now_ms)
        diagnostics["quote"] = {k: q[k] for k in ("valid", "bid", "ask", "timestamp", "age_ms", "source")}
        px_exec = (q["ask"] if direction == "LONG" else q["bid"]) if q["valid"] else None
        ref_px = px_exec if px_exec is not None else last_closed.close
        retest_time = None
        retest_source = None
        if px_exec is not None and q["timestamp"] is not None and fvg["confirmed_at"] < q["timestamp"] <= now_ms:
            if bottom <= px_exec <= top:
                retest_time, retest_source = int(q["timestamp"]), "TICK_IN_ZONE"
        if retest_source is None and last_closed.timestamp >= fvg["confirmed_at"]:
            if direction == "LONG":
                wick = last_closed.low <= top and ref_px >= bottom
            else:
                wick = last_closed.high >= bottom and ref_px <= top
            if wick:
                retest_time, retest_source = int(last_close_ms), "CLOSED_BAR_WICK"
        if retest_source is None:
            return _fail("FALLBACK_WAITING_RETRACE", f"Chờ giá retest vùng FVG 5M [{bottom:.2f} - {top:.2f}]",
                         diagnostics=diagnostics)

        # ---- 4. Target = confirmed pivot on the correct side of entry -----------------------
        entry_ref = px_exec if px_exec is not None else last_closed.close
        last_swing_low = swing_lows[-1]["price"] if swing_lows else None
        last_swing_high = swing_highs[-1]["price"] if swing_highs else None
        if direction == "LONG":
            sl_base = min(bottom, last_swing_low) if last_swing_low is not None else bottom
            sl = round(sl_base - SL_BUFFER_ATR_MULT * atr, 2)
            side = [s for s in swing_highs if s["price"] > entry_ref]
        else:
            sl_base = max(top, last_swing_high) if last_swing_high is not None else top
            sl = round(sl_base + SL_BUFFER_ATR_MULT * atr, 2)
            side = [s for s in swing_lows if s["price"] < entry_ref]
        if not side:
            return _fail("FALLBACK_NO_LIQUIDITY_TARGET",
                         "Chưa có đỉnh/đáy 5M đã xác nhận ở phía mục tiêu của lệnh", diagnostics=diagnostics)
        target_pivot = side[-1]
        tp = round(target_pivot["price"], 2)

        instance_id = ny_setup_instance_id(session_id, symbol, direction, fvg["id"])
        structure = {
            "direction": direction, "entry_ref": entry_ref, "stop_loss": sl, "take_profit": tp,
            "fvg_id": fvg["id"], "fvg_zone": [bottom, top], "fvg_confirmed_at": fvg["confirmed_at"],
            "retest_time": retest_time, "retest_source": retest_source, "instance_id": instance_id,
            "target_pivot_id": target_pivot.get("id"),
        }

        is_geom_valid, geom_err = validate_price_geometry(direction, entry_ref, sl, tp)
        if not is_geom_valid:
            return _fail("INVALID_GEOMETRY", f"Hình học giá không hợp lệ: {geom_err}",
                         strategy_candidate=True, structure=structure, diagnostics=diagnostics)

        # ---- 5. Money: quote required, budget > 0, calculator is the only RR/size source ----
        if not q["valid"]:
            code = "QUOTE_STALE" if q.get("stale") else "QUOTE_UNAVAILABLE"
            return _fail(code, "Chưa có báo giá hợp lệ để lập kế hoạch vào lệnh",
                         strategy_candidate=True, structure=structure, diagnostics=diagnostics)

        min_rr = getattr(policy, "min_net_rr", None)
        if min_rr is None or not math.isfinite(min_rr) or min_rr <= 0:
            return _fail("CALCULATOR_REJECTED", "Policy min_net_rr không hợp lệ",
                         strategy_candidate=True, structure=structure, diagnostics=diagnostics)
        cap = getattr(policy, "ny_fallback_risk_pct_cap", None)
        requested = requested_risk_pct if requested_risk_pct is not None else cap
        try:
            effective_risk_pct = effective_fallback_risk_pct(
                equity=float(account_equity), remaining_usdt=float(remaining_risk_allowance_usdt),
                configured_cap_pct=float(cap if cap is not None else float("nan")),
                requested_risk_pct=float(requested if requested is not None else float("nan")))
        except ValueError:
            return _fail("RISK_BUDGET_DATA_UNAVAILABLE", "Dữ liệu ngân sách rủi ro không hợp lệ",
                         strategy_candidate=True, structure=structure, diagnostics=diagnostics)
        if effective_risk_pct <= 0:
            return _fail("NO_REMAINING_RISK", "Hết ngân sách rủi ro còn lại cho lệnh fallback",
                         strategy_candidate=True, structure=structure, diagnostics=diagnostics)

        calc_res = calculate_risk_reward(
            direction=direction, planned_entry=entry_ref, stop_loss=sl, take_profit=tp,
            capital_usdt=account_equity, risk_pct=effective_risk_pct, min_net_rr=min_rr,
            leverage=leverage, margin_mode=margin_mode, now_ms=now_ms,
        )
        diagnostics["calc"] = {"net_rr": calc_res.net_rr, "skip_reason": calc_res.skip_reason,
                               "invalid_reason": calc_res.invalid_reason, "can_execute": calc_res.can_execute}
        if not calc_res.is_valid:
            return _fail("INVALID_GEOMETRY", f"Hình học giá không hợp lệ: {calc_res.invalid_reason}",
                         strategy_candidate=True, structure=structure, diagnostics=diagnostics)
        if not calc_res.meets_min_rr:
            if calc_res.can_execute is False and calc_res.skip_reason and calc_res.net_rr <= 0:
                reason = str(calc_res.skip_reason)
                return _fail("CALCULATOR_REJECTED", f"Tính toán khối lượng bị từ chối: {reason}",
                             strategy_candidate=True, structure=structure, diagnostics=diagnostics)
            return _fail("NET_RR_TOO_LOW",
                         f"Mục tiêu thanh khoản 5M chỉ đạt Net R:R 1:{calc_res.net_rr:.2f} (Yêu cầu >= {min_rr:.1f})",
                         strategy_candidate=True, structure=structure, diagnostics=diagnostics)
        if not calc_res.can_execute:
            reason = str(calc_res.skip_reason or calc_res.invalid_reason or "CALCULATOR_REJECTED")
            return _fail("CALCULATOR_REJECTED", f"Tính toán khối lượng bị từ chối: {reason}",
                         strategy_candidate=True, structure=structure, diagnostics=diagnostics)

        # ---- candidate -----------------------------------------------------------------------
        ranking_score = 70.0
        if calc_res.net_rr >= 2.5:
            ranking_score += 15.0
        elif calc_res.net_rr >= 2.0:
            ranking_score += 10.0
        if retest_source == "TICK_IN_ZONE":
            ranking_score += 10.0

        expires_at = fallback_expiry_ms(now_ms, window_end_ms) if window_end_ms else now_ms + 45 * 60 * 1000
        signal_id = ny_signal_id(instance_id)
        evidence_snapshot = {
            "strategy_family": "NY_QUOTA_PAPER",
            "model_version": FALLBACK_MODEL_VERSION,
            "structure_label": "FVG_RETEST_FALLBACK",
            "fallback_reason": "MISSING_STANDARD_SWEEP_FALLBACK_TO_5M_POI",
            "sweep": "ABSENT",
            "structure_chain_verified": False,
            "htf_bias": htf_bias_norm,
            "h1_alignment": h1_align_norm,
            "timeframe": "5M",
            "session_instance_id": session_id,
            "instance_id": instance_id,
            "fvg_id": fvg["id"],
            "fvg_zone": [bottom, top],
            "fvg_confirmed_at": fvg["confirmed_at"],
            "retest": {"time": retest_time, "source": retest_source,
                       "quote_timestamp": q["timestamp"], "quote_age_ms": q["age_ms"]},
            "liquidity_target": {"price": tp, "pivot_id": target_pivot.get("id"),
                                 "confirmed_at": target_pivot.get("confirmed_at")},
            "atr": {"value": atr, "period": 14, "sl_buffer_mult": SL_BUFFER_ATR_MULT, "bars": len(closed)},
            "closed_bar_cutoff_ms": last_close_ms,
            "last_closed_bar_ts": last_closed.timestamp,
            "invalidation_price": sl,
            "liquidity_target_price": tp,
            "ranking_score": ranking_score,
            "is_experimental": True,
            "evaluated_at": now_ms,
        }

        candidate = {
            "id": f"fallback-{symbol}-5M-{instance_id}"[:50],
            "setup_id": instance_id,
            "instance_id": instance_id,
            "signal_id": signal_id,
            "symbol": symbol,
            "instrument": symbol,
            "direction": direction,
            "strategy_family": "NY_QUOTA_PAPER",
            "strategy_version": "7.0.0-fallback",
            "state": "candidate",
            "planned_entry": calc_res.planned_entry,
            "actual_entry": None,
            "stop_loss": calc_res.stop_loss,
            "invalidation_price": calc_res.stop_loss,
            "invalidation_reason": f"Giá phá mức SL của cấu trúc fallback M5 đã xác nhận (FVG {fvg['id']})",
            "targets": [{"price": calc_res.take_profit, "close_fraction": 1.0, "gross_rr": calc_res.gross_rr}],
            "quantity": calc_res.quantity,
            "initial_risk_usdt": calc_res.net_risk_usdt,
            "risk_pct": effective_risk_pct,
            "requested_risk_pct": requested if requested is not None else effective_risk_pct,
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
            "expires_at": expires_at,
            "htf_bias": htf_bias_norm,
            "h1_alignment": h1_align_norm,
            "evidence": evidence_snapshot,
            "ranking_score": ranking_score,
        }

        return {
            "is_eligible": True,
            "strategy_family": "NY_QUOTA_PAPER",
            "reason_code": "FALLBACK_CANDIDATE_READY",
            "reason_category": "STRATEGY",
            "missing_conditions": [],
            "candidate": candidate,
            "strategy_candidate": True,
            "structure": structure,
            "diagnostics": diagnostics,
        }
