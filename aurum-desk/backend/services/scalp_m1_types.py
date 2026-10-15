"""
backend/services/scalp_m1_types.py
V15.0 - Core types, enums, dataclasses, and configuration for Autonomous SMC Scalp Watcher.

Defines:
- ScalpMode: OFF, SHADOW, PAPER
- ModelIdentifier: STANDARD_SMC, SMC_M1_REVERSAL, SMC_M1_CONTINUATION, NY_DAILY_RESEARCH
- OpportunityStage: Funnel progression stages
- OpportunityReasonCode: Stable reason codes
- OpportunityEvaluation: Structured funnel record with latency metrics
- WatchedZone: Zone watch contract and proximity tracking
- M1ScalpConfig: Scalp parameters and safety thresholds
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


class ScalpMode(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    PAPER = "PAPER"


class ModelIdentifier(str, Enum):
    STANDARD_SMC = "STANDARD_SMC"
    SMC_M1_REVERSAL = "SMC_M1_REVERSAL"
    SMC_M1_CONTINUATION = "SMC_M1_CONTINUATION"
    NY_DAILY_RESEARCH = "NY_DAILY_RESEARCH"


class OpportunityStage(str, Enum):
    DATA_READY = "DATA_READY"
    ZONE_DETECTED = "ZONE_DETECTED"
    ZONE_WATCHED = "ZONE_WATCHED"
    PRICE_NEAR = "PRICE_NEAR"
    ZONE_TOUCHED = "ZONE_TOUCHED"
    TRIGGER_PENDING = "TRIGGER_PENDING"
    TRIGGER_CONFIRMED = "TRIGGER_CONFIRMED"
    PLAN_VALID = "PLAN_VALID"
    GUARD_BLOCKED = "GUARD_BLOCKED"
    ARMED = "ARMED"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class OpportunityReasonCode(str, Enum):
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    QUOTE_STALE = "QUOTE_STALE"
    WAITING_ZONE_TOUCH = "WAITING_ZONE_TOUCH"
    WAITING_M1_STRUCTURE = "WAITING_M1_STRUCTURE"
    WAITING_M1_RETEST = "WAITING_M1_RETEST"
    TRIGGER_EXPIRED = "TRIGGER_EXPIRED"
    ENTRY_TOO_FAR = "ENTRY_TOO_FAR"
    INSUFFICIENT_NET_REWARD = "INSUFFICIENT_NET_REWARD"
    GUARD_BLOCKED = "GUARD_BLOCKED"
    SESSION_CLOSED = "SESSION_CLOSED"
    DAILY_LIMIT_REACHED = "DAILY_LIMIT_REACHED"
    WAITING_LIQUIDITY_REFERENCE = "WAITING_LIQUIDITY_REFERENCE"
    WAITING_LIQUIDITY_TARGET = "WAITING_LIQUIDITY_TARGET"
    HYPOTHESIS_EXPIRED = "HYPOTHESIS_EXPIRED"
    HYPOTHESIS_STATE_CORRUPT = "HYPOTHESIS_STATE_CORRUPT"
    HYPOTHESIS_SCHEMA_UNSUPPORTED = "HYPOTHESIS_SCHEMA_UNSUPPORTED"
    HYPOTHESIS_STORE_UNAVAILABLE = "HYPOTHESIS_STORE_UNAVAILABLE"


class ZoneState(str, Enum):
    WATCHING = "WATCHING"
    NEAR = "NEAR"
    TOUCHED = "TOUCHED"
    WAITING_TRIGGER = "WAITING_TRIGGER"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class TargetDecision:
    """Canonical Target Decision contract (PHẦN 081, 082, 091)."""
    status: str                                     # CONFIRMED / UNAVAILABLE / INVALID_SIDE / INSUFFICIENT_DISTANCE
    target_type: str                                # LIQUIDITY_LEVEL / UNAVAILABLE
    price: Optional[float]
    source_level_id: Optional[str] = None
    known_at_ms: Optional[int] = None
    reason_codes: Tuple[str, ...] = ()
    label_vi: str = ""


@dataclass(frozen=True)
class OpportunityEvaluation:
    """Structured record for Opportunity Funnel transitions (PHẦN 026, 030)."""
    opportunity_id: str
    symbol: str = "XAUUSDT"
    model_id: str = ModelIdentifier.SMC_M1_REVERSAL.value
    model_version: str = "v15.0"
    direction: str = "LONG"
    session_id: str = ""
    zone_id: Optional[str] = None
    as_of_ms: int = 0
    stage: str = OpportunityStage.ZONE_DETECTED.value
    reason_codes: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    config_version: int = 1

    # Latency tracking metrics (PHẦN 030)
    market_event_received_at: Optional[int] = None
    evaluation_started_at: Optional[int] = None
    evaluation_completed_at: Optional[int] = None
    trigger_known_at: Optional[int] = None
    armed_at: Optional[int] = None
    filled_at: Optional[int] = None

    # Pricing & execution snapshot
    planned_entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    net_rr: Optional[float] = None
    order_id: Optional[str] = None
    watch_setup_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "symbol": self.symbol,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "direction": self.direction,
            "session_id": self.session_id,
            "zone_id": self.zone_id,
            "as_of_ms": self.as_of_ms,
            "stage": self.stage,
            "reason_codes": list(self.reason_codes),
            "evidence_ids": list(self.evidence_ids),
            "config_version": self.config_version,
            "market_event_received_at": self.market_event_received_at,
            "evaluation_started_at": self.evaluation_started_at,
            "evaluation_completed_at": self.evaluation_completed_at,
            "trigger_known_at": self.trigger_known_at,
            "armed_at": self.armed_at,
            "filled_at": self.filled_at,
            "planned_entry": self.planned_entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "net_rr": self.net_rr,
            "order_id": self.order_id,
            "watch_setup_id": self.watch_setup_id,
            "details": self.details,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> OpportunityEvaluation:
        return OpportunityEvaluation(
            opportunity_id=d["opportunity_id"],
            symbol=d.get("symbol", "XAUUSDT"),
            model_id=d.get("model_id", ModelIdentifier.SMC_M1_REVERSAL.value),
            model_version=d.get("model_version", "v15.0"),
            direction=d.get("direction", "LONG"),
            session_id=d.get("session_id", ""),
            zone_id=d.get("zone_id"),
            as_of_ms=int(d.get("as_of_ms", 0)),
            stage=d.get("stage", OpportunityStage.ZONE_DETECTED.value),
            reason_codes=tuple(d.get("reason_codes") or ()),
            evidence_ids=tuple(d.get("evidence_ids") or ()),
            config_version=int(d.get("config_version", 1)),
            market_event_received_at=d.get("market_event_received_at"),
            evaluation_started_at=d.get("evaluation_started_at"),
            evaluation_completed_at=d.get("evaluation_completed_at"),
            trigger_known_at=d.get("trigger_known_at"),
            armed_at=d.get("armed_at"),
            filled_at=d.get("filled_at"),
            planned_entry=d.get("planned_entry"),
            stop_loss=d.get("stop_loss"),
            take_profit=d.get("take_profit"),
            net_rr=d.get("net_rr"),
            order_id=d.get("order_id"),
            watch_setup_id=d.get("watch_setup_id"),
            details=dict(d.get("details") or {}),
        )


@dataclass
class WatchedZone:
    """Watched SMC POI zone contract (PHẦN 033, 038)."""
    zone_id: str
    symbol: str
    timeframe: str                           # "15M" or "5M"
    zone_type: str                           # "FVG" or "OB"
    direction: str                           # "BULLISH" / "LONG" or "BEARISH" / "SHORT"
    top: float
    bottom: float                            # top must be > bottom
    created_at: int                          # Candle open timestamp where zone formed
    known_at: int                            # Timestamp when zone was fully confirmed
    source_evidence_ids: Tuple[str, ...] = ()
    freshness_state: str = "FRESH"           # "FRESH", "TESTED", "MITIGATED"
    touch_count: int = 0
    invalidation_level: float = 0.0          # Price level that voids this zone
    model_version: str = "v15.0"
    state: str = ZoneState.WATCHING.value
    last_evaluated_at: int = 0
    proximity_distance_atr: Optional[float] = None
    rank_score: float = 0.0

    def __post_init__(self):
        if self.top <= self.bottom:
            raise ValueError(f"INVALID_ZONE_BOUNDS: top ({self.top}) must be > bottom ({self.bottom})")
        if self.invalidation_level <= 0.0:
            if self.direction in ("BEARISH", "SHORT"):
                object.__setattr__(self, "invalidation_level", round(self.top + 0.1 * (self.top - self.bottom), 2))
            else:
                object.__setattr__(self, "invalidation_level", round(self.bottom - 0.1 * (self.top - self.bottom), 2))

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def height(self) -> float:
        return self.top - self.bottom

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "zone_type": self.zone_type,
            "direction": self.direction,
            "top": self.top,
            "bottom": self.bottom,
            "midpoint": self.midpoint,
            "height": self.height,
            "created_at": self.created_at,
            "known_at": self.known_at,
            "source_evidence_ids": list(self.source_evidence_ids),
            "freshness_state": self.freshness_state,
            "touch_count": self.touch_count,
            "invalidation_level": self.invalidation_level,
            "model_version": self.model_version,
            "state": self.state,
            "last_evaluated_at": self.last_evaluated_at,
            "proximity_distance_atr": self.proximity_distance_atr,
            "rank_score": self.rank_score,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> WatchedZone:
        return WatchedZone(
            zone_id=d["zone_id"],
            symbol=d["symbol"],
            timeframe=d.get("timeframe", "15M"),
            zone_type=d.get("zone_type", "FVG"),
            direction=d.get("direction", "BULLISH"),
            top=float(d["top"]),
            bottom=float(d["bottom"]),
            created_at=int(d["created_at"]),
            known_at=int(d["known_at"]),
            source_evidence_ids=tuple(d.get("source_evidence_ids") or ()),
            freshness_state=d.get("freshness_state", "FRESH"),
            touch_count=int(d.get("touch_count", 0)),
            invalidation_level=float(d.get("invalidation_level", 0.0)),
            model_version=d.get("model_version", "v15.0"),
            state=d.get("state", ZoneState.WATCHING.value),
            last_evaluated_at=int(d.get("last_evaluated_at", 0)),
            proximity_distance_atr=d.get("proximity_distance_atr"),
            rank_score=float(d.get("rank_score", 0.0)),
        )


@dataclass(frozen=True)
class M1ScalpConfig:
    """Configuration and thresholds for M1 Scalp & Watcher (PHẦN 050)."""
    scalp_m1_enabled: bool = False
    scalp_m1_mode: str = ScalpMode.OFF.value
    daily_ny_research_enabled: bool = False

    # Structure & timing parameters
    min_closed_m1_bars: int = 15
    pivot_left_bars: int = 2
    pivot_right_bars: int = 2
    displacement_body_atr_min: float = 0.70  # Body >= 0.7 * ATR1M qualifies M1 displacement
    displacement_atr_period: int = 14
    min_atr_bars: int = 20
    sl_buffer_ticks: int = 5
    tick_size: float = 0.01

    # Net RR & Sizing
    min_net_rr: float = 2.0
    fallback_risk_pct: float = 0.10          # Safe risk cap for research/scalp (0.10%)

    # TTL & Proximity Hysteresis (PHẦN 039)
    near_zone_enter_atr: float = 0.30        # Distance <= 0.30 ATR -> Enter NEAR
    near_zone_exit_atr: float = 0.50         # Distance > 0.50 ATR -> Exit NEAR
    max_zone_age_ms: int = 4 * 60 * 60 * 1000   # 4 hours max zone age
    max_trigger_age_ms: int = 6 * 60 * 1000     # 6 minutes max trigger staleness
    max_entry_distance_atr: float = 1.50     # Entry price within 1.5 ATR of current
    max_active_zones: int = 8                # Bounded watchlist size
    config_version: int = 1

    def __post_init__(self):
        if self.near_zone_enter_atr >= self.near_zone_exit_atr:
            raise ValueError("INVALID_CONFIG: near_zone_enter_atr must be < near_zone_exit_atr for hysteresis")
        if self.displacement_body_atr_min <= 0:
            raise ValueError("INVALID_CONFIG: displacement_body_atr_min must be > 0")
        if self.min_net_rr <= 0:
            raise ValueError("INVALID_CONFIG: min_net_rr must be > 0")
        if self.max_active_zones <= 0:
            raise ValueError("INVALID_CONFIG: max_active_zones must be > 0")


import logging

logger = logging.getLogger(__name__)

active_scalp_config = M1ScalpConfig()


def load_effective_scalp_config(db: Any = None) -> M1ScalpConfig:
    """
    PHẦN 016, 017: Single source of config persistence for M1 Scalp & Watcher.
    Loads persisted overrides from SystemConfig in DB, validates types and boundaries,
    and returns an immutable M1ScalpConfig.
    Does NOT commit when reading. Does NOT call network.
    """
    if db is None:
        return active_scalp_config

    try:
        import models
        rows = db.query(models.SystemConfig).filter(
            models.SystemConfig.key.in_([
                "scalp_m1_mode",
                "daily_ny_research_enabled",
                "min_closed_m1_bars",
                "displacement_body_atr_min",
                "min_net_rr",
                "fallback_risk_pct",
                "near_zone_enter_atr",
                "near_zone_exit_atr",
                "max_active_zones",
                "max_trigger_age_ms",
                "max_entry_distance_atr",
                "config_version",
            ])
        ).all()
        config_map = {r.key: r.value for r in rows}
    except Exception as e:
        logger.warning(f"Failed to query SystemConfig for scalp config: {e}")
        return active_scalp_config

    overrides: Dict[str, Any] = {}

    if "scalp_m1_mode" in config_map:
        val = str(config_map["scalp_m1_mode"]).strip().upper()
        if val in (ScalpMode.OFF.value, ScalpMode.SHADOW.value, ScalpMode.PAPER.value):
            overrides["scalp_m1_mode"] = val
            overrides["scalp_m1_enabled"] = (val != ScalpMode.OFF.value)

    if "daily_ny_research_enabled" in config_map:
        val = str(config_map["daily_ny_research_enabled"]).strip().lower()
        if val in ("true", "1", "yes"):
            overrides["daily_ny_research_enabled"] = True
        elif val in ("false", "0", "no"):
            overrides["daily_ny_research_enabled"] = False

    for num_key, typ in [
        ("min_closed_m1_bars", int),
        ("displacement_body_atr_min", float),
        ("min_net_rr", float),
        ("fallback_risk_pct", float),
        ("near_zone_enter_atr", float),
        ("near_zone_exit_atr", float),
        ("max_active_zones", int),
        ("max_trigger_age_ms", int),
        ("max_entry_distance_atr", float),
        ("config_version", int),
    ]:
        if num_key in config_map:
            try:
                parsed_val = typ(config_map[num_key])
                overrides[num_key] = parsed_val
            except (ValueError, TypeError):
                pass

    if not overrides:
        return active_scalp_config

    base_dict = active_scalp_config.__dict__.copy()
    base_dict.update(overrides)
    try:
        return M1ScalpConfig(**base_dict)
    except Exception as e:
        logger.warning(f"Invalid persisted scalp config overrides: {e}, falling back to defaults")
        return active_scalp_config


def get_scalp_config(**overrides: Any) -> M1ScalpConfig:
    if not overrides:
        return active_scalp_config
    base_dict = active_scalp_config.__dict__.copy()
    base_dict.update(overrides)
    return M1ScalpConfig(**base_dict)
