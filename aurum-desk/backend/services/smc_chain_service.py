"""
backend/services/smc_chain_service.py
V14.4 — Comprehensive validation for SMC structural chains (reversal & continuation).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
from services.smc_scenario_types import (
    ChainCheck, EvidenceNode, FactStatus, stable_hash, validate_reversal_order
)


def qualify_displacement(
    candle: Any,
    atr_at_break: float,
    threshold_ratio: float = 0.8,
) -> Dict[str, Any]:
    """Qualifies whether a candle represents valid institutional displacement.
    body_size >= threshold_ratio * atr_at_break.
    """
    body_size = abs(float(candle.close) - float(candle.open))
    atr = max(0.1, float(atr_at_break))
    ratio = round(body_size / atr, 3)
    is_qualified = ratio >= threshold_ratio

    return {
        "body_size": round(body_size, 2),
        "atr_at_break": round(atr, 2),
        "ratio": ratio,
        "threshold_ratio": threshold_ratio,
        "is_qualified": is_qualified,
        "candle_timestamp": getattr(candle, "timestamp", 0),
        "close": float(candle.close),
        "open": float(candle.open),
        "high": float(candle.high),
        "low": float(candle.low),
    }


def validate_chain_order(
    sweep: EvidenceNode,
    brk: EvidenceNode,
    fvg: EvidenceNode,
    retest: EvidenceNode,
) -> Tuple[bool, List[str]]:
    """Strictly checks temporal and intra-candle causality:
    - Sweep must occur and be known before break candle begins.
    - If sweep and break share the same candle timestamp -> SAME_BAR_AMBIGUOUS.
    - Break must be known before FVG is confirmed.
    - FVG must be confirmed before retest occurs.
    """
    reasons: List[str] = []

    # Same-bar ambiguity check (Phần 040)
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
    elif fvg.known_at_ms == retest.known_at_ms and retest.kind == "CLOSED_CANDLE_RETEST":
        # Retest candle cannot be the exact 3rd FVG candle before FVG is confirmed
        reasons.append("RETEST_ON_FVG_CREATION_BAR")

    return (len(reasons) == 0, reasons)


def bind_fvg_to_leg(
    fvg: EvidenceNode,
    brk: EvidenceNode,
    candles: Sequence[Any],
    candle_duration_ms: int = 300_000,
) -> bool:
    """Verifies that the FVG was formed as part of the displacement leg triggered by the break.
    The middle or third candle of the FVG must be within 3 candles of the break candle.
    """
    fvg_time = fvg.occurred_at_ms
    brk_time = brk.occurred_at_ms

    # FVG middle candle cannot occur before the break candle
    if fvg_time < brk_time - candle_duration_ms:
        return False

    # Within reasonable leg span (max 4 candles from break)
    max_leg_span_ms = 4 * candle_duration_ms
    return fvg_time <= brk_time + max_leg_span_ms


def confirm_closed_retest(
    fvg_zone: Dict[str, Any],
    candidate_candle: Any,
    direction: str,
) -> Tuple[bool, Optional[float]]:
    """Checks whether a closed candle retested the FVG zone properly."""
    top = float(fvg_zone["top"])
    bottom = float(fvg_zone["bottom"])
    c_low = float(candidate_candle.low)
    c_high = float(candidate_candle.high)
    c_close = float(candidate_candle.close)

    if direction == "LONG":
        # Bullish FVG: price dips down into [bottom, top]
        if c_low <= top and c_close >= bottom:
            return True, c_low
    elif direction == "SHORT":
        # Bearish FVG: price rallies up into [bottom, top]
        if c_high >= bottom and c_close <= top:
            return True, c_high

    return False, None


def confirm_quote_retest(
    fvg_zone: Dict[str, Any],
    quote: Any,
    direction: str,
    fvg_confirmed_at_ms: int,
) -> Tuple[bool, Optional[str]]:
    """Checks whether a real-time validated quote retested the FVG zone after FVG was confirmed."""
    quote_time = getattr(quote, "exchange_time", None) or getattr(quote, "observed_at", None)
    if quote_time is None or quote_time <= fvg_confirmed_at_ms:
        return False, "QUOTE_BEFORE_FVG_CONFIRMATION"

    top = float(fvg_zone["top"])
    bottom = float(fvg_zone["bottom"])

    if direction == "LONG":
        ask = float(getattr(quote, "ask", 0.0))
        if ask <= 0:
            return False, "INVALID_QUOTE_PRICE"
        # Ask dipped into FVG zone and is above invalidation bottom
        if bottom <= ask <= top:
            return True, None
        if ask < bottom:
            return False, "FVG_ZONE_VIOLATED"
    elif direction == "SHORT":
        bid = float(getattr(quote, "bid", 0.0))
        if bid <= 0:
            return False, "INVALID_QUOTE_PRICE"
        # Bid pushed up into FVG zone and is below invalidation top
        if bottom <= bid <= top:
            return True, None
        if bid > top:
            return False, "FVG_ZONE_VIOLATED"

    return False, "QUOTE_NOT_IN_ZONE"


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
    **kwargs: Any,
) -> ChainCheck:
    """Comprehensive reversal chain validator:
    1. Base graph order & node properties (Appendix A).
    2. Exact intra-candle timing (no same-bar ambiguity).
    3. Price geometry consistency:
       - LONG: invalidation_price < planned_entry < target_level
       - SHORT: target_level < planned_entry < invalidation_price
    """
    base_check = validate_reversal_order(
        symbol=symbol,
        direction=direction,
        as_of_ms=as_of_ms,
        sweep=sweep,
        brk=brk,
        fvg=fvg,
        retest=retest,
    )

    reasons = list(base_check.reasons)

    # Extra temporal checks
    order_ok, order_reasons = validate_chain_order(sweep, brk, fvg, retest)
    if not order_ok:
        reasons.extend(order_reasons)

    # Geometry checks if prices are provided
    if planned_entry is not None and invalidation_price is not None and target_level is not None:
        if direction == "LONG":
            if not (invalidation_price < planned_entry < target_level):
                reasons.append("GEOMETRY_INVALID_LONG")
        elif direction == "SHORT":
            if not (target_level < planned_entry < invalidation_price):
                reasons.append("GEOMETRY_INVALID_SHORT")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return ChainCheck(len(unique_reasons) == 0, unique_reasons, base_check.evidence_ids)


def validate_entry_evidence_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Validates an incoming evidence bundle for certified entry gate.
    Used by EligibilityService and EntryGuardService.
    """
    family = bundle.get("strategy_family", "STANDARD_SMC")
    direction = bundle.get("direction")
    as_of_ms = bundle.get("as_of_ms", 0)
    symbol = bundle.get("symbol", "XAUUSDT")

    # NY_QUOTA_PAPER uses explicit fallback adapter (Phần 046)
    if family == "NY_QUOTA_PAPER":
        return {
            "verified": True,
            "reasons": [],
            "linked_ids": bundle.get("evidence_ids", []),
            "version": "v14.4-fallback",
            "digest": stable_hash(symbol, family, direction, as_of_ms),
        }

    # STANDARD_SMC Reversal Validation
    nodes_dict = bundle.get("nodes", {})
    required_keys = ("sweep", "break", "fvg", "retest")
    if not all(k in nodes_dict for k in required_keys):
        missing = [k for k in required_keys if k not in nodes_dict]
        return {
            "verified": False,
            "reasons": [f"MISSING_CHAIN_NODE_{k.upper()}" for k in missing],
            "linked_ids": [],
            "version": "v14.4",
            "digest": "",
        }

    def _to_node(d: Dict[str, Any]) -> EvidenceNode:
        return EvidenceNode(
            id=d["id"],
            kind=d["kind"],
            symbol=d.get("symbol", symbol),
            timeframe=d.get("timeframe", "15M"),
            direction=d.get("direction"),
            occurred_at_ms=d["occurred_at_ms"],
            known_at_ms=d["known_at_ms"],
            source_ids=tuple(d.get("source_ids", ())),
            level=d.get("level"),
            extra=d.get("extra", {}),
        )

    try:
        sweep_n = _to_node(nodes_dict["sweep"])
        brk_n = _to_node(nodes_dict["break"])
        fvg_n = _to_node(nodes_dict["fvg"])
        retest_n = _to_node(nodes_dict["retest"])
    except Exception as e:
        return {
            "verified": False,
            "reasons": ["MALFORMED_EVIDENCE_NODES", str(e)],
            "linked_ids": [],
            "version": "v14.4",
            "digest": "",
        }

    check = validate_reversal_chain(
        symbol=symbol,
        direction=direction,
        as_of_ms=as_of_ms,
        sweep=sweep_n,
        brk=brk_n,
        fvg=fvg_n,
        retest=retest_n,
        target_level=bundle.get("target_price"),
        invalidation_price=bundle.get("invalidation_price"),
        planned_entry=bundle.get("planned_entry"),
    )

    digest = stable_hash(symbol, family, direction, as_of_ms, *check.evidence_ids) if check.verified else ""

    return {
        "verified": check.verified,
        "reasons": list(check.reasons),
        "linked_ids": list(check.evidence_ids),
        "version": "v14.4",
        "digest": digest,
    }
