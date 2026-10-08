from typing import List, Dict, Any, Optional, Tuple

class VolumeAnalyzer:
    """
    RVOL (Relative Volume) and Volume Profile Engine:
    - Uses strictly closed bars for authoritative calculations.
    - Compares current bar volume against historical baseline (past 20 closed bars).
    - Denominator excludes the current bar.
    - Zero guard and insufficient sample guards (returns None / UNKNOWN, never fake 1.0).
    - Bitget XAUUSDT perpetual volume unit is clearly marked (Contracts / Oz).
    """

    @staticmethod
    def calculate_rvol(
        candles: list,
        target_idx: Optional[int] = None,
        baseline_period: int = 20
    ) -> Dict[str, Any]:
        """
        Calculate RVOL for candles[target_idx] (defaults to last closed candle).
        Returns detailed RVOL metrics and classification.
        """
        if not candles or len(candles) < 10:
            return {
                "rvol": None,
                "status": "UNKNOWN",
                "reason": "INSUFFICIENT_SAMPLES: Cần tối thiểu 10 nến lịch sử để tính volume baseline",
                "volume": 0.0,
                "baseline_volume": 0.0,
                "classification": "UNKNOWN"
            }

        idx = target_idx if target_idx is not None else (len(candles) - 1)
        if idx < 0 or idx >= len(candles):
            idx = len(candles) - 1

        curr_candle = candles[idx]
        curr_vol = getattr(curr_candle, "volume", 0.0)

        # Baseline: strictly prior closed bars, excluding current bar idx
        start_idx = max(0, idx - baseline_period)
        baseline_bars = candles[start_idx:idx]

        if len(baseline_bars) < 5:
            return {
                "rvol": None,
                "status": "UNKNOWN",
                "reason": "INSUFFICIENT_BASELINE: Không đủ nến đóng trước đó để tạo baseline",
                "volume": curr_vol,
                "baseline_volume": 0.0,
                "classification": "UNKNOWN"
            }

        past_volumes = [getattr(c, "volume", 0.0) for c in baseline_bars]
        baseline_vol = sum(past_volumes) / len(past_volumes)

        if baseline_vol <= 0.0001:
            return {
                "rvol": None,
                "status": "ZERO_BASELINE",
                "reason": "ZERO_VOLUME_GUARD: Volume quá khứ bằng 0",
                "volume": curr_vol,
                "baseline_volume": 0.0,
                "classification": "UNKNOWN"
            }

        rvol = round(curr_vol / baseline_vol, 2)

        # Classification
        if rvol >= 2.5:
            classification = "ULTRA_HIGH"
        elif rvol >= 1.5:
            classification = "HIGH"
        elif rvol >= 0.8:
            classification = "NORMAL"
        else:
            classification = "LOW"

        return {
            "rvol": rvol,
            "status": "VALID",
            "volume": round(curr_vol, 4),
            "baseline_volume": round(baseline_vol, 4),
            "classification": classification,
            "samples_used": len(baseline_bars),
            "unit": "oz (Bitget XAUUSDT Contracts)"
        }

volume_analyzer = VolumeAnalyzer()
