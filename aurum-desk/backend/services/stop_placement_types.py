"""
backend/services/stop_placement_types.py
AURUM V17.5 — Types, Data Classes & Contracts for Structural Stop Placement & Paired Research.
Immutable, strictly-typed, and JSON-safe data structures.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class StopPlacementMode(str, Enum):
    """Execution modes for structural stop placement."""
    LEGACY = "LEGACY"
    SHADOW_COMPARE = "SHADOW_COMPARE"
    PAPER_STRUCTURAL = "PAPER_STRUCTURAL"


class StopAnchorKind(str, Enum):
    """Classifies the structural origin of the protective stop anchor."""
    SWEEP_EXTREME = "SWEEP_EXTREME"
    PROTECTED_LEG_HIGH = "PROTECTED_LEG_HIGH"
    PROTECTED_LEG_LOW = "PROTECTED_LEG_LOW"
    FVG_LEG_ORIGIN = "FVG_LEG_ORIGIN"
    DISPLACEMENT_ORIGIN = "DISPLACEMENT_ORIGIN"
    UNKNOWN = "UNKNOWN"


class StopStatus(str, Enum):
    """Status of the stop placement decision."""
    VALID = "VALID"
    WAITING_EVIDENCE = "WAITING_EVIDENCE"
    INVALID_DATA = "INVALID_DATA"
    REJECTED_GEOMETRY = "REJECTED_GEOMETRY"
    REJECTED_COST_RR = "REJECTED_COST_RR"
    UNAVAILABLE_LEGACY = "UNAVAILABLE_LEGACY"


class PriceBasis(str, Enum):
    """Price source basis for stop anchor and execution quotes."""
    CANDLE_LAST = "CANDLE_LAST"
    CANDLE_BID = "CANDLE_BID"
    CANDLE_ASK = "CANDLE_ASK"
    MARKET_QUOTE_BID = "MARKET_QUOTE_BID"
    MARKET_QUOTE_ASK = "MARKET_QUOTE_ASK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StopAnchor:
    """Immutable structural reference level used as the invalidation anchor."""
    id: str
    kind: str  # StopAnchorKind value
    price: float
    timeframe: str
    occurred_at_ms: int
    known_at_ms: int
    source_leg_id: str
    source_break_id: Optional[str] = None
    price_basis: str = PriceBasis.CANDLE_LAST.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "price": self.price,
            "timeframe": self.timeframe,
            "occurred_at_ms": self.occurred_at_ms,
            "known_at_ms": self.known_at_ms,
            "source_leg_id": self.source_leg_id,
            "source_break_id": self.source_break_id,
            "price_basis": self.price_basis,
        }


@dataclass(frozen=True)
class StopAnchorResult:
    """Result of structural anchor resolution."""
    status: str  # "AVAILABLE" or reason code
    anchor: Optional[StopAnchor] = None
    reasons: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "anchor": self.anchor.to_dict() if self.anchor else None,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class StopPlacementPolicy:
    """Authoritative stop placement configuration."""
    mode: str = StopPlacementMode.SHADOW_COMPARE.value
    version: int = 1
    atr_period: int = 14
    atr_method: str = "SMA_TR_V1"
    min_buffer_ticks: int = 5
    atr_m1_multiplier: float = 0.25
    atr_m5_multiplier: float = 0.10
    spread_multiplier: float = 1.50
    application_scope: str = "NEW_PAPER_ORDERS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "version": self.version,
            "atr_period": self.atr_period,
            "atr_method": self.atr_method,
            "min_buffer_ticks": self.min_buffer_ticks,
            "atr_m1_multiplier": self.atr_m1_multiplier,
            "atr_m5_multiplier": self.atr_m5_multiplier,
            "spread_multiplier": self.spread_multiplier,
            "application_scope": self.application_scope,
        }


@dataclass(frozen=True)
class StopATRResult:
    """ATR measurement specifically computed for stop loss buffer sizing."""
    status: str  # "AVAILABLE" or "UNAVAILABLE"
    value: float = 0.0
    period: int = 14
    timeframe: str = "1M"
    method: str = "SMA_TR_V1"
    last_closed_at_ms: int = 0
    bars_count: int = 0
    reason_code: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "value": round(self.value, 4),
            "period": self.period,
            "timeframe": self.timeframe,
            "method": self.method,
            "last_closed_at_ms": self.last_closed_at_ms,
            "bars_count": self.bars_count,
        }


@dataclass(frozen=True)
class StopBufferResult:
    """Breakdown of dynamic buffer components."""
    buffer_price: float
    components: Dict[str, float]
    dominant_source: str
    tick_size: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "buffer_price": round(self.buffer_price, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "dominant_source": self.dominant_source,
            "tick_size": self.tick_size,
        }


@dataclass(frozen=True)
class StopMetrics:
    """Calculated stop loss distance and risk metrics."""
    price_distance: float
    distance_ticks: int
    distance_atr: Optional[float] = None
    risk_usdt: Optional[float] = None
    delta_vs_legacy_sl: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "price_distance": round(self.price_distance, 4),
            "distance_ticks": self.distance_ticks,
            "distance_atr": round(self.distance_atr, 4) if self.distance_atr is not None else None,
            "risk_usdt": round(self.risk_usdt, 4) if self.risk_usdt is not None else None,
            "delta_vs_legacy_sl": round(self.delta_vs_legacy_sl, 4) if self.delta_vs_legacy_sl is not None else None,
        }


@dataclass(frozen=True)
class StopPlacementDecision:
    """Complete, self-contained stop placement decision for a candidate opportunity."""
    status: str  # StopStatus value
    selected_sl: float
    anchor: Optional[StopAnchor] = None
    buffer: Optional[StopBufferResult] = None
    metrics: Optional[StopMetrics] = None
    reasons: Tuple[str, ...] = ()
    policy_version: int = 1
    mode: str = StopPlacementMode.SHADOW_COMPARE.value
    decision_cutoff_ms: int = 0
    applied_variant: str = "A"  # "A" (legacy), "B" (anchor+tick), "C" (structural dynamic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "selected_sl": round(self.selected_sl, 2),
            "anchor": self.anchor.to_dict() if self.anchor else None,
            "buffer": self.buffer.to_dict() if self.buffer else None,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "reasons": list(self.reasons),
            "policy_version": self.policy_version,
            "mode": self.mode,
            "decision_cutoff_ms": self.decision_cutoff_ms,
            "applied_variant": self.applied_variant,
        }


@dataclass(frozen=True)
class VariantPlan:
    """A single execution variant (A, B, or C) for a given opportunity."""
    variant_id: str  # "A", "B", "C"
    stop_loss: float
    take_profit: float
    quantity: float
    net_rr: float
    gross_rr: float
    is_valid: bool
    can_execute: bool
    meets_min_rr: bool
    rejection_reason: Optional[str] = None
    buffer_price: float = 0.0
    dominant_buffer: str = "tick_floor"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "stop_loss": round(self.stop_loss, 2),
            "take_profit": round(self.take_profit, 2),
            "quantity": round(self.quantity, 4),
            "net_rr": round(self.net_rr, 2),
            "gross_rr": round(self.gross_rr, 2),
            "is_valid": self.is_valid,
            "can_execute": self.can_execute,
            "meets_min_rr": self.meets_min_rr,
            "rejection_reason": self.rejection_reason,
            "buffer_price": round(self.buffer_price, 4),
            "dominant_buffer": self.dominant_buffer,
        }


@dataclass(frozen=True)
class StopComparisonPlan:
    """Paired comparison of variants A, B, and C evaluated on the exact same opportunity."""
    opportunity_key: str
    model_id: str
    symbol: str
    direction: str
    planned_entry: float
    take_profit: float  # Identical across all variants
    as_of_ms: int
    variant_a: VariantPlan
    variant_b: VariantPlan
    variant_c: VariantPlan
    anchor: Optional[StopAnchor] = None
    atr_m1: Optional[float] = None
    atr_m5: Optional[float] = None
    spread: Optional[float] = None
    policy_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opportunity_key": self.opportunity_key,
            "model_id": self.model_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "planned_entry": round(self.planned_entry, 2),
            "take_profit": round(self.take_profit, 2),
            "as_of_ms": self.as_of_ms,
            "variant_a": self.variant_a.to_dict(),
            "variant_b": self.variant_b.to_dict(),
            "variant_c": self.variant_c.to_dict(),
            "anchor": self.anchor.to_dict() if self.anchor else None,
            "atr_m1": round(self.atr_m1, 4) if self.atr_m1 is not None else None,
            "atr_m5": round(self.atr_m5, 4) if self.atr_m5 is not None else None,
            "spread": round(self.spread, 4) if self.spread is not None else None,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class StopExitEvidence:
    """Exit trigger evidence captured when a position closes at or through stop loss."""
    price_basis: str
    trigger_level: float
    applied_exit_price: float
    quote_timestamp: Optional[int] = None
    evidence_reference: Optional[str] = None
    is_gap: bool = False
    gap_amount: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "price_basis": self.price_basis,
            "trigger_level": round(self.trigger_level, 2),
            "applied_exit_price": round(self.applied_exit_price, 2),
            "quote_timestamp": self.quote_timestamp,
            "evidence_reference": self.evidence_reference,
            "is_gap": self.is_gap,
            "gap_amount": round(self.gap_amount, 4),
        }
