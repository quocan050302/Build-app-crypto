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
    WAITING_VOLATILITY_EVIDENCE = "WAITING_VOLATILITY_EVIDENCE"
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
    bound_leg: Optional[Any] = None
    reasons: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "anchor": self.anchor.to_dict() if self.anchor else None,
            "bound_leg": self.bound_leg.to_dict() if (self.bound_leg and hasattr(self.bound_leg, "to_dict")) else None,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class StopProtectiveDistanceResult:
    """Breakdown of minimum entry-relative protective distance components (V17.7)."""
    min_distance: float
    components: Dict[str, float]
    dominant_source: str
    tick_size: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_distance": round(self.min_distance, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "dominant_source": self.dominant_source,
            "tick_size": self.tick_size,
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
    min_protective_ticks: int = 10
    min_distance_atr_m1_mult: float = 0.50
    min_distance_atr_m5_mult: float = 0.20
    min_distance_spread_mult: float = 2.0
    max_stop_distance_atr: float = 4.0
    max_spread_usd: float = 2.0
    algorithm_version: str = "V17_7_1"

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
            "min_protective_ticks": self.min_protective_ticks,
            "min_distance_atr_m1_mult": self.min_distance_atr_m1_mult,
            "min_distance_atr_m5_mult": self.min_distance_atr_m5_mult,
            "min_distance_spread_mult": self.min_distance_spread_mult,
            "max_stop_distance_atr": self.max_stop_distance_atr,
            "max_spread_usd": self.max_spread_usd,
            "algorithm_version": self.algorithm_version,
        }


@dataclass(frozen=True)
class StopATRResult:
    """ATR measurement specifically computed for stop loss buffer sizing (V17.7.3 P1-01)."""
    status: str  # "AVAILABLE" or "UNAVAILABLE"
    value: float = 0.0
    period: int = 14
    timeframe: str = "1M"
    method: str = "SMA_TR_V1"
    last_closed_at_ms: int = 0
    bars_count: int = 0
    reason_code: Optional[str] = None
    symbol: Optional[str] = None
    source: str = "CANDLE_CLOSED"
    source_candle_count: int = 0
    last_closed_candle_timestamp: int = 0
    calculated_as_of: int = 0
    decision_cutoff: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "value": round(self.value, 4),
            "period": self.period,
            "timeframe": self.timeframe,
            "method": self.method,
            "last_closed_at_ms": self.last_closed_at_ms,
            "bars_count": self.bars_count,
            "reason_code": self.reason_code,
            "symbol": self.symbol,
            "source": self.source,
            "source_candle_count": self.source_candle_count or self.bars_count,
            "last_closed_candle_timestamp": self.last_closed_candle_timestamp or self.last_closed_at_ms,
            "calculated_as_of": self.calculated_as_of,
            "decision_cutoff": self.decision_cutoff,
        }


