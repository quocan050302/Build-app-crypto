import math
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import List, Optional, Dict, Any, Union, Literal

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
    quantity: float = 0.1
    initial_risk_usdt: float = 10.0
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
    strategy_family: str = "STANDARD_SMC"
    origin: str = "UNKNOWN"
    execution_mode: str = "AUTO"
    arm_decision_snapshot: Optional[str] = None
    fill_decision_snapshot: Optional[str] = None

class ArmSetupRequest(BaseModel):
    setup_id: str
    setup_instance_id: Optional[str] = None
    expected_revision: Optional[int] = None
    expected_direction: str  # "LONG" or "SHORT"
    idempotency_key: Optional[str] = None
    order_type: Optional[str] = None  # None/MARKET/LIMIT/STOP
    planned_entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

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
    last_processed_market_timestamp: Optional[int] = None
    recovery_status: Optional[str] = None
    recovery_run_id: Optional[str] = None
    last_recovery_attempt: Optional[int] = None
    resolved_through: Optional[str] = None
    recovery_confidence: Optional[str] = None
    discovered_at: Optional[int] = None
    occurred_at: Optional[int] = None
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
class ReportGeneratePayload(BaseModel):
    report_type: Optional[str] = "SESSION_REPORT"


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
    status: Optional[str] = "PENDING_REVIEW"
    reviewed_at: Optional[int] = None
    hypothesis: Optional[str] = None
    author: Optional[str] = "SYSTEM"
    audit_trail: Optional[str] = None
    strategy_family: Optional[str] = "STANDARD_SMC"
    facts_snapshot: Optional[str] = None
    compliance_snapshot: Optional[str] = None
    # V10 Fields
    severity: Optional[str] = "INFO"
    effect: Optional[str] = "ANNOTATE"
    enabled: Optional[bool] = True
    validation_status: Optional[str] = "UNVALIDATED"
    validated_at: Optional[int] = None
    validation_report: Optional[str] = None
    scope: Optional[str] = None
    predicate: Optional[str] = None
    stage: Optional[str] = "BEFORE_ARM"
    version: Optional[int] = 1
    revision: Optional[int] = 1
    effective_at: Optional[int] = None
    expiry_at: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)

class LessonUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    reflection: Optional[str] = None
    action_rule: Optional[str] = None
    hypothesis: Optional[str] = None
    status: Optional[str] = None
    severity: Optional[str] = None
    effect: Optional[str] = None
    enabled: Optional[bool] = None
    predicate: Optional[str] = None
    scope: Optional[str] = None
    stage: Optional[str] = None
    revision: Optional[int] = None

class RuleValidationRequest(BaseModel):
    predicate: Optional[Union[Dict[str, Any], str]] = None
    severity: str = "INFO"
    effect: str = "ANNOTATE"
    scope: Optional[Union[Dict[str, Any], str]] = None

class RuleValidationResponse(BaseModel):
    is_valid: bool
    status: str
    message: str
    report: Dict[str, Any]

# Trade Review & Psychology Schemas
class TradeReviewSchema(BaseModel):
    id: str
    trade_id: str
    execution_mode: str = "AUTO"
    user_notes: Optional[str] = None
    self_reported_entry_reason: Optional[str] = None
    psychology_before: Optional[str] = None
    psychology_during: Optional[str] = None
    psychology_after: Optional[str] = None
    emotions: List[str] = []
    confidence_score: Optional[int] = None
    discipline_score: Optional[int] = None
    user_loss_reason: Optional[str] = None
    mistakes: Optional[str] = None
    what_went_well: Optional[str] = None
    improvement_plan: Optional[str] = None
    revision: int = 1
    created_at: int
    updated_at: int
    reviewed_at: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)

