"""
backend/services/smc_chain_service.py
V14.4.1 — Validation of SMC structural chains (sweep -> displacement break -> FVG -> retest).

Every predicate here MEASURES something from candles / evidence extras. A chain is verified only if the
relationships were proven by predicates (prefix-ATR displacement, FVG bound to the leg's creator candles,
retest touching the actual FVG bounds after confirmation) - never because the caller declared links.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.smc_scenario_types import (
    ChainCheck, EvidenceNode, stable_hash, validate_reversal_order,
)


def _finite_pos(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


# ---------------------------------------------------------------------------------------------------
# Displacement (prefix ATR, close beyond)
# ---------------------------------------------------------------------------------------------------
def prefix_atr(candles: Sequence[Any], index: int, period: int) -> Optional[float]:
    """Mean true range of the `period` closed bars strictly BEFORE candles[index]. None if not enough bars."""
    if index < period + 1 or index > len(candles):
        return None
    trs: List[float] = []
    for i in range(index - period, index):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    atr = sum(trs) / len(trs)
    return atr if atr > 0 else None


def qualify_displacement(
    candle: Any,
    atr_at_break: Optional[float],
    threshold_ratio: float = 0.8,
    *,
    direction: Optional[str] = None,
    broken_level: Optional[float] = None,
) -> Dict[str, Any]:
    """Whether the break candle is a displacement: body >= threshold * prefix-ATR AND (when direction and
    broken_level are given) it CLOSES beyond the broken level. No ATR -> not qualified (never a default)."""
    body = abs(float(candle.close) - float(candle.open))
    if atr_at_break is None or not _finite_pos(atr_at_break):
        return {"body_size": round(body, 4), "atr_at_break": None, "ratio": None,
                "threshold_ratio": threshold_ratio, "is_qualified": False, "close_beyond": None,
                "reason": "ATR_UNAVAILABLE", "candle_timestamp": getattr(candle, "timestamp", 0)}
    ratio = body / atr_at_break
    close_beyond: Optional[bool] = None
    if direction in ("BULLISH", "LONG") and broken_level is not None:
        close_beyond = float(candle.close) > broken_level
    elif direction in ("BEARISH", "SHORT") and broken_level is not None:
        close_beyond = float(candle.close) < broken_level
    qualified = ratio >= threshold_ratio and close_beyond is not False
    reason = None if qualified else ("BODY_BELOW_ATR_THRESHOLD" if ratio < threshold_ratio else "NO_CLOSE_BEYOND_LEVEL")
    return {
        "body_size": round(body, 4), "atr_at_break": round(atr_at_break, 4), "ratio": round(ratio, 4),
        "threshold_ratio": threshold_ratio, "is_qualified": bool(qualified), "close_beyond": close_beyond,
        "reason": reason, "candle_timestamp": getattr(candle, "timestamp", 0),
        "open": float(candle.open), "close": float(candle.close),
        "high": float(candle.high), "low": float(candle.low),
    }


# ---------------------------------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------------------------------
def validate_chain_order(
    sweep: EvidenceNode, brk: EvidenceNode, fvg: EvidenceNode, retest: EvidenceNode,
) -> Tuple[bool, List[str]]:
    """Temporal/intra-candle causality: sweep before the break candle, break known before FVG
    confirmation, FVG confirmed before the retest candle begins."""
    reasons: List[str] = []
    if sweep.occurred_at_ms == brk.occurred_at_ms:
        reasons.append("SAME_BAR_AMBIGUOUS")
    elif sweep.occurred_at_ms > brk.occurred_at_ms:
        reasons.append("SWEEP_AFTER_BREAK")
    elif sweep.known_at_ms > brk.occurred_at_ms:
        reasons.append("SWEEP_NOT_KNOWN_BEFORE_BREAK")
    if brk.known_at_ms > fvg.known_at_ms:
        reasons.append("BREAK_AFTER_FVG")
    if fvg.known_at_ms > retest.occurred_at_ms:
        reasons.append("RETEST_BEFORE_FVG_CONFIRMATION")
    return (len(reasons) == 0, reasons)


# ---------------------------------------------------------------------------------------------------
# FVG <-> displacement leg binding (checks real candles)
# ---------------------------------------------------------------------------------------------------
def bind_fvg_to_leg(
    fvg: EvidenceNode,
    brk: EvidenceNode,
    candles: Sequence[Any],
    candle_duration_ms: int = 300_000,
) -> Tuple[bool, str]:
    """True only if the FVG was CREATED by the break's displacement leg.

    The FVG's 3 creator candles are (middle - dur, middle, middle + dur). The break candle must be one of
    them and the stored zone must equal the creator candles' actual gap: bullish bottom=first.high,
    top=third.low; bearish top=first.low, bottom=third.high. Returns (ok, reason)."""
    top, bottom = fvg.extra.get("top"), fvg.extra.get("bottom")
    if not (_finite_pos(top) and _finite_pos(bottom)) or bottom >= top:
        return False, "FVG_GEOMETRY_MISSING"
    mid = fvg.occurred_at_ms
    first_ts, third_ts = mid - candle_duration_ms, mid + candle_duration_ms
    if brk.occurred_at_ms not in (first_ts, mid, third_ts):
        return False, "BREAK_NOT_IN_FVG_LEG"
    by_ts = {c.timestamp: c for c in candles}
    first, middle, third = by_ts.get(first_ts), by_ts.get(mid), by_ts.get(third_ts)
    if first is None or middle is None or third is None:
        return False, "FVG_CREATOR_CANDLES_MISSING"
    if fvg.direction == "BULLISH":
        ok = abs(first.high - bottom) < 1e-6 and abs(third.low - top) < 1e-6
    elif fvg.direction == "BEARISH":
        ok = abs(first.low - top) < 1e-6 and abs(third.high - bottom) < 1e-6
    else:
        return False, "FVG_DIRECTION_UNKNOWN"
    return (True, "OK") if ok else (False, "FVG_BOUNDS_DO_NOT_MATCH_CREATOR_CANDLES")


# ---------------------------------------------------------------------------------------------------
# Retest
# ---------------------------------------------------------------------------------------------------
def confirm_closed_retest(
    fvg_zone: Dict[str, Any],
    candidate_candle: Any,
    direction: str,
    *,
    known_at_ms: Optional[int] = None,
    as_of_ms: Optional[int] = None,
    candle_duration_ms: int = 300_000,
) -> Tuple[bool, Optional[float]]:
    """A CLOSED candle that traded back into the actual FVG bounds and closed on the valid side.
    Returns (touched, touch_price) where touch_price is the deepest in-zone price the candle reached
    (clamped to the zone) - not an unrelated wick extreme."""
    if getattr(candidate_candle, "is_closed", None) is not True:
        return False, None
    ts = candidate_candle.timestamp
    if known_at_ms is not None and ts < known_at_ms:
        return False, None
    if as_of_ms is not None and ts + candle_duration_ms > as_of_ms:
        return False, None
    top, bottom = float(fvg_zone["top"]), float(fvg_zone["bottom"])
    if direction == "LONG":
        if candidate_candle.low <= top and candidate_candle.close >= bottom:
            return True, round(min(max(candidate_candle.low, bottom), top), 2)
    elif direction == "SHORT":
        if candidate_candle.high >= bottom and candidate_candle.close <= top:
            return True, round(min(max(candidate_candle.high, bottom), top), 2)
    return False, None


def confirm_quote_retest(
    fvg_zone: Dict[str, Any],
    quote: Any,
    direction: str,
    fvg_confirmed_at_ms: int,
) -> Tuple[bool, Optional[str]]:
    """A validated, fresh quote inside the actual FVG bounds after the FVG was confirmed."""
    if not getattr(quote, "is_valid", False):
        return False, "QUOTE_NOT_VALID"
    if getattr(quote, "is_stale", False):
        return False, "QUOTE_STALE"
    quote_time = getattr(quote, "exchange_time", None) or getattr(quote, "observed_at", None)
    if quote_time is None or quote_time <= fvg_confirmed_at_ms:
        return False, "QUOTE_BEFORE_FVG_CONFIRMATION"
    top, bottom = float(fvg_zone["top"]), float(fvg_zone["bottom"])
    if direction == "LONG":
        ask = float(getattr(quote, "ask", 0.0) or 0.0)
        if ask <= 0:
            return False, "INVALID_QUOTE_PRICE"
        if bottom <= ask <= top:
            return True, None
        if ask < bottom:
            return False, "FVG_ZONE_VIOLATED"
    elif direction == "SHORT":
        bid = float(getattr(quote, "bid", 0.0) or 0.0)
        if bid <= 0:
            return False, "INVALID_QUOTE_PRICE"
        if bottom <= bid <= top:
            return True, None
        if bid > top:
            return False, "FVG_ZONE_VIOLATED"
    return False, "QUOTE_NOT_IN_ZONE"


# ---------------------------------------------------------------------------------------------------
# Chain validation
# ---------------------------------------------------------------------------------------------------
def validate_reversal_chain(
    *,
    symbol: str,
    direction: str,
    as_of_ms: int,
    sweep: EvidenceNode,
    brk: EvidenceNode,
    fvg: EvidenceNode,
    retest: EvidenceNode,
    target_level: Optional[float] = None,
    invalidation_price: Optional[float] = None,
    planned_entry: Optional[float] = None,
    regime: Optional[str] = None,
    check_measurements: bool = True,
) -> ChainCheck:
    """Reversal chain validator.

    1. Graph/order/direction (validate_reversal_order + validate_chain_order).
    2. MEASURED facts (check_measurements): sweep wick extreme beyond the swept level and closed back;
       displacement qualified by prefix-ATR with close beyond the broken level; FVG real bounds with the
       leg binding already proven; retest zone equal to the FVG bounds and the touch price inside it.
    3. Price geometry: LONG inv < entry < target, SHORT target < entry < inv.
    4. SHOCK regime blocks (explanation only, no entry in a shock)."""
    base = validate_reversal_order(
        symbol=symbol, direction=direction, as_of_ms=as_of_ms,
        sweep=sweep, brk=brk, fvg=fvg, retest=retest,
    )
    reasons = list(base.reasons)
    ok, order_reasons = validate_chain_order(sweep, brk, fvg, retest)
    if not ok:
        reasons.extend(order_reasons)

    if check_measurements:
        wick, lvl = sweep.extra.get("wick_extreme"), sweep.level
        if not _finite_pos(wick) or lvl is None:
            reasons.append("SWEEP_EXTREME_MISSING")
        else:
            if direction == "LONG" and not wick < lvl:
                reasons.append("SWEEP_WICK_NOT_BEYOND_LEVEL")
            if direction == "SHORT" and not wick > lvl:
                reasons.append("SWEEP_WICK_NOT_BEYOND_LEVEL")
        if sweep.extra.get("closed_back") is not True:
            reasons.append("SWEEP_NOT_CLOSED_BACK")

        disp = brk.extra.get("displacement") or {}
        if not isinstance(disp, dict) or disp.get("is_qualified") is not True:
            reasons.append("DISPLACEMENT_NOT_QUALIFIED")
        elif disp.get("close_beyond") is not True:
            reasons.append("DISPLACEMENT_NO_CLOSE_BEYOND")

        top, bottom = fvg.extra.get("top"), fvg.extra.get("bottom")
        if not (_finite_pos(top) and _finite_pos(bottom)) or bottom >= top:
            reasons.append("FVG_GEOMETRY_MISSING")
        if fvg.extra.get("leg_bound") is not True:
            reasons.append("FVG_NOT_BOUND_TO_LEG")

        zt, zb = retest.extra.get("zone_top"), retest.extra.get("zone_bottom")
        if not (_finite_pos(zt) and _finite_pos(zb)) or not (_finite_pos(top) and _finite_pos(bottom)) \
                or abs(zt - top) > 1e-6 or abs(zb - bottom) > 1e-6:
            reasons.append("RETEST_ZONE_MISMATCH")
        touch = retest.extra.get("touch_price")
        if not _finite_pos(touch) or not (_finite_pos(top) and _finite_pos(bottom)) or not (bottom <= touch <= top):
            reasons.append("RETEST_TOUCH_OUTSIDE_ZONE")

    if regime == "SHOCK":
        reasons.append("REGIME_SHOCK")

    if planned_entry is not None and invalidation_price is not None and target_level is not None:
        if direction == "LONG" and not (invalidation_price < planned_entry < target_level):
            reasons.append("GEOMETRY_INVALID_LONG")
        elif direction == "SHORT" and not (target_level < planned_entry < invalidation_price):
            reasons.append("GEOMETRY_INVALID_SHORT")

    unique = tuple(dict.fromkeys(reasons))
    return ChainCheck(len(unique) == 0, unique, base.evidence_ids)


# ---------------------------------------------------------------------------------------------------
# Entry-bundle validators (single entry point used by manual eligibility, auto-arm and API)
# ---------------------------------------------------------------------------------------------------
FALLBACK_REQUIRED_KEYS = ("fvg_id", "fvg_zone", "fvg_confirmed_at", "retest", "liquidity_target", "invalidation_price")


def validate_fallback_evidence(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Adapter for the V14.3 NY_QUOTA_PAPER fallback (experimental, no sweep required). Checks its OWN
    facts: FVG zone + confirmation, retest after confirmation, real liquidity target, SL/TP geometry."""
    ev = bundle.get("evidence") or {}
    direction, as_of = bundle.get("direction"), bundle.get("as_of_ms", 0)
    reasons: List[str] = [f"MISSING_FALLBACK_{k.upper()}" for k in FALLBACK_REQUIRED_KEYS if not ev.get(k)]
    if not reasons:
        zone = ev["fvg_zone"]
        try:
            bottom, top = float(zone[0]), float(zone[1])
        except Exception:
            bottom = top = 0.0
        if not (bottom > 0 and top > bottom):
            reasons.append("FALLBACK_FVG_GEOMETRY_INVALID")
        rt = ev["retest"] if isinstance(ev["retest"], dict) else {}
        if not rt.get("time") or rt["time"] < ev["fvg_confirmed_at"]:
            reasons.append("FALLBACK_RETEST_BEFORE_FVG_CONFIRMATION")
        if as_of and ev["fvg_confirmed_at"] > as_of:
            reasons.append("FALLBACK_FVG_NOT_KNOWN_AT_CUTOFF")
        entry, sl, tp = bundle.get("planned_entry"), bundle.get("invalidation_price"), bundle.get("target_price")
        if entry is not None and sl is not None and tp is not None:
            if direction == "LONG" and not (sl < entry < tp):
                reasons.append("GEOMETRY_INVALID_LONG")
            elif direction == "SHORT" and not (tp < entry < sl):
                reasons.append("GEOMETRY_INVALID_SHORT")
    unique = list(dict.fromkeys(reasons))
    return {
        "verified": not unique, "reasons": unique,
        "linked_ids": [ev.get("fvg_id")] if ev.get("fvg_id") else [],
        "version": "v14.4.1-fallback", "experimental": True,
        "digest": stable_hash(bundle.get("symbol"), "NY_QUOTA_PAPER", direction, ev.get("fvg_id"),
                              ev.get("fvg_confirmed_at")) if not unique else "",
    }