@dataclass(frozen=True)
class StopBufferResult:
    """Breakdown of dynamic buffer components (V17.7.4)."""
    buffer_price: float
    components: Dict[str, float]
    dominant_source: str
    tick_size: float
    raw_atr_m1: Optional[float] = None
    raw_atr_m5: Optional[float] = None

    def __post_init__(self):
        # Strict V17.7.4: components dictionary stores WEIGHTED buffer contributions
        # (e.g. k_m1 * raw_atr_m1), NOT authoritative raw ATR metrics.
        # Raw ATR must NEVER be inferred or reverse-calculated from weighted components.
        # If raw_atr_m1 or raw_atr_m5 is not explicitly provided as verified evidence,
        # it remains strictly None.
        pass

    def to_dict(self) -> Dict[str, Any]:
        return {
            "buffer_price": round(self.buffer_price, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "dominant_source": self.dominant_source,
            "tick_size": self.tick_size,
            "raw_atr_m1": round(self.raw_atr_m1, 4) if self.raw_atr_m1 is not None else None,
            "raw_atr_m5": round(self.raw_atr_m5, 4) if self.raw_atr_m5 is not None else None,
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
class SelectedStopLeg:
    """Descriptor capturing the exact structural leg and break linkage for protective stop placement."""
    model_id: str
    direction: str
    instance_id: str
    leg_id: str
    origin_pivot_id: Optional[str] = None
    origin_price: Optional[float] = None
    origin_occurred_at_ms: Optional[int] = None
    origin_known_at_ms: Optional[int] = None
    broken_reference_id: Optional[str] = None
    broken_reference_price: Optional[float] = None
    break_bar_id: Optional[str] = None
    break_open_ms: Optional[int] = None
    break_known_at_ms: Optional[int] = None
    fvg_id: Optional[str] = None
    fvg_middle_bar_ms: Optional[int] = None
    fvg_confirmed_at_ms: Optional[int] = None
    source_bar_ids: Tuple[str, ...] = ()
    decision_cutoff_ms: int = 0
    price_basis: str = PriceBasis.CANDLE_LAST.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "direction": self.direction,
            "instance_id": self.instance_id,
            "leg_id": self.leg_id,
            "origin_pivot_id": self.origin_pivot_id,
            "origin_price": self.origin_price,
            "origin_occurred_at_ms": self.origin_occurred_at_ms,
            "origin_known_at_ms": self.origin_known_at_ms,
            "broken_reference_id": self.broken_reference_id,
            "broken_reference_price": self.broken_reference_price,
            "break_bar_id": self.break_bar_id,
            "break_open_ms": self.break_open_ms,
            "break_known_at_ms": self.break_known_at_ms,
            "fvg_id": self.fvg_id,
            "fvg_middle_bar_ms": self.fvg_middle_bar_ms,
            "fvg_confirmed_at_ms": self.fvg_confirmed_at_ms,
            "source_bar_ids": list(self.source_bar_ids),
            "decision_cutoff_ms": self.decision_cutoff_ms,
            "price_basis": self.price_basis,
        }


@dataclass(frozen=True)
class ProtectedLegEpisode:
    """
    AURUM V17.5.6 (Appendix C1):
    Typed structural displacement episode proof binding the selected reference, break bar,
    and eligible protective origin candidates.
    """
    status: str  # "AVAILABLE" or failure reason code
    model_id: str
    direction: str
    reference_id: Optional[str] = None
    reference_price: Optional[float] = None
    reference_occurred_at_ms: Optional[int] = None
    reference_known_at_ms: Optional[int] = None
    break_bar_id: Optional[str] = None
    break_open_ms: Optional[int] = None
    break_known_at_ms: Optional[int] = None
    decision_cutoff_ms: int = 0
    episode_start_ms: Optional[int] = None
    episode_end_ms: Optional[int] = None
    eligible_origin_ids: Tuple[str, ...] = ()
    source_bar_ids: Tuple[str, ...] = ()
    boundary_source: Optional[str] = "CONFIRMED_STRUCTURE_NODES"
    derivation_version: int = 1
    reasons: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "model_id": self.model_id,
            "direction": self.direction,
            "reference_id": self.reference_id,
            "reference_price": round(self.reference_price, 4) if self.reference_price is not None else None,
            "reference_occurred_at_ms": self.reference_occurred_at_ms,
            "reference_known_at_ms": self.reference_known_at_ms,
            "break_bar_id": self.break_bar_id,
            "break_open_ms": self.break_open_ms,
            "break_known_at_ms": self.break_known_at_ms,
            "decision_cutoff_ms": self.decision_cutoff_ms,
            "episode_start_ms": self.episode_start_ms,
            "episode_end_ms": self.episode_end_ms,
            "eligible_origin_ids": list(self.eligible_origin_ids),
            "source_bar_ids": list(self.source_bar_ids),
            "boundary_source": self.boundary_source,
            "derivation_version": self.derivation_version,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class StopPlacementDecision:
    """Complete, self-contained stop placement decision for a candidate opportunity."""
    status: str  # StopStatus value
    selected_sl: Optional[float] = None
    anchor: Optional[StopAnchor] = None
    bound_leg: Optional[Any] = None
    buffer: Optional[StopBufferResult] = None
    metrics: Optional[StopMetrics] = None
    reasons: Tuple[str, ...] = ()
    policy_version: int = 1
    mode: str = StopPlacementMode.SHADOW_COMPARE.value
    decision_cutoff_ms: int = 0
    applied_variant: str = "A"  # "A" (legacy), "B" (anchor+tick), "C" (structural dynamic)
    volatility_context: Optional[Dict[str, Any]] = None
    protective_distance: Optional[float] = None
    expansion_applied: bool = False
    distance_assessment: Optional[str] = "DISTANCE_NORMAL"
    algorithm_version: str = "V17_7_1"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "selected_sl": round(self.selected_sl, 2) if self.selected_sl is not None else None,
            "anchor": self.anchor.to_dict() if self.anchor else None,
            "bound_leg": self.bound_leg.to_dict() if (self.bound_leg and hasattr(self.bound_leg, "to_dict")) else None,
            "buffer": self.buffer.to_dict() if self.buffer else None,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "reasons": list(self.reasons),
            "policy_version": self.policy_version,
            "mode": self.mode,
            "decision_cutoff_ms": self.decision_cutoff_ms,
            "applied_variant": self.applied_variant,
            "volatility_context": self.volatility_context,
            "protective_distance": round(self.protective_distance, 4) if self.protective_distance is not None else None,
            "expansion_applied": self.expansion_applied,
            "distance_assessment": self.distance_assessment,
            "algorithm_version": self.algorithm_version,
        }


