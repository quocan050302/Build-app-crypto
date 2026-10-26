"""
backend/lab/smc_session_dataset.py
V14.4.1 - Isolated research CLI: point-in-time as-of examples + dataset coverage audit.

Small on purpose (NOT the old replay dashboard, never touches the trading DB). It refuses to invent data:
no spot XAUUSD + futures XAUUSDT splice, no M5 synthesised from M15, no forward-filled OHLC.
Real data absent/short -> NOT_VERIFIED with null metrics. Forward PAPER sampling is the honest way to grow it.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Sequence

M5 = 300_000


def audit_dataset_coverage(candles: Sequence[Any], *, instrument: str, timeframe: str, source: str,
                           timezone_note: str = "UTC epoch ms", expected_instrument: Optional[str] = None,
                           duration_ms: int = M5) -> Dict[str, Any]:
    """Source, timezone, content hash, coverage and gaps. Mixed instruments are NOT_VERIFIED."""
    ts = sorted({int(c.timestamp) for c in candles})
    out: Dict[str, Any] = {"instrument": instrument, "timeframe": timeframe, "source": source,
                           "timezone": timezone_note, "n_bars": len(ts), "status": "NOT_VERIFIED"}
    if expected_instrument and expected_instrument != instrument:
        out["reason"] = "INSTRUMENT_MISMATCH_NOT_SPLICED"
        return out
    if len(ts) < 2:
        out["reason"] = "INSUFFICIENT_DATA"
        return out
    span_bars = (ts[-1] - ts[0]) // duration_ms + 1
    gaps = sum(1 for a, b in zip(ts, ts[1:]) if b - a > duration_ms)
    out.update({
        "first_ms": ts[0], "last_ms": ts[-1], "expected_bars": span_bars, "observed_bars": len(ts),
        "coverage_ratio": len(ts) / span_bars, "gap_count": gaps,
        "sha256": hashlib.sha256(json.dumps(ts).encode()).hexdigest()[:16],
        "span_days": (ts[-1] - ts[0]) / 86_400_000,
    })
    out["status"] = "COVERED" if out["coverage_ratio"] >= 0.98 and gaps == 0 else "GAPS"
    return out


def build_asof_examples(candles_by_tf: Dict[str, List[Any]], decision_times_ms: Sequence[int], *, symbol: str,
                        config: Any, forecast_fn: Any = None) -> List[Dict[str, Any]]:
    """For each decision time, features are computed from CLOSED bars up to that instant only (prefix), then the
    caller attaches labels from FUTURE bars via smc_outcome_labels. Chronological, deterministic, no DB writes."""
    from services.smc_scenario_service import load_closed_context
    from services.smc_session_forecast_service import evaluate_session_forecasts
    fn = forecast_fn or evaluate_session_forecasts
    examples: List[Dict[str, Any]] = []
    for t in sorted(decision_times_ms):
        prefix = {tf: [c for c in v if c.timestamp + _dur(tf) <= t] for tf, v in candles_by_tf.items()}
        ctx = load_closed_context(prefix, t, config)
        fc = fn(symbol, ctx, t, config)
        for session, f in fc.items():
            examples.append({
                "id": f"{symbol}:{session}:{t}", "group": f"{symbol}:{f.session_id}", "decision_ms": t,
                "session": session, "hypothesis_type": f.hypothesis_type, "data_status": f.data_status,
                "features": f.features, "label": None, "label_end_ms": None,
                "counterfactual_news": True,       # no point-in-time calendar here -> not a production winrate
            })
    return examples


def _dur(tf: str) -> int:
    return {"5M": 300_000, "15M": 900_000, "1H": 3_600_000, "4H": 14_400_000, "D": 86_400_000}.get(tf, 300_000)


def verdict_for_real_data(audits: Sequence[Dict[str, Any]], *, min_days: int = 90) -> Dict[str, Any]:
    """Honest status for a research claim: NOT_VERIFIED + null metrics unless every required series is covered
    for at least `min_days` of the SAME instrument."""
    if not audits or any(a.get("status") != "COVERED" for a in audits) or any((a.get("span_days") or 0) < min_days for a in audits):
        return {"status": "NOT_VERIFIED", "winrate": None, "expectancy": None,
                "reason": "REAL_DATASET_MISSING_OR_SHORT"}
    return {"status": "DATA_OK_METRICS_PENDING", "winrate": None, "expectancy": None}
