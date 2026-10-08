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
    state: str = "candidate"  # draft, candidate, armed, paper_open, closed, expired, invalidated, cancelled, rejected
    order_type: str = "MARKET"
    timeframe: str = "15M"
    planned_entry: float
    actual_entry: Optional[float] = None
    stop_loss: float
    take_profit: float
    actual_exit: Optional[float] = None
    quantity: float
    initial_risk_usdt: float
    risk_pct: float = 0.25
    gross_rr: float = 2.0
    estimated_net_rr: float = 1.9
    fees_assumption: float = 0.10
    slippage_assumption: float = 0.10
    leverage: int = 5
    margin_mode: str = "ISOLATED"
    estimated_liquidation: Optional[float] = None
    initial_margin: Optional[float] = None
    strategy_version: str = "1.0.0"

class ArmSetupRequest(BaseModel):
    setup_id: str
    setup_instance_id: Optional[str] = None
    expected_revision: Optional[int] = None
    expected_direction: str  # "LONG" or "SHORT"
    idempotency_key: Optional[str] = None
    order_type: Optional[str] = None  # None/MARKET/LIMIT/STOP

class PaperOrderCreate(PaperOrderBase):
    checklist_snapshot: Optional[str] = None
    lessons_retrieved: Optional[str] = None
    expected_direction: Optional[str] = None
    expected_revision: Optional[int] = None
    setup_instance_id: Optional[str] = None
    idempotency_key: Optional[str] = None

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
    leverage: int = 5
    margin_mode: str = "ISOLATED"
    estimated_liquidation: Optional[float] = None
    initial_margin: Optional[float] = None
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

# Watch Setups (Upcoming Plans & Setups)
class WatchSetupItem(BaseModel):
    id: str
    version: int
    strategy: str
    direction: str
    timeframe: str
    state: str
    htf_bias: str
    h1_alignment: str
    poi_zone: Optional[str] = None
    trigger_mode: str
    provisional_entry: float
    provisional_sl: float
    provisional_tp: float
    confirmed_entry: Optional[float] = None
    confirmed_sl: Optional[float] = None
    confirmed_tp: Optional[float] = None
    invalidation_price: float
    invalidation_reason: Optional[str] = None
    gross_rr: float
    net_rr: float
    risk_usdt: float
    quantity: float
    leverage: int
    margin_mode: str
    risk_pct: float = 0.25
    config_version: int = 1
    estimated_liquidation: Optional[float] = None
    conditions_met: Optional[List[str]] = None
    conditions_remaining: Optional[List[str]] = None
    distance_to_entry_atr: Optional[float] = None
    distance_to_entry_usdt: Optional[float] = None
    created_at: int
    updated_at: int
    expires_at: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)

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
    setup_stage: str = "WATCHING"
    last_analyzed_at: int
    last_data_at: int
    missing_conditions: List[str] = []
    conditions_met: List[str] = []
    swings: List[Dict[str, Any]] = []
    structure_events: List[Dict[str, Any]] = []
    reason_code: Optional[str] = None
    reason_detail: Optional[str] = None

# ==================== V5.3 / V6 RISK SETTINGS ====================
class RiskSettingsUpdate(BaseModel):
    leverage: Optional[int] = None
    margin_mode: Optional[str] = None
    risk_pct: Optional[float] = None
    expected_config_version: Optional[int] = None

class RiskSettingsResponse(BaseModel):
    symbol: str = "XAUUSDT"
    product_type: str = "USDT-FUTURES"
    requested_leverage: int
    margin_mode: str
    risk_pct: float
    config_version: int
    updated_at: int
    metadata_version: int
    min_leverage: int = 1
    max_leverage: int = 100
    cross_margin_supported: bool = False
    source: str = "Bitget Classic Futures USDT-M"
    status: str = "SUCCESS"

