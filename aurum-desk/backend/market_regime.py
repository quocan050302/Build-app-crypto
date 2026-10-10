"""
Aurum Desk V13 — Causal Market State & Regime Evaluator.
Evaluates pure price-action and structural regime at a given as_of timestamp:
- TREND_UP / TREND_DOWN / RANGE / TRANSITION / EVENT_VOLATILITY / UNKNOWN
- Normalized by ATR(14)
- Provides supporting and conflicting evidence in Vietnamese
"""

from typing import Dict, Any, List, Optional
import math


class MarketRegimeType:
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    TRANSITION = "TRANSITION"
    EVENT_VOLATILITY = "EVENT_VOLATILITY"
    UNKNOWN = "UNKNOWN"


def get_vietnamese_regime_label(regime: str) -> str:
    labels = {
        MarketRegimeType.TREND_UP: "Xu Hướng Tăng Mạnh (Bullish Trend)",
        MarketRegimeType.TREND_DOWN: "Xu Hướng Giảm Rõ Rệt (Bearish Trend)",
        MarketRegimeType.RANGE: "Đi Ngang Tích Lũy (Range-Bound / Choppy)",
        MarketRegimeType.TRANSITION: "Giai Đoạn Chuyển Giao (Transition / Reversal)",
        MarketRegimeType.EVENT_VOLATILITY: "Biến Động Bất Thường (High Event Volatility)",
        MarketRegimeType.UNKNOWN: "Chưa Rõ Ràng / Thiếu Dữ Liệu (Unknown / Insufficient Data)"
    }
    return labels.get(regime, regime)


