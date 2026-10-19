"""
backend/services/cg01_daily_bias_types.py
Data contracts and snapshots for CG-01 Evidence-Based Daily Bias.
Implements Rules 008, 051 - 070.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class CG01DailyBiasDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class CG01DailyBiasStatus(str, Enum):
    READY = "READY"
    NEUTRAL = "NEUTRAL"
    UNAVAILABLE = "UNAVAILABLE"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class CG01DailyLiquidityLevel:
    """Checklist Fact 1: Liquidity levels (PDH, PDL, HTF Swings)."""
    level_id: str
    level_type: str            # PDH, PDL, H4_SWING_HIGH, H4_SWING_LOW, H1_SWING_HIGH, H1_SWING_LOW, EQUAL_HIGHS, EQUAL_LOWS
    price: float
    timeframe: str
    status: str                # UNSWEPT, SWEPT, VIOLATED
    swept_at_ms: Optional[int] = None
    known_at_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level_id": self.level_id,
            "level_type": self.level_type,
            "price": self.price,
            "timeframe": self.timeframe,
            "status": self.status,
            "swept_at_ms": self.swept_at_ms,
            "known_at_ms": self.known_at_ms,
        }


@dataclass(frozen=True)
class CG01DailyDealingRange:
    """Checklist Fact 2: Dealing Range & Premium/Discount location."""
    range_id: str
    upper: float
    lower: float
    midpoint: float
    price_zone: str            # PREMIUM, DISCOUNT, EQUILIBRIUM, UNKNOWN
    high_anchor_id: Optional[str] = None
    low_anchor_id: Optional[str] = None
    is_valid: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "range_id": self.range_id,
            "upper": self.upper,
            "lower": self.lower,
            "midpoint": self.midpoint,
            "price_zone": self.price_zone,
            "position": self.price_zone,
            "high_anchor_id": self.high_anchor_id,
            "low_anchor_id": self.low_anchor_id,
            "is_valid": self.is_valid,
        }


@dataclass(frozen=True)
class CG01HTFZoneReaction:
    """Checklist Fact 3: Reaction at HTF FVG/OB."""
    zone_id: str
    zone_type: str             # FVG, ORDER_BLOCK
    timeframe: str
    top: float
    bottom: float
    reaction: str              # RESPECTED, VIOLATED, IN_ZONE, UNCERTAIN, UNTESTED
    confirmed_candle_ts: Optional[int] = None
    known_at_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "zone_type": self.zone_type,
            "timeframe": self.timeframe,
            "top": self.top,
            "bottom": self.bottom,
            "reaction": self.reaction,
            "confirmed_candle_ts": self.confirmed_candle_ts,
            "known_at_ms": self.known_at_ms,
        }


@dataclass
class CG01DailyBiasSnapshot:
    """
    Rules 051 - 070: Immutable Evidence-Based Daily Bias Snapshot for CG-01.
    Pure data container capturing H4 anchor, D1 context, H1 session relation,
    liquidity checklist, range location, and invalidation rules.
    """
    symbol: str
    trading_day_key: str
    bias_id: str
    direction: str                          # BULLISH, BEARISH, NEUTRAL
    status: str                             # READY, NEUTRAL, UNAVAILABLE, INVALIDATED
    allowed_directions: Tuple[str, ...]     # ("LONG",), ("SHORT",), ()
    authority_token: str
    revision: int = 1
    algorithm_version: str = "v16.30"
    config_fingerprint: str = ""
    evaluated_at_ms: int = 0
    facts_known_at_ms: int = 0
    h4_structure: Dict[str, Any] = field(default_factory=dict)
    h1_session_context: Dict[str, Any] = field(default_factory=dict)
    d1_structure: Optional[Dict[str, Any]] = None
    liquidity_facts: List[Dict[str, Any]] = field(default_factory=list)
    range_facts: Optional[Dict[str, Any]] = None
    zone_reaction_facts: List[Dict[str, Any]] = field(default_factory=list)
    invalidation_rule: Dict[str, Any] = field(default_factory=dict)
    reason_codes: Tuple[str, ...] = ()
    ui_summary_vi: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "trading_day_key": self.trading_day_key,
            "bias_id": self.bias_id,
            "direction": self.direction,
            "status": self.status,
            "allowed_directions": list(self.allowed_directions),
            "authority_token": self.authority_token,
            "revision": self.revision,
            "algorithm_version": self.algorithm_version,
            "config_fingerprint": self.config_fingerprint,
            "evaluated_at_ms": self.evaluated_at_ms,
            "facts_known_at_ms": self.facts_known_at_ms,
            "h4_structure": self.h4_structure,
            "h1_session_context": self.h1_session_context,
            "d1_structure": self.d1_structure,
            "liquidity_facts": self.liquidity_facts,
            "range_facts": self.range_facts,
            "zone_reaction_facts": self.zone_reaction_facts,
            "invalidation_rule": self.invalidation_rule,
            "reason_codes": list(self.reason_codes),
            "ui_summary_vi": self.ui_summary_vi,
            "summary_vi": (self.ui_summary_vi.get("detail") or self.ui_summary_vi.get("status")) if isinstance(self.ui_summary_vi, dict) else str(self.ui_summary_vi or ""),
        }