# Day Audit & Risk
class DayAuditResponse(BaseModel):
    date_str: str
    initial_equity: float
    current_equity: float
    realized_pnl_today: float
    fills_count: int
    today_fills_count: int = 0
    active_positions_count: int = 0
    armed_orders_count: int = 0
    max_daily_fills: int = 3
    consecutive_losses: int
    max_consecutive_losses: int = 2
    daily_loss_limit_usdt: float
    cooldown_remaining_sec: int = 0
    is_blocked: bool
    block_reason: Optional[str] = None
    auto_paper_active: bool = False
    leverage: int = 5
    margin_mode: str = "ISOLATED"
    risk_pct: float = 0.25
    config_version: int = 1
    metadata_version: int = 1
    min_leverage: int = 1
    max_leverage: int = 100
    cross_margin_supported: bool = False

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
    source_url: Optional[str] = None
    event_type_id: Optional[str] = None
    source_timezone: Optional[str] = "America/New_York"
    source_time_raw: Optional[str] = None
    schedule_kind: Optional[str] = "EXACT"
    gold_relevance: Optional[str] = "LOW"
    research_status: Optional[str] = "NOT_FETCHED"
    research_assessment: Optional[str] = None
    research_fetched_at: Optional[int] = None

class NewsImportRowPreview(BaseModel):
    row_index: int
    title: str
    country: str
    impact: str
    source_time_str: str
    scheduled_at_utc_ms: int
    time_vn_str: str
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None
    url: Optional[str] = None
    gold_relevance: str = "LOW"
    is_valid: bool = True
    error_message: Optional[str] = None

class NewsImportPreviewRequest(BaseModel):
    csv_content: str
    source_timezone: str = "America/New_York"

class NewsImportPreviewResponse(BaseModel):
    total_rows: int
    valid_count: int
    invalid_count: int
    source_timezone: str
    preview_rows: List[NewsImportRowPreview]
    errors: List[str] = []

class NewsImportCommitRequest(BaseModel):
    csv_content: str
    source_timezone: str = "America/New_York"

class NewsImportCommitResponse(BaseModel):
    status: str
    imported_count: int
    skipped_duplicates_count: int
    error_count: int
    message: str

class NewsResearchResponse(BaseModel):
    news_id: int
    title: str
    country: str
    impact: str
    scheduled_at: int
    source_url: Optional[str] = None
    gold_relevance: str
    research_status: str
    meaning_vn: str
    transmission_channels: Dict[str, str] = {}
    pre_release_scenarios: List[str] = []
    post_release_assessment: Optional[str] = None
    citations: List[str] = []
    historical_releases: List[Dict[str, Any]] = []
    limitations: str
    fetched_at: Optional[int] = None

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
    last_success_time: Optional[int] = None
    last_error: Optional[str] = None
    degraded_reason: Optional[str] = None
    d_4h_bias: Optional[str] = None
    h1_alignment: Optional[str] = None

# Telegram Settings Schemas
class TelegramConfigSchema(BaseModel):
    enabled: bool
    bot_token_masked: str
    chat_id: str
    subscribed_events: List[str]
    quiet_hours_enabled: bool
    quiet_hours_start: str
    quiet_hours_end: str
    bypass_critical_quiet_hours: bool = True
    near_entry_mode: str = "ATR"
    near_entry_atr_mult: float = 0.5
    near_entry_price_dist: float = 2.0
    near_entry_cooldown_min: int = 30
    timezone: str
    base_chart_url: Optional[str] = None

class TelegramConfigUpdate(BaseModel):
    enabled: bool
    bot_token: Optional[str] = None  # None/empty means keep current secret
    chat_id: str
    subscribed_events: List[str]
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "06:00"
    bypass_critical_quiet_hours: bool = True
    near_entry_mode: str = "ATR"
    near_entry_atr_mult: float = 0.5
    near_entry_price_dist: float = 2.0
    near_entry_cooldown_min: int = 30
    timezone: str = "Asia/Ho_Chi_Minh"
    base_chart_url: Optional[str] = None

class TelegramTestRequest(BaseModel):
    bot_token: Optional[str] = None
    chat_id: str

class NotificationOutboxItem(BaseModel):
    id: int
    event_id: Optional[str] = None
    channel: str = "TELEGRAM"
    recipient: Optional[str] = None
    message_type: str
    dedupe_key: str
    status: str
    priority: str = "STANDARD"
    attempts: int = 0
    last_attempt_at: Optional[int] = None
    next_attempt_at: Optional[int] = None
    occurred_at: Optional[int] = None
    error_message: Optional[str] = None
    created_at: int