def evaluate_market_regime(
    candles_15m: List[Dict[str, Any]],
    candles_1h: Optional[List[Dict[str, Any]]] = None,
    atr_val: Optional[float] = None
) -> Dict[str, Any]:
    """
    Pure causal market state evaluator based on available closed candles up to as_of.
    """
    if not candles_15m or len(candles_15m) < 20:
        return {
            "regime": MarketRegimeType.UNKNOWN,
            "label_vi": get_vietnamese_regime_label(MarketRegimeType.UNKNOWN),
            "confidence_score": 0.20,
            "supporting_evidence": ["Không đủ nến lịch sử tối thiểu (cần >= 20 nến 15M)"],
            "conflicting_evidence": [],
            "regime_version": "v13.0",
            "setup_eligibility": {
                "b1_trend_continuation": False,
                "b2_range_breakout": False,
                "recommended_stance": "QUAN_SÁT"
            }
        }

    closes = [c["close"] for c in candles_15m]
    highs = [c["high"] for c in candles_15m]
    lows = [c["low"] for c in candles_15m]

    curr_close = closes[-1]

    # Calculate 14-period ATR if not provided
    if atr_val is None or atr_val <= 0:
        trs = []
        for i in range(1, min(len(candles_15m), 15)):
            tr = max(
                highs[-i] - lows[-i],
                abs(highs[-i] - closes[-i - 1]),
                abs(lows[-i] - closes[-i - 1])
            )
            trs.append(tr)
        atr_val = sum(trs) / len(trs) if trs else 5.0

    # 1. Check for abnormal volatility (Event Volatility)
    recent_candle_ranges = [highs[-i] - lows[-i] for i in range(1, min(len(candles_15m), 4))]
    max_recent_range = max(recent_candle_ranges) if recent_candle_ranges else 0.0
    if max_recent_range >= 2.8 * atr_val:
        return {
            "regime": MarketRegimeType.EVENT_VOLATILITY,
            "label_vi": get_vietnamese_regime_label(MarketRegimeType.EVENT_VOLATILITY),
            "confidence_score": 0.85,
            "supporting_evidence": [
                f"Phát hiện nến biến động cực đại {max_recent_range:.2f} USD (vượt 2.8x ATR {atr_val:.2f})",
                "Thị trường vừa hấp thụ tin tức mạnh hoặc biến động bất thường, giãn spread cao"
            ],
            "conflicting_evidence": ["Cấu trúc kỹ thuật có thể bị phá vỡ giả bởi tin tức"],
            "regime_version": "v13.0",
            "setup_eligibility": {
                "b1_trend_continuation": False,
                "b2_range_breakout": False,
                "recommended_stance": "ĐỨNG_NGOÀI_BẢO_TOÀN_VỐN"
            }
        }

    # 2. Compute 20 EMA and 50 EMA on closes
    def calc_ema(series, period):
        k = 2.0 / (period + 1)
        ema = series[0]
        for val in series[1:]:
            ema = (val * k) + (ema * (1 - k))
        return ema

    ema20 = calc_ema(closes[-30:], 20) if len(closes) >= 30 else sum(closes[-20:]) / 20.0
    ema50 = calc_ema(closes[-50:], 50) if len(closes) >= 50 else sum(closes[-30:]) / 30.0

    # 3. Detect recent swings (last 20 bars)
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    range_span = recent_high - recent_low
    equilibrium = (recent_high + recent_low) / 2.0

    supporting = []
    conflicting = []

    # Check for Range Bound condition
    if range_span <= 3.5 * atr_val and abs(curr_close - equilibrium) <= 0.6 * atr_val:
        supporting.append(f"Biên độ 20 nến hẹp ({range_span:.2f} USD ~ {range_span/atr_val:.1f}x ATR)")
        supporting.append(f"Giá đang dao động quanh vùng cân bằng Equilibrium ${equilibrium:.2f}")
        return {
            "regime": MarketRegimeType.RANGE,
            "label_vi": get_vietnamese_regime_label(MarketRegimeType.RANGE),
            "confidence_score": 0.78,
            "supporting_evidence": supporting,
            "conflicting_evidence": conflicting,
            "regime_version": "v13.0",
            "setup_eligibility": {
                "b1_trend_continuation": False,
                "b2_range_breakout": True,
                "recommended_stance": "CHỜ_BREAKOUT_HOẶC_ĐÁNH_BIÊN"
            }
        }

    # Check for Uptrend
    if curr_close > ema20 and ema20 > ema50:
        supporting.append(f"Giá (${curr_close:.2f}) nằm trên cả EMA20 (${ema20:.2f}) và EMA50 (${ema50:.2f})")
        supporting.append(f"Đường EMA20 đang dốc lên cao hơn EMA50, xác nhận xu hướng tăng")
        if curr_close > equilibrium:
            supporting.append(f"Giá nằm ở nửa trên Dealing Range (trên Equilibrium ${equilibrium:.2f})")
        else:
            conflicting.append("Giá đang hồi sâu về vùng Discount dưới Equilibrium")

        return {
            "regime": MarketRegimeType.TREND_UP,
            "label_vi": get_vietnamese_regime_label(MarketRegimeType.TREND_UP),
            "confidence_score": 0.82 if not conflicting else 0.68,
            "supporting_evidence": supporting,
            "conflicting_evidence": conflicting,
            "regime_version": "v13.0",
            "setup_eligibility": {
                "b1_trend_continuation": True,
                "b2_range_breakout": False,
                "recommended_stance": "TÌM_KIẾM_LONG_PULLBACK"
            }
        }

    # Check for Downtrend
    if curr_close < ema20 and ema20 < ema50:
        supporting.append(f"Giá (${curr_close:.2f}) nằm dưới cả EMA20 (${ema20:.2f}) và EMA50 (${ema50:.2f})")
        supporting.append(f"Đường EMA20 đang dốc xuống thấp hơn EMA50, xác nhận xu hướng giảm")
        if curr_close < equilibrium:
            supporting.append(f"Giá nằm ở nửa dưới Dealing Range (dưới Equilibrium ${equilibrium:.2f})")
        else:
            conflicting.append("Giá đang hồi lên vùng Premium trên Equilibrium")

        return {
            "regime": MarketRegimeType.TREND_DOWN,
            "label_vi": get_vietnamese_regime_label(MarketRegimeType.TREND_DOWN),
            "confidence_score": 0.82 if not conflicting else 0.68,
            "supporting_evidence": supporting,
            "conflicting_evidence": conflicting,
            "regime_version": "v13.0",
            "setup_eligibility": {
                "b1_trend_continuation": True,
                "b2_range_breakout": False,
                "recommended_stance": "TÌM_KIẾM_SHORT_PULLBACK"
            }
        }

    # Otherwise Transition / Mixed
    supporting.append("Đường EMA20 và EMA50 cắt nhau hoặc giá đang dao động qua lại giữa 2 đường EMA")
    conflicting.append("Chưa hình thành cấu trúc đỉnh đáy đồng thuận theo một hướng duy nhất")
    return {
        "regime": MarketRegimeType.TRANSITION,
        "label_vi": get_vietnamese_regime_label(MarketRegimeType.TRANSITION),
        "confidence_score": 0.60,
        "supporting_evidence": supporting,
        "conflicting_evidence": conflicting,
        "regime_version": "v13.0",
        "setup_eligibility": {
            "b1_trend_continuation": False,
            "b2_range_breakout": False,
            "recommended_stance": "CHỜ_XÁC_NHẬN_PHÁ_VỠ_CHOCH"
        }
    }
