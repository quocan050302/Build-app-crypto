from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional, Dict, Any

# Candle Schemas
class CandleBase(BaseModel):
    symbol: str
    timeframe: str
    timestamp: int  # epoch ms
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = True

class CandleCreate(CandleBase):
    pass

class Candle(CandleBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

class CandleListResponse(BaseModel):
    symbol: str
    timeframe: str
    candles: List[Candle]
    count: int
    is_stale: bool = False
    last_candle_time: Optional[int] = None
    server_time: int

# Paper Trade & Risk/Reward Overlay Schemas
class TargetDetail(BaseModel):
    price: float
    close_fraction: float = 1.0
    gross_rr: float

class PaperOrderBase(BaseModel):
    setup_id: Optional[str] = None
    signal_id: Optional[str] = None
    instrument: str = "XAUUSDT"
    direction: str  # LONG / SHORT
    state: str = "candidate"  # draft, candidate, armed, paper_open, closed, expired, invalidated, cancelled
    order_type: str = "MARKET"
    timeframe: str = "15M"
    planned_entry: float
    actual_entry: Optional[float] = None
    stop_loss: float
    take_profit: float
    actual_exit: Optional[float] = None
    quantity: float
    initial_risk_usdt: float
    risk_pct: float = 0.5
    gross_rr: float = 2.0
    estimated_net_rr: float = 1.9
    fees_assumption: float = 0.10
    slippage_assumption: float = 0.10
    strategy_version: str = "1.0.0"

class PaperOrderCreate(PaperOrderBase):
    checklist_snapshot: Optional[str] = None
    lessons_retrieved: Optional[str] = None

class PaperOrderResponse(PaperOrderBase):
    id: str
    realized_pnl_net: Optional[float] = None
    realized_r: Optional[float] = None
    exit_cause: Optional[str] = None
    invalidation_reason: Optional[str] = None
    created_at: int
    armed_at: Optional[int] = None
    opened_at: Optional[int] = None
    closed_at: Optional[int] = None
    expires_at: Optional[int] = None
    checklist_snapshot: Optional[str] = None
    lessons_retrieved: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

# Risk/Reward Position Overlay schema for Chart
class RiskRewardOverlayData(BaseModel):
    id: str
    setup_id: Optional[str] = None
    signal_id: Optional[str] = None
    trade_id: Optional[str] = None
    instrument: str = "XAUUSDT"
    direction: str  # LONG / SHORT
    state: str  # draft, candidate, armed, paper_open, closed, invalidated
    planned_entry: float
    actual_entry: Optional[float] = None
    stop_loss: float
    targets: List[TargetDetail] = []
    quantity: float
    initial_risk_usdt: float
    risk_pct: float
    gross_rr: float
    estimated_net_rr: float
    fees_assumption: float
    slippage_assumption: float
    created_at: int
    armed_at: Optional[int] = None
    opened_at: Optional[int] = None
    closed_at: Optional[int] = None
    expires_at: Optional[int] = None
    actual_exit: Optional[float] = None
    realized_pnl_net: Optional[float] = None
    realized_r: Optional[float] = None
    invalidation_reason: Optional[str] = None
    strategy_version: str = "1.0.0"

# SMC Market Analysis Schemas
class SwingPoint(BaseModel):
    index: int
    timestamp: int
    price: float
    type: str  # HIGH / LOW
    confirmed_at: Optional[int] = None

class FVGItem(BaseModel):
    type: str  # BULLISH_FVG / BEARISH_FVG
    top: float
    bottom: float
    timestamp: int
    mitigated: bool
    state: str = "confirmed"  # created, confirmed, partially_mitigated, fully_mitigated, expired

class LiquidityLevel(BaseModel):
    price: float
    type: str  # PDH, PDL, ASIA_HIGH, ASIA_LOW, SWING_HIGH, SWING_LOW
    timestamp: int
    status: str = "active"  # active, swept

class ChecklistItem(BaseModel):
    id: str
    label: str
    status: str  # PASS / FAIL / WAITING
    detail: str
    is_hard_filter: bool = True

class SMCAnalysisResponse(BaseModel):
    status: str
    symbol: str
    timeframe: str
    current_price: float
    bid: float
    ask: float
    trend: str  # BULLISH, BEARISH, RANGING, UNKNOWN
    htf_bias: str  # D/4H bias
    h1_alignment: str
    zone: str  # PREMIUM / DISCOUNT / EQUILIBRIUM
    equilibrium: float
    dealing_range: Dict[str, float]
    swing_high: float
    swing_low: float
    last_event: str
    atr: float
    active_fvgs: List[FVGItem]
    liquidity_levels: List[LiquidityLevel]
    checklist: List[ChecklistItem]
    active_signal: Optional[RiskRewardOverlayData] = None
    engine_state: str  # starting, collecting_data, analyzing, waiting_setup, candidate, armed, triggered, paper_open, blocked_news, blocked_risk, stale_data
    last_analyzed_at: int
    last_data_at: int
    missing_conditions: List[str] = []

# Day Audit & Risk
class DayAuditResponse(BaseModel):
    date_str: str
    initial_equity: float
    current_equity: float
    realized_pnl_today: float
    fills_count: int
    max_daily_fills: int = 3
    consecutive_losses: int
    max_consecutive_losses: int = 2
    daily_loss_limit_usdt: float
    cooldown_remaining_sec: int = 0
    is_blocked: bool
    block_reason: Optional[str] = None
    auto_paper_active: bool = True

# Economic News Schemas
class EconomicNewsItem(BaseModel):
    id: Optional[int] = None
    source_id: Optional[str] = None
    title: str
    country: str = "USD"
    currency: str = "USD"
    impact: str  # High, Medium, Low, Holiday
    scheduled_at: int
    received_at: int
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None
    revised: Optional[str] = None

class NewsBlackoutStatus(BaseModel):
    is_blackout: bool
    reason: Optional[str] = None
    active_event: Optional[str] = None
    remaining_minutes: int = 0

# Research Reports
class ResearchReportResponse(BaseModel):
    id: int
    report_type: str
    session_name: str
    created_at: int
    d_4h_bias: str
    h1_alignment: str
    content_markdown: str
    scenarios: Optional[Dict[str, Any]] = None
    strategy_version: str

# Lessons
class LessonItem(BaseModel):
    id: int
    created_at: int
    title: str
    category: str
    related_trade_id: Optional[str] = None
    setup_type: Optional[str] = None
    session: Optional[str] = None
    reflection: str
    action_rule: str
    is_hard_filter: bool
    is_approved: bool

# Health Status
class SystemHealthResponse(BaseModel):
    status: str
    timestamp: int
    feed_connected: bool
    db_connected: bool
    collector_alive: bool
    data_freshness_sec: float
    data_is_stale: bool
    engine_state: str
    active_timeframe: str
    candle_count: int
    strategy_version: str
    paper_trading_mode: str = "SIMULATION"