class TradeReviewCreateOrUpdate(BaseModel):
    execution_mode: Optional[str] = "AUTO"
    user_notes: Optional[str] = None
    self_reported_entry_reason: Optional[str] = None
    psychology_before: Optional[str] = None
    psychology_during: Optional[str] = None
    psychology_after: Optional[str] = None
    emotions: Optional[List[str]] = []
    confidence_score: Optional[int] = None
    discipline_score: Optional[int] = None
    user_loss_reason: Optional[str] = None
    mistakes: Optional[str] = None
    what_went_well: Optional[str] = None
    improvement_plan: Optional[str] = None
    revision: Optional[int] = None
    expected_revision: Optional[int] = None

# Journal Pagination & Summaries
class JournalSummary(BaseModel):
    completed_count: int
    open_count: int
    armed_count: int
    cancelled_count: int
    net_pnl: float
    win_count: int
    loss_count: int
    breakeven_count: int
    winrate_pct: float
    avg_realized_r: float
    # Backward compatibility / alias fields
    wins: Optional[int] = None
    losses: Optional[int] = None
    breakevens: Optional[int] = None
    average_realized_r: Optional[float] = None

class PaginatedJournalResponse(BaseModel):
    items: List[PaperOrderResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    summary: JournalSummary

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
    # V7.1 Telemetry
    transport_state: Optional[str] = "CONNECTED"
    feed_source: Optional[str] = "WS"
    quote_exchange_age_ms: Optional[float] = None
    quote_local_age_ms: Optional[float] = None
    execution_queue_depth: Optional[int] = 0
    execution_lag_ms: Optional[float] = 0.0
    analysis_as_of: Optional[int] = None
    reconnect_count: Optional[int] = 0

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
    has_token: bool = False
    token_configured: bool = False

class TelegramConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    bot_token: Optional[str] = None  # None/empty means keep current secret
    chat_id: Optional[str] = None
    subscribed_events: Optional[List[str]] = None
    quiet_hours_enabled: Optional[bool] = None
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    bypass_critical_quiet_hours: Optional[bool] = None
    near_entry_mode: Optional[str] = None
    near_entry_atr_mult: Optional[float] = None
    near_entry_price_dist: Optional[float] = None
    near_entry_cooldown_min: Optional[int] = None
    timezone: Optional[str] = None
    base_chart_url: Optional[str] = None
    clear_token: Optional[bool] = False

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
    payload: Optional[str] = None
    status: str
    priority: str = "STANDARD"
    attempts: int = 0
    last_attempt_at: Optional[int] = None
    next_attempt_at: Optional[int] = None
    occurred_at: Optional[int] = None
    provider_message_id: Optional[str] = None
    error_message: Optional[str] = None
    lease_expires_at: Optional[int] = None
    worker_id: Optional[str] = None
    created_at: int
    model_config = ConfigDict(from_attributes=True)

class PaginatedOutboxResponse(BaseModel):
    items: List[NotificationOutboxItem]
    total: int
    page: int
    page_size: int
    total_pages: int

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
    entry_session: Optional[str] = None
    exit_session: Optional[str] = None
    entry_fee: Optional[float] = 0.0
    exit_fee: Optional[float] = 0.0
    entry_slippage: Optional[float] = 0.0
    exit_slippage: Optional[float] = 0.0
    net_rr_planned: Optional[float] = None
    net_rr_fill: Optional[float] = None
    gross_rr: Optional[float] = None
    net_risk_usdt: Optional[float] = None
    net_reward_usdt: Optional[float] = None
    strategy_family: Optional[str] = "SMC_MOMENTUM"
    entry_type: Optional[str] = "QUALITY_ENTRY"  # QUALITY_ENTRY vs QUOTA_ENTRY
    ny_session_id: Optional[str] = None
    margin_usdt: Optional[float] = None
    tp_is_maker: Optional[bool] = False

class EquityPoint(BaseModel):
    timestamp: int
    equity: float
    drawdown_usdt: float
    drawdown_pct: float
    daily_date: str
    cash_balance: Optional[float] = None
    open_mtm: Optional[float] = None

StrategyVariantType = Literal["CURRENT_BASELINE", "NY_ADAPTIVE", "NY_DAILY_PAPER_RESEARCH"]

class ReplayRunRequest(BaseModel):
    run_name: str = "Backtest XAUUSDT"
    symbol: str = "XAUUSDT"
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    timeframe: str = "15M"
    initial_equity: float = 1000.0
    risk_pct: float = 0.25
    leverage: int = 30
    margin_mode: str = "ISOLATED"
    spread_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    spread_usd: Optional[float] = None
    slippage_usd: Optional[float] = None
    maker_fee_rate: Optional[float] = None
    fee_rate: float = 0.0004
    latency_ms: int = 0
    speed_ms: int = 10
    seed: int = 42
    custom_candles_json: Optional[str] = None
    mode: Literal["HISTORICAL_MARKET", "SYNTHETIC_QA", "CUSTOM_DATASET", "RECORDED_TICK"] = "HISTORICAL_MARKET"
    warmup_days: int = 50
    export_artifacts: bool = True
    resize_policy: str = "PRESERVE_OR_DOWNSIZE"
    strategy_variant: StrategyVariantType = "CURRENT_BASELINE"
    ny_quota_target: int = 1
    ny_deadline_hour: int = 14
    ny_deadline_minute: int = 30
    quota_risk_pct: Optional[float] = None
    quality_risk_pct: Optional[float] = None

    @model_validator(mode="after")
    def validate_and_normalize(self) -> "ReplayRunRequest":
        if not math.isfinite(self.risk_pct) or self.risk_pct <= 0 or self.risk_pct > 100.0:
            raise ValueError(f"Tỷ lệ rủi ro (risk_pct) phải là số dương hữu hạn <= 100, nhận: {self.risk_pct}")
        if self.quality_risk_pct is None:
            self.quality_risk_pct = self.risk_pct
        elif not math.isfinite(self.quality_risk_pct) or self.quality_risk_pct <= 0 or self.quality_risk_pct > 100.0:
            raise ValueError(f"Tỷ lệ rủi ro quality_risk_pct phải là số dương hữu hạn <= 100, nhận: {self.quality_risk_pct}")
        
        if self.quota_risk_pct is None:
            self.quota_risk_pct = 0.10
        elif not math.isfinite(self.quota_risk_pct) or self.quota_risk_pct <= 0 or self.quota_risk_pct > 100.0:
            raise ValueError(f"Tỷ lệ rủi ro quota_risk_pct phải là số dương hữu hạn <= 100, nhận: {self.quota_risk_pct}")

        if not math.isfinite(self.fee_rate) or self.fee_rate < 0 or self.fee_rate >= 1.0:
            raise ValueError(f"Tỷ lệ phí (fee_rate) phải nằm trong khoảng [0, 1), nhận: {self.fee_rate}")

        if self.leverage < 1 or self.leverage > 125:
            raise ValueError(f"Đòn bẩy (leverage) phải nằm trong khoảng [1, 125], nhận: {self.leverage}")

        if self.latency_ms < 0:
            raise ValueError(f"Độ trễ (latency_ms) không được âm, nhận: {self.latency_ms}")

        if self.ny_quota_target < 1:
            raise ValueError(f"Mục tiêu NY quota target phải >= 1, nhận: {self.ny_quota_target}")

        if not (0 <= self.ny_deadline_hour <= 23):
            raise ValueError(f"Giờ deadline NY (ny_deadline_hour) phải từ 0 đến 23, nhận: {self.ny_deadline_hour}")

        if not (0 <= self.ny_deadline_minute <= 59):
            raise ValueError(f"Phút deadline NY (ny_deadline_minute) phải từ 0 đến 59, nhận: {self.ny_deadline_minute}")

        if self.start_ts is not None and self.end_ts is not None and self.start_ts >= self.end_ts:
            raise ValueError(f"Thời gian bắt đầu (start_ts={self.start_ts}) phải nhỏ hơn thời gian kết thúc (end_ts={self.end_ts})")

        return self

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
    profit_factor: Optional[float] = None
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
    cash_balance: float = 1000.0
    open_mtm: float = 0.0
    dataset_hash: Optional[str] = None
    artifacts_dir: Optional[str] = None
    dataset_type: str = "HISTORICAL_MARKET"
    execution_fidelity: str = "ESTIMATED_EXECUTION"
    strategy_variant: str = "CURRENT_BASELINE"
    ny_quota_stats: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None
    funnel_stats: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None
    quality_trades_count: int = 0
    quota_trades_count: int = 0
    quality_net_pnl: float = 0.0
    quota_net_pnl: float = 0.0
    ny_fill_coverage_pct: float = 0.0
    effective_config: Optional[Dict[str, Any]] = None
    config_hash: Optional[str] = None
    timeframe_metadata: Optional[Dict[str, Any]] = None
    ledger_postings: Optional[List[Dict[str, Any]]] = None
    decision_events: Optional[List[Dict[str, Any]]] = None
    execution_events: Optional[List[Dict[str, Any]]] = None
    model_config = ConfigDict(from_attributes=True)

# Lab Job API Types
JobStatus = Literal[
    "QUEUED", "RUNNING", "VALIDATING", "EXPORTING",
    "SUCCEEDED", "INCOMPLETE", "FAILED", "CANCELLING", "CANCEL_REQUESTED", "CANCELLED"
]

class JobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus = "QUEUED"
    message: str = "Tác vụ chạy Replay đã được đưa vào hàng đợi xử lý"
    created_at: int
    config_hash: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress_pct: float = 0.0
    current_phase: str = "INITIALIZING"
    completed_events: int = 0
    total_events: int = 0
    effective_config: Dict[str, Any] = Field(default_factory=dict)
    quality_summary: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: int
    updated_at: int

class JobCancelResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str

class StressTestRequest(BaseModel):
    run_name: str = "Stress Test Matrix"
    symbol: str = "XAUUSDT"
    strategy_variant: Optional[StrategyVariantType] = "CURRENT_BASELINE"
    mode: Literal["HISTORICAL_MARKET", "SYNTHETIC_QA", "CUSTOM_DATASET", "RECORDED_TICK"] = "HISTORICAL_MARKET"
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    custom_dataset_dir: Optional[str] = None
    initial_equity: float = Field(default=1000.0, gt=0.0)
    risk_pct: float = Field(default=0.25, gt=0.0, le=5.0)
    quality_risk_pct: Optional[float] = Field(default=None, gt=0.0, le=5.0)
    quota_risk_pct: Optional[float] = Field(default=None, gt=0.0, le=5.0)
    base_fee_rate: float = Field(default=0.0006, ge=0.0, lt=0.05)
    base_maker_fee_rate: float = Field(default=0.0002, ge=0.0, lt=0.05)
    base_spread_usd: float = Field(default=0.35, ge=0.0)
    base_slippage_usd: float = Field(default=0.10, ge=0.0)
    leverage: int = Field(default=30, ge=1, le=125)
    warmup_days: int = Field(default=50, ge=1, le=180)
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
    profit_factor: Optional[float] = None
    max_drawdown_pct: float
    expectancy_r: float

class StressTestResponse(BaseModel):
    id: str
    run_name: str
    symbol: str
    baseline: StressTestResultRow
    stress_matrix: List[StressTestResultRow]
    created_at: int


# ========================================================
# V7 Trading Policy & NY Session Schemas
# ========================================================
class TradingPolicyUpdate(BaseModel):
    symbol: Optional[str] = "XAUUSDT"
    max_daily_fills: Optional[int] = 3
    daily_timezone: Optional[str] = "Asia/Ho_Chi_Minh"
    entry_session_policy: Optional[str] = "NY_ONLY"  # NY_ONLY, ALL_SESSIONS_WITH_NY_RESERVE
    ny_timezone: Optional[str] = "America/New_York"
    ny_entry_start: Optional[str] = "08:00"
    ny_entry_end: Optional[str] = "11:00"
    ny_min_fills: Optional[int] = 1
    reserve_ny_slot: Optional[bool] = True
    ny_fallback_enabled: Optional[bool] = True
    ny_fallback_start: Optional[str] = "10:30"
    ny_fallback_risk_pct_cap: Optional[float] = 0.10
    min_net_rr: Optional[float] = 2.0
    max_open_positions: Optional[int] = 1
    max_armed_orders: Optional[int] = 1

class TradingPolicyResponse(BaseModel):
    id: str = "default"
    symbol: str = "XAUUSDT"
    version: int = 1
    mode: str = "PAPER"
    is_active: bool = True
    max_daily_fills: int = 3
    daily_timezone: str = "Asia/Ho_Chi_Minh"
    entry_session_policy: str = "NY_ONLY"
    ny_timezone: str = "America/New_York"
    ny_entry_start: str = "08:00"
    ny_entry_end: str = "11:00"
    ny_min_fills: int = 1
    reserve_ny_slot: bool = True
    ny_fallback_enabled: bool = True
    ny_fallback_start: str = "10:30"
    ny_fallback_risk_pct_cap: float = 0.10
    min_net_rr: float = 2.0
    max_open_positions: int = 1
    max_armed_orders: int = 1
    created_at: Optional[int] = None
    updated_at: int
    model_config = ConfigDict(from_attributes=True)

class NYSessionStatusResponse(BaseModel):
    session_instance_id: str
    symbol: str
    is_in_ny_window: bool
    is_fallback_active: bool
    quota_state: str
    daily_fills: int
    max_daily_fills: int
    remaining_daily_slots: int
    ny_fills: int
    ny_min_fills: int
    reserved_slots: int
    local_ny_time: str
    local_vn_time: str
    ny_window_display: str
    vn_window_display: str
    minutes_to_window_start: Optional[int] = None
    minutes_to_fallback: Optional[int] = None
    minutes_to_window_end: Optional[int] = None
    allowed: bool
    reason_code: Optional[str] = None
    reason_message: Optional[str] = None

# V10.1 Governed Policy Config & Desired Enable State Schemas
class LessonPolicyConfig(BaseModel):
    lesson_advisory_enabled: bool = True
    lesson_entry_rules_enabled: bool = True
    lesson_shadow_mode: bool = False
    plan_adjustment_enabled: bool = False
    version: int = 1
    updated_at: int = 0

class LessonPolicyUpdate(BaseModel):
    lesson_advisory_enabled: Optional[bool] = None
    lesson_entry_rules_enabled: Optional[bool] = None
    lesson_shadow_mode: Optional[bool] = None
    plan_adjustment_enabled: Optional[bool] = None
    expected_version: Optional[int] = None

class LessonEnableRequest(BaseModel):
    enabled: bool
    expected_revision: Optional[int] = None

class LessonDecisionItem(BaseModel):
    rule_id: Union[int, str]
    rule_version: int = 1
    lesson_id: Optional[Union[int, str]] = None
    severity: str = "INFO"  # "INFO" | "WARN" | "CRITICAL"
    effect: str = "ANNOTATE"  # "ANNOTATE" | "WARN_ENTRY" | "BLOCK_ENTRY" | "PROPOSE_PLAN_ADJUSTMENT"
    title: str = ""
    message: str = ""
    next_step: Optional[str] = None
    matched: bool = False
    evaluated: bool = False
    data_unavailable: bool = False
    would_block: bool = False
    effective_block: bool = False
    reason_code: Optional[str] = None
    evaluated_at: int = 0
    metric_value: Optional[Any] = None
    scope: Optional[Dict[str, Any]] = None
    symbol: Optional[str] = None
    direction: Optional[str] = None
    timeframe: Optional[str] = None
    setup_instance_id: Optional[str] = None
    revision: Optional[int] = None



