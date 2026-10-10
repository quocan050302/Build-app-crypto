"""
AURUM DESK — REPLAY CONTRACTS (V13.5)
Typed internal domain models for pending orders, market events, positions, policy snapshots, and session audits.
Strictly decoupled from UI rendering, external network transports, and live trading state.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple


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
    version: str = "v13.5"


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