def validate_entry_evidence_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Validates an incoming evidence bundle for the certified entry gate. STANDARD_SMC needs the 4 chain
    nodes and passes the MEASURED predicates; NY_QUOTA_PAPER goes to its own adapter."""
    family = bundle.get("strategy_family", "STANDARD_SMC")
    direction = bundle.get("direction")
    as_of_ms = bundle.get("as_of_ms", 0)
    symbol = bundle.get("symbol", "XAUUSDT")

    if family == "NY_QUOTA_PAPER":
        return validate_fallback_evidence(bundle)

    nodes_dict = bundle.get("nodes", {})
    required_keys = ("sweep", "break", "fvg", "retest")
    if not all(k in nodes_dict for k in required_keys):
        missing = [k for k in required_keys if k not in nodes_dict]
        return {"verified": False, "reasons": [f"MISSING_CHAIN_NODE_{k.upper()}" for k in missing],
                "linked_ids": [], "version": "v14.4.1", "digest": ""}

    try:
        sweep_n, brk_n, fvg_n, retest_n = (EvidenceNode.from_dict({"symbol": symbol, "timeframe": "5M", **nodes_dict[k]})
                                           for k in required_keys)
    except Exception as e:  # malformed evidence is a controlled failure, never a pass
        return {"verified": False, "reasons": ["MALFORMED_EVIDENCE_NODES", type(e).__name__],
                "linked_ids": [], "version": "v14.4.1", "digest": ""}

    check = validate_reversal_chain(
        symbol=symbol, direction=direction, as_of_ms=as_of_ms,
        sweep=sweep_n, brk=brk_n, fvg=fvg_n, retest=retest_n,
        target_level=bundle.get("target_price"),
        invalidation_price=bundle.get("invalidation_price"),
        planned_entry=bundle.get("planned_entry"),
        regime=bundle.get("regime"),
    )
    digest = stable_hash(symbol, family, direction, as_of_ms, *check.evidence_ids) if check.verified else ""
    return {"verified": check.verified, "reasons": list(check.reasons),
            "linked_ids": list(check.evidence_ids), "version": "v14.4.1", "digest": digest}
