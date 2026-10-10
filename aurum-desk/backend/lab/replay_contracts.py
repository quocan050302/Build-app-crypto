"""
AURUM DESK — REPLAY CONTRACTS (V13.6)
Typed internal domain models for pending orders, market events, positions, policy snapshots, and session audits.
Supports both object attribute access and dictionary-like subscripting for seamless backward compatibility.
"""

from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class EventPhase(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    QUOTE = "QUOTE"
    END = "END"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


def validate_finite_positive(val: Any, name: str) -> float:
    try:
        f = float(val)
        if f <= 0.0 or not (-1e15 < f < 1e15):
            raise ValueError(f"{name} must be finite positive, got {val}")
        return f
    except Exception as e:
        raise ValueError(f"{name} invalid: {e}")


def validate_direction(direction: str) -> str:
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"Invalid direction {direction}, must be LONG or SHORT")
    return direction


def validate_time_ref(t_ms: Any, name: str) -> int:
    try:
        t = int(t_ms)
        if t <= 0:
            raise ValueError(f"{name} must be positive timestamp in ms, got {t_ms}")
        return t
    except Exception as e:
        raise ValueError(f"{name} invalid: {e}")


@dataclass
class ReplayPendingOrder:
    order_id: str
    setup_id: str
    session_id: str
    entry_type: str  # SMC_CONFIRMED, SMC_CONTEXT_SCHEDULED_PAPER
    direction: str   # LONG, SHORT
    decision_ms: int
    earliest_execution_ms: int
    expiry_ms: int
    planned_entry: float
    planned_sl: float
    planned_tp: float
    planned_net_rr: float
    quantity: float
    leverage: int = 30
    margin_mode: str = "ISOLATED"
    stop_model: str = "STRUCTURAL"
    target_model: str = "STRUCTURAL"
    target_source: str = "UNKNOWN"
    status: str = "CREATED"  # CREATED, SUBMITTED, PENDING, FILLED, REJECTED, EXPIRED, CANCELLED
    rejection_reason: Optional[str] = None
    created_at_ms: int = 0
    executed_at_ms: Optional[int] = None
    fill_price: Optional[float] = None
    missing_confirmations: Optional[List[str]] = None
    confidence_kind: Optional[str] = None
    entry_model: Optional[str] = None
    reason: Optional[str] = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReplayMarketEvent:
    kind: str  # OPEN, CLOSE, QUOTE, END
    timestamp: int
    timeframe: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float = 0.0
    sequence: int = 0
    bar_id: Optional[str] = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass
class ReplayPosition:
    position_id: str
    order_id: str
    setup_id: str
    session_id: str
    entry_type: str
    direction: str
    entry_time: int
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float
    leverage: int
    margin_mode: str
    initial_risk_usdt: float
    planned_net_rr: float
    entry_fee: float
    stop_model: str = "STRUCTURAL"
    target_model: str = "STRUCTURAL"
    target_source: str = "UNKNOWN"
    status: str = "OPEN"  # OPEN, CLOSED
    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    exit_cause: Optional[str] = None
    exit_fee: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    realized_r: Optional[float] = None
    is_ambiguous: bool = False
    holding_bars: int = 0
    entry_slippage: float = 0.0
    net_rr_fill: Optional[float] = None
    gross_rr: Optional[float] = None
    net_risk_usdt: Optional[float] = None
    net_reward_usdt: Optional[float] = None
    strategy_family: str = "SMC_MOMENTUM"
    ny_session_id: Optional[str] = None
    entry_session: str = "NEW_YORK"
    exit_session: str = "NEW_YORK"
    trade_day_vn: Optional[str] = None
    ny_session_date: Optional[str] = None
    decision_time: Optional[int] = None
    execution_time: Optional[int] = None
    reason: Optional[str] = None
    missing_confirmations: Optional[List[str]] = None
    confidence_kind: Optional[str] = None
    entry_model: Optional[str] = None
    tp_is_maker: bool = False

    @property
    def id(self) -> str:
        return self.position_id

    @property
    def fees(self) -> float:
        return round(self.entry_fee + self.exit_fee, 4)

    @property
    def total_fees(self) -> float:
        return round(self.entry_fee + self.exit_fee, 4)

    @property
    def slippage(self) -> float:
        return round(self.entry_slippage, 4)

    def __getitem__(self, key: str) -> Any:
        if key == "id":
            return self.position_id
        if key in ("fees", "total_fees"):
            return self.total_fees
        if key == "slippage":
            return self.slippage
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "id":
            return self.position_id
        if key in ("fees", "total_fees"):
            return self.total_fees
        if key == "slippage":
            return self.slippage
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key) or key in ("id", "fees", "total_fees", "slippage")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["id"] = self.position_id
        d["fees"] = self.total_fees
        d["total_fees"] = self.total_fees
        d["slippage"] = self.slippage
        return d


@dataclass
class PolicySnapshot:
    snapshot_id: str
    source: str = "FOLLOW_APP_SNAPSHOT"  # FOLLOW_APP_SNAPSHOT, RESEARCH_CUSTOM
    strategy_variant: str = "NY_ADAPTIVE"
    entry_cadence: str = "DAILY_PAPER"
    session_windows: List[Tuple[str, str]] = field(default_factory=lambda: [("08:30", "12:00"), ("13:00", "15:30")])
    deadline_hour: int = 14
    deadline_minute: int = 30
    risk_pct: float = 0.50
    quota_risk_pct: float = 0.10
    ny_max_fills: int = 3
    daily_max_fills: int = 3
    cooldown_minutes: int = 30
    consecutive_losses_limit: int = 2
    daily_loss_budget_pct: float = 1.50
    leverage: int = 30
    margin_mode: str = "ISOLATED"
    min_net_rr: float = 2.0
    version: str = "v13.6"


@dataclass
class SessionDataAudit:
    ny_date: str
    tradable_hours: float
    expected_15m_bars: int
    observed_15m_bars: int
    expected_5m_bars: int
    observed_5m_bars: int
    gaps: List[Dict[str, Any]] = field(default_factory=list)
    coverage_pct: float = 100.0
    is_complete: bool = True
    is_market_open: bool = True
    warmup_status: str = "COMPLETE"  # COMPLETE, INCOMPLETE, UNKNOWN
    reasons: List[str] = field(default_factory=list)
