"""
backend/services/smc_session_features.py
V14.4.1 - point-in-time session features. Pure functions, no I/O.

Thresholds mentioned in docs (0.8 / 1.3 expansion) are CONFIG HYPOTHESES, not validated probabilities; this
module only computes the measurements and returns None when they cannot be computed.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, Optional, Sequence


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def compute_asia_expansion(asia_range: Optional[float], prior_completed_ranges: Sequence[float], *,
                           min_history: int = 5, window: int = 20) -> Optional[float]:
    """K_A = Asia range / median(range of the last `window` COMPLETED earlier Asia sessions).
    None when the range is unknown/non-positive or fewer than `min_history` completed sessions exist."""
    r = _num(asia_range)
    hist = [x for x in (_num(h) for h in prior_completed_ranges) if x is not None and x > 0][-window:]
    if r is None or r <= 0 or len(hist) < min_history:
        return None
    med = statistics.median(hist)
    return round(r / med, 4) if med > 0 else None


def compute_europe_position(europe_close_as_of: Optional[float], asia_high: Optional[float],
                            asia_low: Optional[float]) -> Optional[float]:
    """X_E = (Europe close AS OF now - Asia mid) / Asia range. None on a zero/unknown Asia range."""
    c, h, l = _num(europe_close_as_of), _num(asia_high), _num(asia_low)
    if c is None or h is None or l is None or h <= l:
        return None
    return round((c - (h + l) / 2.0) / (h - l), 4)


def compute_clv(high: Optional[float], low: Optional[float], close: Optional[float]) -> Optional[float]:
    """CLV = (2*close - high - low) / (high - low) in [-1, 1]. None when high == low or unknown."""
    h, l, c = _num(high), _num(low), _num(close)
    if h is None or l is None or c is None or h <= l:
        return None
    return round((2.0 * c - h - l) / (h - l), 4)


def feature_record(name: str, value: Optional[float], *, known_at_ms: int, source: str) -> Dict[str, Any]:
    """Every feature stored point-in-time: when it became knowable and from which source."""
    return {"name": name, "value": value, "known_at_ms": known_at_ms, "source": source,
            "status": "OK" if value is not None else "UNAVAILABLE"}
