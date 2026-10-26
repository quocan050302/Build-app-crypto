"""
backend/lab/smc_outcome_labels.py
V14.4.1 - Outcome labels for the isolated research CLI. Features stop at decision time; future bars are labels only.
A bar that touches both TP and SL is AMBIGUOUS (never wins-first). Timeouts and censored examples are tagged and
counted, never silently turned into wins or dropped.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence


def label_trade_outcome(*, direction: str, entry: float, stop_loss: float, take_profit: float,
                        bars_after_fill: Sequence[Any], horizon_bars: int, filled: bool = True) -> Dict[str, Any]:
    """bars_after_fill: closed bars strictly AFTER the fill bar, oldest first (open/high/low/close/timestamp)."""
    if direction not in ("LONG", "SHORT"):
        raise ValueError("UNKNOWN_DIRECTION")
    geometry_ok = (stop_loss < entry < take_profit) if direction == "LONG" else (take_profit < entry < stop_loss)
    if not geometry_ok:
        raise ValueError("INVALID_PRICE_GEOMETRY")
    if not filled:
        return {"label": "NOT_FILLED", "closed_trade": False, "bars_used": 0}
    window = list(bars_after_fill)[:horizon_bars]
    for i, b in enumerate(window, start=1):
        hit_tp = b.high >= take_profit if direction == "LONG" else b.low <= take_profit
        hit_sl = b.low <= stop_loss if direction == "LONG" else b.high >= stop_loss
        if hit_tp and hit_sl:
            return {"label": "AMBIGUOUS", "closed_trade": False, "bars_used": i, "reason": "SAME_BAR_TP_AND_SL"}
        if hit_tp:
            return {"label": "TP_FIRST", "closed_trade": True, "bars_used": i}
        if hit_sl:
            return {"label": "SL_FIRST", "closed_trade": True, "bars_used": i}
    if len(window) < horizon_bars:
        return {"label": "CENSORED", "closed_trade": False, "bars_used": len(window), "reason": "HORIZON_NOT_REACHED_IN_DATA"}
    return {"label": "TIMEOUT", "closed_trade": False, "bars_used": len(window)}


def label_session_event(*, up_level: float, down_level: float, bars: Sequence[Any]) -> Dict[str, Any]:
    """SESSION event label: which of two levels was reached first by a closed bar's extreme. This is NOT a trade
    result (a LONG can hit its SL while the session still ends up)."""
    for b in bars:
        up, dn = b.high >= up_level, b.low <= down_level
        if up and dn:
            return {"label": "AMBIGUOUS"}
        if up:
            return {"label": "TARGET_FIRST"}
        if dn:
            return {"label": "ALTERNATIVE_FIRST"}
    return {"label": "UNRESOLVED"}


def net_winrate_from_closed(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Empirical NET winrate = closed trades with net PnL after costs > 0 / closed trades. TP probability and
    timeout/manual closes are different things and are reported separately by the caller."""
    closed = [t for t in trades if t.get("closed_trade") and t.get("net_pnl") is not None]
    if not closed:
        return {"n": 0, "wins": 0, "net_winrate": None}
    wins = sum(1 for t in closed if t["net_pnl"] > 0)
    return {"n": len(closed), "wins": wins, "net_winrate": wins / len(closed)}