# Multi-Timeframe Matrix
class MarketMatrixItem(BaseModel):
    timeframe: str
    trend: str
    zone: str
    current_price: float
    atr: float
    last_candle_time: int
    is_stale: bool
    freshness_sec: float

class MarketMatrixResponse(BaseModel):
    symbol: str
    d_4h_bias: str
    h1_alignment: str
    server_time: int
    matrix: List[MarketMatrixItem]

# RVOL Response
class RvolResponse(BaseModel):
    symbol: str
    timeframe: str
    rvol: Optional[float]
    status: str
    volume: float
    baseline_volume: float
    classification: str
    samples_used: int
    unit: str

# ==================== LAB & TESTING SCHEMAS ====================

class ScenarioStepResult(BaseModel):
    step_index: int
    name: str
    status: str  # PASS / FAIL / SKIPPED
    expected: str
    actual: str
    detail: Optional[str] = None
    timestamp: int
    payload: Optional[Dict[str, Any]] = None

class ScenarioRunResponse(BaseModel):
    scenario_id: str
    name: str
    description: str
    status: str  # PASS / FAIL
    steps: List[ScenarioStepResult]
    started_at: int
    completed_at: int
    duration_ms: int
    error: Optional[str] = None
    config_version: Optional[int] = 1
    metadata_version: Optional[int] = 1

class ReplayTradeItem(BaseModel):
    id: str
    setup_id: Optional[str] = None
    direction: str  # LONG / SHORT
    order_type: str
    entry_time: int
    entry_price: float
    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    exit_cause: Optional[str] = None
    stop_loss: float
    take_profit: float
    quantity: float
    initial_risk_usdt: float
    gross_pnl: float
    fees: float
    slippage: float
    net_pnl: float
    realized_r: float
    session: str  # TOKYO / LONDON / NEW_YORK / OVERLAP
    is_ambiguous: bool = False
    status: str  # CLOSED / OPEN

class EquityPoint(BaseModel):
    timestamp: int
    equity: float
    drawdown_usdt: float
    drawdown_pct: float
    daily_date: str

class ReplayRunRequest(BaseModel):
    run_name: str = "Backtest XAUUSDT"
    symbol: str = "XAUUSDT"
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    timeframe: str = "15M"
    initial_equity: float = 1000.0
    risk_pct: float = 0.25
    leverage: int = 5
    margin_mode: str = "ISOLATED"
    spread_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    fee_rate: float = 0.0004
    speed_ms: int = 10
    seed: int = 42
    custom_candles_json: Optional[str] = None

class ReplayRunResponse(BaseModel):
    id: str
    run_name: str
    symbol: str
    start_ts: int
    end_ts: int
    initial_equity: float
    final_equity: float
    total_trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate_pct: float
    profit_factor: float
    max_drawdown_usdt: float
    max_drawdown_pct: float
    expectancy_r: float
    total_net_pnl: float
    total_fees: float
    total_slippage: float
    worst_day_pnl: float
    max_consecutive_losses: int
    loss_budget_breaches: int
    signals_count: int
    rejected_count: int
    trades: List[ReplayTradeItem]
    equity_curve: List[EquityPoint]
    session_breakdown: Dict[str, Any]
    rejection_reasons: Dict[str, int]
    warnings: List[str]
    created_at: int
    model_config = ConfigDict(from_attributes=True)

class StressTestRequest(BaseModel):
    run_name: str = "Stress Test Matrix"
    symbol: str = "XAUUSDT"
    spread_multipliers: List[float] = [1.0, 2.0, 3.0]
    slippage_multipliers: List[float] = [1.0, 2.0, 3.0]
    fee_multipliers: List[float] = [1.0, 2.0]
    latency_ms_list: List[int] = [0, 500, 2000]

class StressTestResultRow(BaseModel):
    spread_mult: float
    slippage_mult: float
    fee_mult: float
    latency_ms: int
    trades_count: int
    net_pnl: float
    win_rate_pct: float
    profit_factor: float
    max_drawdown_pct: float
    expectancy_r: float

class StressTestResponse(BaseModel):
    id: str
    run_name: str
    symbol: str
    baseline: StressTestResultRow
    stress_matrix: List[StressTestResultRow]
    created_at: int