@dataclass(frozen=True)
class StopApplicationResult:
    """Typed result of applying stop placement policy to a trading candidate."""
    allowed: bool
    selected_sl: Optional[float]
    target: float
    calculation: Optional[Any]
    decision: StopPlacementDecision
    comparison: Optional[StopComparisonPlan]
    reason_codes: Tuple[str, ...] = ()

    def __iter__(self):
        yield self.selected_sl
        yield self.target
        yield self.calculation
        yield self.decision
        yield self.comparison

    def __getitem__(self, idx: int) -> Any:
        return (self.selected_sl, self.target, self.calculation, self.decision, self.comparison)[idx]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "selected_sl": round(self.selected_sl, 2) if self.selected_sl is not None else None,
            "target": round(self.target, 2),
            "decision": self.decision.to_dict() if self.decision else None,
            "comparison": self.comparison.to_dict() if self.comparison else None,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class StopComparisonState:
    """Mutable runtime research state tracked separately from the immutable plan."""
    plan_id: str
    opportunity_key: str
    status: str = "WAIT_ENTRY"  # WAIT_ENTRY, OPEN, CLOSED, EXPIRED, CENSORED, GAP
    last_event_key: Optional[str] = None
    variant_states: Dict[str, Any] = field(default_factory=dict)
    as_of_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "opportunity_key": self.opportunity_key,
            "status": self.status,
            "last_event_key": self.last_event_key,
            "variant_states": self.variant_states,
            "as_of_ms": self.as_of_ms,
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
    entry_intent: str = "MARKET"
    order_type: str = "MARKET"
    decision_cutoff_ms: int = 0
    expires_at_ms: Optional[int] = None
    cost_snapshot: Optional[Dict[str, Any]] = None
    context_status: str = "CONTEXT_INCOMPLETE"
    context_reason: Optional[str] = None
    provenance: Optional[str] = None

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
            "entry_intent": self.entry_intent,
            "order_type": self.order_type,
            "decision_cutoff_ms": self.decision_cutoff_ms,
            "expires_at_ms": self.expires_at_ms,
            "cost_snapshot": self.cost_snapshot,
            "context_status": self.context_status,
            "context_reason": self.context_reason,
            "provenance": self.provenance,
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


@dataclass(frozen=True)
class ComparisonEntryGateResult:
    """
    AURUM V17.5.5 (Appendix A1):
    Strict immutable decision returned by evaluate_stop_comparison_entry_gate.
    Ensures research variants cannot transition to OPEN without verified context and costs.
    """
    allowed: bool
    reason_code: str
    explanation: str
    context_status: Optional[str] = None
    cost_available: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "explanation": self.explanation,
            "context_status": self.context_status,
            "cost_available": self.cost_available,
        }
