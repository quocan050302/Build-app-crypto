from sqlalchemy import Column, Integer, String, Float, BigInteger, Boolean, Text, UniqueConstraint, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, synonym

Base = declarative_base()

class Candle(Base):
    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), index=True, nullable=False)
    timeframe = Column(String(10), index=True, nullable=False)
    timestamp = Column(BigInteger, index=True, nullable=False)  # epoch ms
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, default=0.0)
    is_closed = Column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint('symbol', 'timeframe', 'timestamp', name='uq_candle_symbol_tf_ts'),
    )


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id = Column(String(36), primary_key=True, index=True)  # UUID
    setup_id = Column(String(50), index=True)
    signal_id = Column(String(50), index=True)
    instrument = Column(String(20), default="XAUUSDT")
    direction = Column(String(10), nullable=False)  # LONG / SHORT
    state = Column(String(20), default="candidate", index=True)  # draft, candidate, armed, paper_open, closed, expired, invalidated, cancelled
    order_type = Column(String(10), default="MARKET")  # MARKET / LIMIT
    timeframe = Column(String(10), default="15M")

    # Price levels
    planned_entry = Column(Float, nullable=False)
    actual_entry = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    actual_exit = Column(Float, nullable=True)

    # Risk & Sizing
    quantity = Column(Float, nullable=False)  # oz or contract unit
    initial_risk_usdt = Column(Float, nullable=False)
    risk_pct = Column(Float, default=0.25)
    gross_rr = Column(Float, default=2.0)
    estimated_net_rr = Column(Float, default=1.9)
    fees_assumption = Column(Float, default=0.10)
    slippage_assumption = Column(Float, default=0.10)

    # Leverage & Margin (Bitget USDT-M Classic Futures model)
    leverage = Column(Integer, default=5)
    margin_mode = Column(String(20), default="ISOLATED")
    estimated_liquidation = Column(Float, nullable=True)
    initial_margin = Column(Float, nullable=True)
    config_version = Column(Integer, default=1)

    # Execution & PnL
    realized_pnl_net = Column(Float, nullable=True)
    realized_r = Column(Float, nullable=True)
    exit_cause = Column(String(30), nullable=True)  # TP_HIT, SL_HIT, MANUAL_CLOSE, EXPIRED, INVALIDATED, LIQUIDATED, AMBIGUOUS_BAR_SL_FIRST
    invalidation_reason = Column(Text, nullable=True)
    strategy_version = Column(String(20), default="1.0.0")

    # Timestamps (epoch ms)
    created_at = Column(BigInteger, nullable=False)
    armed_at = Column(BigInteger, nullable=True)
    opened_at = Column(BigInteger, nullable=True)
    closed_at = Column(BigInteger, nullable=True)
    expires_at = Column(BigInteger, nullable=True)

    checklist_snapshot = Column(Text, nullable=True)  # JSON
    lessons_retrieved = Column(Text, nullable=True)   # JSON

    # V5 Idempotency and Instance tracking
    idempotency_key = Column(String(100), nullable=True, index=True)
    setup_instance_id = Column(String(100), nullable=True, index=True)

    # V6.1 Offline Position Recovery & Checkpoint Fields
    last_processed_market_timestamp = Column(BigInteger, nullable=True)
    recovery_status = Column(String(30), default="NONE")  # NONE, RECOVERING, RECOVERED, UP_TO_DATE, UNRESOLVED_GAP
    recovery_run_id = Column(String(36), nullable=True)
    last_recovery_attempt = Column(BigInteger, nullable=True)
    resolved_through = Column(BigInteger, nullable=True)
    recovery_confidence = Column(String(30), nullable=True)  # CONFIRMED, ASSUMED_CONSERVATIVE, UNRESOLVED
    discovered_at = Column(BigInteger, nullable=True)
    occurred_at = Column(BigInteger, nullable=True)

    # V7 Trading Policy & Quota Fields
    strategy_family = Column(String(30), default="STANDARD_SMC")  # STANDARD_SMC, NY_QUOTA_PAPER, MANUAL
    session_instance_id = Column(String(60), nullable=True, index=True)
    scenario_snapshot_id = Column(String(80), nullable=True, index=True)
    policy_config_version = Column(Integer, default=1)
    evidence_snapshot_id = Column(String(60), nullable=True)
    requested_risk_pct = Column(Float, default=0.25)
    effective_risk_pct = Column(Float, default=0.25)
    risk_profile = Column(String(30), default="STANDARD")  # STANDARD, QUOTA_FALLBACK
    cost_snapshot = Column(Text, nullable=True)  # JSON

    # V10.1 Unified Entry Decision & Stage Snapshots
    origin = Column(String(30), default="UNKNOWN")  # MANUAL_WEB, AUTO_STRATEGY, NY_FALLBACK, UNKNOWN
    execution_mode = Column(String(20), default="AUTO")  # MANUAL, AUTO
    arm_decision_snapshot = Column(Text, nullable=True)  # JSON
    fill_decision_snapshot = Column(Text, nullable=True)  # JSON
    quality_snapshot_id = Column(String(80), nullable=True, index=True)
    # V11 Lab/Replay transient sizing policy override (not mapped to DB column to preserve zero-migration DB integrity)
    resize_policy = None
    # V16.6 Intrabar execution ambiguity flag
    intrabar_ambiguous = Column(Boolean, default=False)

    @property
    def symbol(self):
        return self.instrument

    @symbol.setter
    def symbol(self, val):
        self.instrument = val

    @property
    def filled_at(self):
        return self.opened_at

    @property
    def snapshot(self):
        from services.order_plan_snapshot_service import parse_order_plan_snapshot
        return parse_order_plan_snapshot(self.cost_snapshot)

    @property
    def candidate_score_summary(self):
        from services.order_quality_display_service import build_order_score_display
        display = build_order_score_display(self)
        return display.to_dict() if hasattr(display, "to_dict") else display



class DayAudit(Base):
    __tablename__ = "day_audits"

    id = Column(Integer, primary_key=True, index=True)
    date_str = Column(String(10), unique=True, index=True)  # "YYYY-MM-DD" in UTC+7
    initial_equity = Column(Float, default=1000.0)
    current_equity = Column(Float, default=1000.0)
    realized_pnl_today = Column(Float, default=0.0)
    fills_count = Column(Integer, default=0)  # Max 3
    consecutive_losses = Column(Integer, default=0)  # Max 2
    cooldown_until = Column(BigInteger, nullable=True)  # epoch ms
    updated_at_ms = Column(BigInteger, nullable=True)  # epoch ms when equity/audit was recorded
    is_blocked = Column(Boolean, default=False)
    block_reason = Column(String(255), nullable=True)


class EconomicNews(Base):
    __tablename__ = "economic_news"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(String(100), unique=True, nullable=True)
    title = Column(String(255), nullable=False)
    country = Column(String(10), default="USD")
    currency = Column(String(10), default="USD")
    impact = Column(String(20), default="Low")  # High, Medium, Low, Holiday
    scheduled_at = Column(BigInteger, index=True, nullable=False)  # epoch ms UTC
    received_at = Column(BigInteger, nullable=False)
    forecast = Column(String(50), nullable=True)
    previous = Column(String(50), nullable=True)
    actual = Column(String(50), nullable=True)
    revised = Column(String(50), nullable=True)
    raw_json = Column(Text, nullable=True)

    # V5.1 URL & Research Enrichment Fields
    source_url = Column(String(500), nullable=True)
    event_type_id = Column(String(100), nullable=True, index=True)
    source_timezone = Column(String(50), default="America/New_York")
    source_time_raw = Column(String(100), nullable=True)
    schedule_kind = Column(String(20), default="EXACT")  # EXACT, ALL_DAY, TENTATIVE, TBA
    gold_relevance = Column(String(20), default="LOW")  # HIGH, MEDIUM, LOW
    research_status = Column(String(30), default="NOT_FETCHED")  # NOT_FETCHED, QUEUED, FETCHING, SUCCEEDED, PARTIAL, BLOCKED, FAILED
    research_assessment = Column(Text, nullable=True)  # JSON formatted research assessment
    research_fetched_at = Column(BigInteger, nullable=True)


class NewsReaction(Base):
    __tablename__ = "news_reactions"

    id = Column(Integer, primary_key=True, index=True)
    news_id = Column(Integer, ForeignKey("economic_news.id"), nullable=False)
    reaction_1m = Column(Float, nullable=True)
    reaction_5m = Column(Float, nullable=True)
    reaction_15m = Column(Float, nullable=True)
    range_usdt = Column(Float, nullable=True)


class ResearchReport(Base):
    __tablename__ = "research_reports"

    id = Column(Integer, primary_key=True, index=True)
    report_type = Column(String(30), nullable=False)  # PREMARKET, TOKYO, LONDON, NEW_YORK, DAY_SUMMARY
    created_at = Column(BigInteger, index=True, nullable=False)
    session_name = Column(String(50), nullable=False)
    d_4h_bias = Column(String(50), nullable=False)
    h1_alignment = Column(String(50), nullable=False)
    m15_pois = Column(Text, nullable=True)  # JSON
    liquidity_levels = Column(Text, nullable=True)  # JSON
    scenarios = Column(Text, nullable=True)  # JSON
    content_markdown = Column(Text, nullable=False)
    strategy_version = Column(String(20), default="1.0.0")

    # V13 Causal Daily Research Additive Fields
    research_date = Column(String(10), nullable=True, index=True)
    date_basis = Column(String(20), nullable=True, default="VN_DATE")
    mode = Column(String(30), nullable=True, default="CURRENT_ASOF")
    as_of_ms = Column(BigInteger, nullable=True, index=True)
    market_regime = Column(String(50), nullable=True)
    timeframe_matrix = Column(Text, nullable=True)
    structured_scenarios = Column(Text, nullable=True)
    data_coverage_status = Column(String(50), nullable=True)
    quality_score = Column(Float, nullable=True)
    provenance_metadata = Column(Text, nullable=True)
    review_reference = Column(Text, nullable=True)


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(BigInteger, nullable=False)
    title = Column(String(255), nullable=False)
    category = Column(String(50), default="PROCESS")  # PROCESS, RULE_COMPLIANCE, RISK, NEWS, EXECUTION
    related_trade_id = Column(String(36), nullable=True)
    setup_type = Column(String(50), nullable=True)
    session = Column(String(50), nullable=True)
    reflection = Column(Text, nullable=False)
    action_rule = Column(Text, nullable=False)
    is_hard_filter = Column(Boolean, default=False)
    is_approved = Column(Boolean, default=True)

    # V7 Evidence-based Lesson Fields
    strategy_family = Column(String(30), default="STANDARD_SMC")
    facts_snapshot = Column(Text, nullable=True)       # JSON facts (entry/exit/pnl/cause/confidence)
    compliance_snapshot = Column(Text, nullable=True)  # JSON evaluated rules compliance
    mfe_mae_snapshot = Column(Text, nullable=True)     # JSON MFE/MAE/hold time
    action_candidate = Column(Text, nullable=True)     # JSON structured proposed rule candidate
    # V8 Review & Governance Fields
    status = Column(String(30), default="PENDING_REVIEW")  # PENDING_REVIEW, APPROVED, REJECTED, ARCHIVED
    reviewed_at = Column(BigInteger, nullable=True)
    hypothesis = Column(Text, nullable=True)
    author = Column(String(50), default="SYSTEM")
    audit_trail = Column(Text, nullable=True)

    # V10 Governed Rule & Three-Color Classification Fields
    severity = Column(String(20), default="INFO")  # INFO (Xanh - Tham khảo), WARNING (Vàng - Cảnh báo), CRITICAL (Đỏ - Hạn chế entry)
    effect = Column(String(30), default="ANNOTATE")  # ANNOTATE, WARN_ENTRY, BLOCK_ENTRY, PROPOSE_PLAN_ADJUSTMENT
    enabled = Column(Boolean, default=True)  # Independent operational toggle
    validation_status = Column(String(20), default="UNVALIDATED")  # VALID, INVALID, UNVALIDATED
    validated_at = Column(BigInteger, nullable=True)
    validation_report = Column(Text, nullable=True)  # JSON validation result & evidence report
    scope = Column(Text, nullable=True)  # JSON: { "symbol": "XAUUSDT", "strategy_family": "ALL", "direction": "ALL", "execution_mode": "ALL", "session": "ALL" }
    predicate = Column(Text, nullable=True)  # JSON: typed deterministic condition { "metric": ..., "operator": ..., "threshold": ... }
    stage = Column(String(30), default="BEFORE_ARM")  # BEFORE_ARM, BEFORE_FILL, POSITION_REVIEW
    version = Column(Integer, default=1)  # Rule version
    revision = Column(Integer, default=1)  # Optimistic concurrency locking
    effective_at = Column(BigInteger, nullable=True)  # Activation epoch ms
    expiry_at = Column(BigInteger, nullable=True)  # Optional expiry epoch ms


class TradeReview(Base):
    __tablename__ = "trade_reviews"

    id = Column(String(36), primary_key=True)
    trade_id = Column(String(36), ForeignKey("paper_orders.id"), unique=True, index=True, nullable=False)
    execution_mode = Column(String(20), default="AUTO")  # AUTO, MANUAL
    user_notes = Column(Text, nullable=True)
    self_reported_entry_reason = Column(Text, nullable=True)
    psychology_before = Column(String(100), nullable=True)
    psychology_during = Column(String(100), nullable=True)
    psychology_after = Column(String(100), nullable=True)
    emotions = Column(Text, nullable=True)  # JSON list of string tags
    confidence_score = Column(Integer, nullable=True)  # 1-10
    discipline_score = Column(Integer, nullable=True)  # 1-10
    user_loss_reason = Column(Text, nullable=True)
    mistakes = Column(Text, nullable=True)
    what_went_well = Column(Text, nullable=True)
    improvement_plan = Column(Text, nullable=True)
    revision = Column(Integer, default=1)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    reviewed_at = Column(BigInteger, nullable=True)


class PostTradeReviewRecord(Base):
    """
    V16.39 Section 078 - 080: Canonical persisted post-trade review artifact.
    Kept separate from user notes in TradeReview to ensure system automation
    never overwrites trader psychology or manual reflections.
    """
    __tablename__ = "post_trade_reviews"

    id = Column(String(36), primary_key=True)
    order_id = Column(String(36), ForeignKey("paper_orders.id"), unique=True, index=True, nullable=False)
    symbol = Column(String(20), nullable=False)
    model_id = Column(String(50), nullable=False)
    direction = Column(String(10), nullable=False)
    planned_entry = Column(Float, nullable=False)
    actual_entry = Column(Float, nullable=False)
    planned_sl = Column(Float, nullable=False)
    planned_tp = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    exit_cause = Column(String(50), nullable=False)
    gross_pnl = Column(Float, nullable=False)
    net_pnl = Column(Float, nullable=False)
    realized_rr = Column(Float, nullable=False)
    outcome_status = Column(String(20), nullable=False)
    hold_time_ms = Column(BigInteger, nullable=True)
    entry_slippage_usd = Column(Float, nullable=False)
    loss_classification = Column(String(50), nullable=False)
    review_json = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class LessonRepairTask(Base):
    """
    V16.39.2 Section 039 - 042: Durable repair record for missing/failed post-trade lessons.
    Tracks orders whose post-trade lesson creation failed or is pending repair.
    Idempotent, retry count, status: PENDING, COMPLETED, FAILED.
    """
    __tablename__ = "lesson_repair_tasks"

    id = Column(String(36), primary_key=True)
    order_id = Column(String(36), ForeignKey("paper_orders.id"), unique=True, index=True, nullable=False)
    artifact_type = Column(String(30), default="LESSON")
    status = Column(String(20), default="PENDING", index=True)  # PENDING, COMPLETED, FAILED
    retry_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class SystemConfig(Base):
    __tablename__ = "system_configs"

    key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class WatchSetup(Base):
    __tablename__ = "watch_setups"

    id = Column(String(50), primary_key=True)
    version = Column(Integer, default=1)
    strategy = Column(String(50), default="SMC_V1")
    direction = Column(String(10), nullable=False)  # LONG / SHORT
    timeframe = Column(String(10), default="15M")
    state = Column(String(30), default="WATCHING", index=True)
    # States: WATCHING, WAITING_PRICE, WAITING_SWEEP, WAITING_MSS, WAITING_RETRACE, READY, ARMED, TRIGGERED, PAPER_OPEN, CLOSED, INVALIDATED, EXPIRED, CANCELLED, REJECTED
    htf_bias = Column(String(20), default="UNKNOWN")
    h1_alignment = Column(String(20), default="UNKNOWN")
    poi_zone = Column(Text, nullable=True)  # JSON {top, bottom, type, timeframe}
    trigger_mode = Column(String(30), default="CONFIRMED_CLOSE")  # CONFIRMED_CLOSE / WICK_TOUCH

    # Price levels
    provisional_entry = Column(Float, nullable=False)
    provisional_sl = Column(Float, nullable=False)
    provisional_tp = Column(Float, nullable=False)
    confirmed_entry = Column(Float, nullable=True)
    confirmed_sl = Column(Float, nullable=True)
    confirmed_tp = Column(Float, nullable=True)
    invalidation_price = Column(Float, nullable=False)
    invalidation_reason = Column(Text, nullable=True)

    # Risk & Sizing metrics
    gross_rr = Column(Float, default=0.0)
    net_rr = Column(Float, default=0.0)
    risk_usdt = Column(Float, default=0.0)
    quantity = Column(Float, default=0.0)
    leverage = Column(Integer, default=5)
    margin_mode = Column(String(20), default="ISOLATED")
    risk_pct = Column(Float, default=0.25)
    config_version = Column(Integer, default=1)
    estimated_liquidation = Column(Float, nullable=True)

    # Progression evidence
    conditions_met = Column(Text, nullable=True)  # JSON list of completed checks
    conditions_remaining = Column(Text, nullable=True)  # JSON list of pending checks
    distance_to_entry_atr = Column(Float, nullable=True)
    distance_to_entry_usdt = Column(Float, nullable=True)
    news_window = Column(Text, nullable=True)  # JSON
    evidence_timeline = Column(Text, nullable=True)  # JSON

    # Timestamps
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    expires_at = Column(BigInteger, nullable=True)

    # Proximity & Unique Setup Instance tracking
    setup_instance_id = Column(String(100), nullable=True, index=True)
    near_entry_alerted_at = Column(BigInteger, nullable=True)
    near_entry_distance_price = Column(Float, nullable=True)
    near_entry_distance_atr = Column(Float, nullable=True)
    entry_zone_low = Column(Float, nullable=True)
    entry_zone_high = Column(Float, nullable=True)

    # V7 Trading Policy & Quota Fields
    strategy_family = Column(String(30), default="STANDARD_SMC")
    policy_config_version = Column(Integer, default=1)
    evidence_snapshot_id = Column(String(60), nullable=True)
    requested_risk_pct = Column(Float, default=0.25)
    effective_risk_pct = Column(Float, default=0.25)
    risk_profile = Column(String(30), default="STANDARD")
    cost_snapshot = Column(Text, nullable=True)

    # V14.2 additive plan provenance (NOT a state enum): PROVISIONAL (observation only) vs CONFIRMED
    # (strategy produced an active signal). direction_source: SIGNAL | TREND | UNCONFIRMED.
    plan_kind = Column(String(20), nullable=True)
    direction_source = Column(String(20), nullable=True)

    # V14.3 additive: NY session this fallback/standard watch belongs to (NULL on legacy rows).
    # Links WatchSetup -> PaperOrder.session_instance_id / SessionQuota by id, not by display name.
    session_instance_id = Column(String(60), nullable=True, index=True)

    # V14.4.1 additive: exact immutable refs of the decision that produced this watch revision.
    # scenario_snapshot_id -> smc_scenario_snapshots.snapshot_id; quality_snapshot_id -> setup_quality_snapshots.snapshot_id.
    scenario_snapshot_id = Column(String(80), nullable=True, index=True)
    quality_snapshot_id = Column(String(80), nullable=True, index=True)


class DomainEvent(BaseModel if False else Base):
    __tablename__ = "domain_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), nullable=False, unique=True, index=True)
    sequence = Column(Integer, nullable=False, index=True)
    schema_version = Column(String(20), default="1.0.0")
    event_type = Column(String(50), nullable=False, index=True)
    aggregate_id = Column(String(50), nullable=False, index=True)
    aggregate_version = Column(Integer, default=1)
    occurred_at = Column(BigInteger, nullable=False)
    published_at = Column(BigInteger, nullable=False)
    payload = Column(Text, nullable=False)  # JSON payload


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), nullable=True)
    channel = Column(String(20), default="TELEGRAM")
    recipient = Column(String(100), nullable=True)
    message_type = Column(String(50), nullable=False)
    dedupe_key = Column(String(120), nullable=False, unique=True, index=True)
    payload = Column(Text, nullable=False)  # JSON payload
    status = Column(String(20), default="PENDING", index=True)  # PENDING, SENT, FAILED, RETRYING, SUPPRESSED, AMBIGUOUS
    priority = Column(String(20), default="STANDARD", index=True)  # CRITICAL, STANDARD
    attempts = Column(Integer, default=0)
    last_attempt_at = Column(BigInteger, nullable=True)
    next_attempt_at = Column(BigInteger, nullable=True, index=True)
    occurred_at = Column(BigInteger, nullable=True)
    provider_message_id = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    lease_expires_at = Column(BigInteger, nullable=True)
    worker_id = Column(String(50), nullable=True)
    created_at = Column(BigInteger, nullable=False)


class TelegramConfig(Base):
    __tablename__ = "telegram_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    enabled = Column(Boolean, default=False)
    bot_token = Column(String(150), nullable=True)
    chat_id = Column(String(100), nullable=True)
    subscribed_events = Column(Text, nullable=True)  # JSON list
    quiet_hours_enabled = Column(Boolean, default=False)
    quiet_hours_start = Column(String(10), default="23:00")
    quiet_hours_end = Column(String(10), default="06:00")
    bypass_critical_quiet_hours = Column(Boolean, default=True)
    near_entry_mode = Column(String(20), default="ATR")  # ATR or PRICE_DISTANCE
    near_entry_atr_mult = Column(Float, default=0.5)
    near_entry_price_dist = Column(Float, default=2.0)
    near_entry_cooldown_min = Column(Integer, default=30)
    timezone = Column(String(50), default="Asia/Ho_Chi_Minh")
    base_chart_url = Column(String(255), nullable=True)
    updated_at = Column(BigInteger, nullable=False)


class ReplayRun(Base):
    __tablename__ = "replay_runs"

    id = Column(String(36), primary_key=True)
    run_name = Column(String(100), nullable=False)
    symbol = Column(String(20), default="XAUUSDT")
    start_ts = Column(BigInteger, nullable=False)
    end_ts = Column(BigInteger, nullable=False)
    initial_equity = Column(Float, default=1000.0)
    final_equity = Column(Float, nullable=False)
    total_trades = Column(Integer, default=0)
    net_wins = Column(Integer, default=0)
    net_losses = Column(Integer, default=0)
    breakevens = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    profit_factor = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    expectancy_r = Column(Float, default=0.0)
    config_snapshot = Column(Text, nullable=False)
    rvol_ablation_summary = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)

    trades = relationship("ReplayTrade", back_populates="run", cascade="all, delete-orphan")


class ReplayTrade(Base):
    __tablename__ = "replay_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), ForeignKey("replay_runs.id"), nullable=False)
    symbol = Column(String(20), default="XAUUSDT")
    direction = Column(String(10), nullable=False)
    entry_time = Column(BigInteger, nullable=False)
    exit_time = Column(BigInteger, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    gross_pnl = Column(Float, nullable=False)
    net_pnl = Column(Float, nullable=False)
    net_r = Column(Float, nullable=False)
    exit_cause = Column(String(50), nullable=False)
    rvol_at_entry = Column(Float, nullable=True)

    run = relationship("ReplayRun", back_populates="trades")


# ==================== V7 TRADING POLICY & QUOTA MODELS ====================

class TradingPolicy(Base):
    __tablename__ = "trading_policies"

    id = Column(String(50), primary_key=True, default="default")
    version = Column(Integer, default=1)
    mode = Column(String(20), default="PAPER")
    symbol = Column(String(20), default="XAUUSDT")
    max_daily_fills = Column(Integer, default=3)
    daily_min_goal = Column(Integer, default=2, nullable=True)
    daily_timezone = Column(String(50), default="Asia/Ho_Chi_Minh")
    entry_session_policy = Column(String(40), default="ALL_SESSIONS_WITH_NY_RESERVE")  # ALL_SESSIONS_WITH_NY_RESERVE or NY_ONLY
    ny_timezone = Column(String(50), default="America/New_York")
    ny_entry_start = Column(String(10), default="08:00")
    ny_entry_end = Column(String(10), default="11:00")
    ny_min_fills = Column(Integer, default=1)
    reserve_ny_slot = Column(Boolean, default=True)
    ny_fallback_enabled = Column(Boolean, default=True)
    ny_fallback_start = Column(String(10), default="10:30")
    ny_fallback_risk_pct_cap = Column(Float, default=0.10)
    min_net_rr = Column(Float, default=2.0)
    max_open_positions = Column(Integer, default=1)
    max_armed_orders = Column(Integer, default=1)
    updated_at = Column(BigInteger, nullable=False)


class SessionQuota(Base):
    __tablename__ = "session_quotas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_namespace = Column(String(50), default="default", index=True)
    symbol = Column(String(20), default="XAUUSDT", index=True)
    session_instance_id = Column(String(60), unique=True, index=True, nullable=False)  # e.g., NY-2026-10-09
    session_date_utc = Column(String(10), nullable=False)
    session_date_vn = Column(String(10), nullable=False)
    session_date_ny = Column(String(10), nullable=False)
    window_start_ms = Column(BigInteger, nullable=False)
    window_end_ms = Column(BigInteger, nullable=False)
    fallback_start_ms = Column(BigInteger, nullable=False)
    target_fills = Column(Integer, default=1)
    standard_fills = Column(Integer, default=0)
    fallback_fills = Column(Integer, default=0)
    total_fills = Column(Integer, default=0)
    quota_status = Column(String(30), default="NOT_STARTED", index=True)
    # NOT_STARTED, SEEKING_STANDARD, SEEKING_FALLBACK, ARMED_PENDING_FILL, FULFILLED, BLOCKED, MISSED, DISABLED, NOT_APPLICABLE
    last_status_reason = Column(String(255), nullable=True)
    last_evaluated_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class StrategyEvidence(Base):
    __tablename__ = "strategy_evidences"

    id = Column(String(60), primary_key=True)
    strategy_family = Column(String(30), default="STANDARD_SMC")
    strategy_version = Column(String(20), default="7.0.0")
    setup_id = Column(String(60), nullable=True)
    setup_instance_id = Column(String(100), nullable=True)
    direction = Column(String(10), nullable=False)
    htf_context = Column(Text, nullable=True)  # JSON
    h1_alignment = Column(String(20), default="UNKNOWN")
    sweep_evidence = Column(Text, nullable=True)  # JSON
    displacement_evidence = Column(Text, nullable=True)  # JSON
    fvg_evidence = Column(Text, nullable=True)  # JSON
    retest_evidence = Column(Text, nullable=True)  # JSON
    missing_evidence = Column(Text, nullable=True)  # JSON
    cost_snapshot = Column(Text, nullable=True)  # JSON
    risk_snapshot = Column(Text, nullable=True)  # JSON
    created_at = Column(BigInteger, nullable=False)



class SetupQualitySnapshot(Base):
    __tablename__ = "setup_quality_snapshots"

    snapshot_id = Column(String(80), primary_key=True)
    setup_id = Column(String(60), index=True, nullable=True)
    setup_instance_id = Column(String(100), index=True, nullable=True)
    setup_revision = Column(Integer, default=1)
    quality_revision = Column(Integer, default=1)
    symbol = Column(String(20), default="XAUUSDT")
    timeframe = Column(String(10), default="15M")
    direction = Column(String(10), nullable=False)
    strategy_family = Column(String(30), default="STANDARD_SMC")
    as_of_ms = Column(BigInteger, nullable=False, index=True)
    status = Column(String(30), nullable=False)
    score_pct = Column(Float, nullable=True)
    required_met = Column(Integer, default=0)
    required_total = Column(Integer, default=0)
    summary_vi = Column(String(255), nullable=True)
    payload_json = Column(Text, nullable=False)
    evidence_digest = Column(String(64), nullable=True)
    created_at = Column(BigInteger, nullable=False)



# ---------------------------------------------------------------------------
# V14.3 NY opportunity audit (additive tables; no secrets, no orders)
# ---------------------------------------------------------------------------
class NYOpportunityAudit(Base):
    """One row per UNIQUE opportunity: session + family + candidate/evidence identity.

    Scanning 100 times the same candidate updates `seen_count`/`last_seen_at`; it never inserts a new
    row. A material change is traced in `revisions_json` (bounded) without becoming a new opportunity.
    """
    __tablename__ = "ny_opportunity_audits"
    __table_args__ = (
        UniqueConstraint("session_instance_id", "strategy_family", "candidate_key", name="uq_ny_opportunity_identity"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_instance_id = Column(String(60), nullable=False, index=True)
    session_date_ny = Column(String(10), nullable=True, index=True)
    symbol = Column(String(20), default="XAUUSDT")
    strategy_family = Column(String(30), default="NY_QUOTA_PAPER")
    candidate_key = Column(String(100), nullable=False)
    direction = Column(String(10), nullable=True)
    stage = Column(String(24), default="STRATEGY_CANDIDATE")  # STRATEGY_CANDIDATE|FINANCIAL_ELIGIBLE|ARMED|FILLED
    outcome = Column(String(30), nullable=True)
    primary_reason_code = Column(String(60), nullable=True)
    reason_category = Column(String(12), nullable=True)  # DATA|STRATEGY|SAFETY|EXECUTION|SYSTEM
    reasons_json = Column(Text, nullable=True)            # ALL blockers (typed), primary is stable
    evidence_json = Column(Text, nullable=True)
    fingerprint = Column(String(64), nullable=True)
    revision = Column(Integer, default=1)
    revisions_json = Column(Text, nullable=True)
    is_financially_eligible = Column(Boolean, default=False)
    was_armed = Column(Boolean, default=False)
    was_filled = Column(Boolean, default=False)
    watch_setup_id = Column(String(50), nullable=True)
    order_id = Column(String(36), nullable=True)
    seen_count = Column(Integer, default=1)
    first_seen_at = Column(BigInteger, nullable=False)
    last_seen_at = Column(BigInteger, nullable=False)


class NYSessionAudit(Base):
    """One row per NY session: scan coverage, blockers by stage, primary missed reason, one summary."""
    __tablename__ = "ny_session_audits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_instance_id = Column(String(60), unique=True, nullable=False, index=True)
    session_date_ny = Column(String(10), nullable=True, index=True)
    symbol = Column(String(20), default="XAUUSDT")
    scan_count = Column(Integer, default=0)
    fallback_search_scans = Column(Integer, default=0)
    data_issue_scans = Column(Integer, default=0)
    last_quota_state = Column(String(30), nullable=True)
    blockers_json = Column(Text, nullable=True)           # {"stage:code": count}
    last_system_error = Column(Text, nullable=True)
    primary_missed_reason = Column(String(60), nullable=True)
    missed_category = Column(String(12), nullable=True)
    summary_json = Column(Text, nullable=True)
    summary_written_at = Column(BigInteger, nullable=True)  # set once; restart never rewrites
    first_scan_at = Column(BigInteger, nullable=True)
    last_scan_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=False)


# ---------------------------------------------------------------------------
# V14.4 SMC Scenarios and Immutable Decision Snapshots (Additive)
# ---------------------------------------------------------------------------
class SMCScenario(Base):
    __tablename__ = "smc_scenarios"

    scenario_id = Column(String(80), primary_key=True)
    instance_id = Column(String(100), nullable=False, index=True)
    revision = Column(Integer, default=1)
    symbol = Column(String(20), default="XAUUSDT", index=True)
    timeframe = Column(String(10), default="5M")
    strategy_family = Column(String(30), default="STANDARD_SMC")
    direction = Column(String(10), nullable=True)
    state = Column(String(30), default="OBSERVING", index=True)
    as_of_ms = Column(BigInteger, nullable=False)
    anchor_level_id = Column(String(80), nullable=True)
    target_level_id = Column(String(80), nullable=True)
    summary_vi = Column(Text, nullable=True)
    current_snapshot_id = Column(String(80), nullable=True, index=True)
    payload = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class SMCScenarioSnapshot(Base):
    __tablename__ = "smc_scenario_snapshots"

    snapshot_id = Column(String(80), primary_key=True)
    scenario_id = Column(String(80), index=True, nullable=False)
    instance_id = Column(String(100), index=True, nullable=False)
    revision = Column(Integer, default=1)
    symbol = Column(String(20), default="XAUUSDT")
    timeframe = Column(String(10), default="5M")
    direction = Column(String(10), nullable=True)
    state = Column(String(30), nullable=False)
    as_of_ms = Column(BigInteger, nullable=False, index=True)
    evidence_cutoff_ms = Column(BigInteger, nullable=False)
    evidence_digest = Column(String(64), nullable=False)
    plan_fingerprint = Column(String(32), nullable=True)
    payload = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class PaperObservation(Base):
    __tablename__ = "paper_observations"

    id = Column(String(80), primary_key=True)
    namespace = Column(String(40), nullable=False)
    event_kind = Column(String(30), nullable=False)  # SCENARIO_MATERIAL / ORDER_CLOSED
    snapshot_id = Column(String(160), nullable=True)  # legacy closed order may have no scenario ref
    scenario_id = Column(String(160), nullable=True)
    order_id = Column(String(36), nullable=True)
    as_of_ms = Column(BigInteger, nullable=False)
    payload_json = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint('namespace', 'event_kind', 'snapshot_id', 'order_id',
                         name='uq_paper_observation_key'),
    )


class ForecastObservation(Base):
    __tablename__ = "forecast_observations"

    id = Column(String(80), primary_key=True)
    forecast_id = Column(String(80), nullable=False, index=True)
    revision = Column(Integer, default=1)
    symbol = Column(String(20), default="XAUUSDT")
    session = Column(String(20), nullable=False)
    session_id = Column(String(80), nullable=False, index=True)
    as_of_ms = Column(BigInteger, nullable=False, index=True)
    context_cutoff_ms = Column(BigInteger, nullable=False)
    content_digest = Column(String(64), nullable=False)
    hypothesis_type = Column(String(50), nullable=False)
    direction = Column(String(10), nullable=True)
    target_price = Column(Float, nullable=True)
    invalidation_price = Column(Float, nullable=True)
    outcome_status = Column(String(50), default="PENDING")
    outcome_at_ms = Column(BigInteger, nullable=True)
    outcome_details = Column(Text, nullable=True)
    verification_status = Column(String(30), default="UNVERIFIED", nullable=True)
    invalidation_mode = Column(String(30), default="CLOSED_PRICE", nullable=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('session_id', 'content_digest', name='uq_forecast_session_digest'),
    )


# ---------------------------------------------------------------------------
# V15.0 Opportunity Funnel Record (Additive table for funnel telemetry & audits)
# ---------------------------------------------------------------------------
class OpportunityFunnelRecord(Base):
    __tablename__ = "opportunity_funnel_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    opportunity_id = Column(String(80), nullable=False, index=True)
    symbol = Column(String(20), default="XAUUSDT")
    model_id = Column(String(40), nullable=False, index=True)
    model_version = Column(String(20), default="v15.0")
    direction = Column(String(10), nullable=False)
    session_id = Column(String(60), nullable=False, index=True)
    run_id = Column(String(80), nullable=True, index=True)
    zone_id = Column(String(80), nullable=True)
    as_of_ms = Column(BigInteger, nullable=False, index=True)
    stage = Column(String(30), nullable=False, index=True)
    reason_codes_json = Column(Text, nullable=True)
    evidence_ids_json = Column(Text, nullable=True)
    metrics_json = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('opportunity_id', 'stage', name='uq_opportunity_stage'),
    )


# ---------------------------------------------------------------------------
# V16.15 Paper Research Run & Order Association Models (Rules 081-083, 091-096)
# ---------------------------------------------------------------------------
class PaperResearchRunRecord(Base):
    __tablename__ = "paper_research_run_records"

    run_id = Column(String(80), primary_key=True, index=True)
    scope = Column(String(50), nullable=True, index=True)
    symbol = Column(String(20), default="XAUUSDT")
    status = Column(String(20), default="COLLECTING", index=True)  # CREATED, COLLECTING, PAUSED, COMPLETED, DEGRADED, FAILED
    started_at_ms = Column(BigInteger, nullable=False)
    ended_at_ms = Column(BigInteger, nullable=True)
    software_version = Column(String(20), default="V16.18")
    config_fingerprint = Column(String(80), default="DEFAULT")
    execution_mode = Column(String(30), default="PAPER_SHADOW")  # PAPER_SHADOW or PAPER_EXECUTION
    observation_mode = Column(String(30), default="SHADOW")      # SHADOW or ACTIVE
    initial_capital_usdt = Column(Float, default=1000.0)
    current_capital_usdt = Column(Float, default=1000.0)
    metadata_json = Column(Text, nullable=True)
    created_at_ms = Column(BigInteger, nullable=False)
    updated_at_ms = Column(BigInteger, nullable=False)


class PaperResearchOrderLink(Base):
    __tablename__ = "paper_research_order_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(80), nullable=False, index=True)
    order_id = Column(String(36), nullable=False, index=True)
    opportunity_id = Column(String(80), nullable=True, index=True)
    trace_id = Column(String(80), nullable=True)
    linked_at_ms = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('run_id', 'order_id', name='uq_run_order'),
    )


class PaperResearchOrderMilestone(Base):
    __tablename__ = "paper_research_order_milestones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(80), nullable=False, index=True)
    order_id = Column(String(36), nullable=False, index=True)
    milestone = Column(String(30), nullable=False, index=True)
    timestamp_ms = Column(BigInteger, nullable=False)
    payload_json = Column(Text, nullable=True)
    created_at_ms = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('run_id', 'order_id', 'milestone', name='uq_run_order_milestone'),
    )


# Alias for backwards compatibility
PaperResearchMilestone = PaperResearchOrderMilestone


# ---------------------------------------------------------------------------
# V16.17 Durable Trace Persistence & Projection Checkpoints
# ---------------------------------------------------------------------------
class CG01TraceRecord(Base):
    __tablename__ = "cg01_trace_records"

    trace_id = Column(String(80), primary_key=True)
    symbol = Column(String(20), default="XAUUSDT", index=True)
    run_id = Column(String(80), nullable=True, index=True)
    observation_mode = Column(String(30), default="SHADOW", index=True)
    source_event_id = Column(String(120), nullable=True)
    input_version = Column(String(80), nullable=True)
    evaluation_status = Column(String(30), default="COMPLETED")
    candidate_key = Column(String(80), nullable=True)
    armed_order_id = Column(String(36), nullable=True)
    total_runtime_ms = Column(Float, nullable=True)
    feed_delay_ms = Column(Float, nullable=True)
    scheduler_delay_ms = Column(Float, nullable=True)
    arm_delay_ms = Column(Float, nullable=True)
    fill_delay_ms = Column(Float, nullable=True)
    payload_json = Column(Text, nullable=True)
    created_at_ms = Column(BigInteger, nullable=False, index=True)


class PaperResearchCheckpoint(Base):
    __tablename__ = "paper_research_checkpoints"

    scope = Column(String(50), primary_key=True)
    last_event_id = Column(String(80), nullable=True)
    last_event_time_ms = Column(BigInteger, default=0)
    last_sequence = Column(Integer, default=0)
    updated_at_ms = Column(BigInteger, nullable=False)


# ---------------------------------------------------------------------------
# V16.18 Durable Pending Projections & Observation Intervals (Rules 078, 112)
# ---------------------------------------------------------------------------
class PaperResearchPendingProjection(Base):
    __tablename__ = "paper_research_pending_projections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(80), nullable=False, index=True)
    scope = Column(String(50), default="CG01", index=True)
    event_type = Column(String(50), nullable=False)
    aggregate_id = Column(String(80), nullable=True, index=True)
    order_id = Column(String(36), nullable=True, index=True)
    run_id = Column(String(80), nullable=True, index=True)
    reason = Column(String(100), nullable=False)
    attempt_count = Column(Integer, default=0)
    next_retry_at_ms = Column(BigInteger, default=0)
    first_seen_at_ms = Column(BigInteger, nullable=False)
    last_error = Column(Text, nullable=True)
    status = Column(String(30), default="PENDING", index=True)  # PENDING, RESOLVED, UNVERIFIABLE
    payload_json = Column(Text, nullable=True)
    created_at_ms = Column(BigInteger, nullable=False)
    updated_at_ms = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('event_id', 'scope', name='uq_pending_proj_event_scope'),
    )

    resolution_status = synonym("status")
    next_retry_at = synonym("next_retry_at_ms")
    first_seen_at = synonym("first_seen_at_ms")


class PaperObservationInterval(Base):
    __tablename__ = "paper_observation_intervals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(50), default="CG01", index=True)
    run_id = Column(String(80), nullable=True, index=True)
    symbol = Column(String(20), default="XAUUSDT")
    worker_role = Column(String(40), default="SCHEDULER")
    mode = Column(String(30), default="SHADOW")
    start_ms = Column(BigInteger, nullable=False)
    end_ms = Column(BigInteger, nullable=False)
    data_quality = Column(String(30), default="GOOD")
    heartbeats_count = Column(Integer, default=1)
    created_at_ms = Column(BigInteger, nullable=False)


# ---------------------------------------------------------------------------
# V17.6 Engine Observability, Decision Intelligence & Operational Alerts
# ---------------------------------------------------------------------------
class EngineHeartbeatRecord(Base):
    __tablename__ = "engine_heartbeats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    engine_instance_id = Column(String(80), nullable=False, unique=True, index=True)
    symbol = Column(String(20), default="XAUUSDT", index=True)
    run_id = Column(String(80), nullable=True, index=True)
    state = Column(String(30), nullable=False, default="IDLE", index=True)  # RUNNING, IDLE, WAITING_MARKET, PAUSED, DEGRADED, STALLED, STOPPED, UNKNOWN
    last_process_heartbeat_at_ms = Column(BigInteger, nullable=False)
    last_evaluation_started_at_ms = Column(BigInteger, nullable=True)
    last_evaluation_completed_at_ms = Column(BigInteger, nullable=True)
    last_successful_evaluation_at_ms = Column(BigInteger, nullable=True)
    last_cycle_id = Column(String(80), nullable=True)
    cycles_count = Column(Integer, default=0)
    failed_cycles_count = Column(Integer, default=0)
    cadence_sec = Column(Integer, default=15)
    metadata_json = Column(Text, nullable=True)
    updated_at_ms = Column(BigInteger, nullable=False)


class OpportunityDecisionEvent(Base):
    __tablename__ = "opportunity_decision_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(80), nullable=False, unique=True, index=True)
    event_version = Column(Integer, default=1)
    run_id = Column(String(80), nullable=True, index=True)
    cycle_id = Column(String(80), nullable=True, index=True)
    opportunity_id = Column(String(80), nullable=False, index=True)
    symbol = Column(String(20), default="XAUUSDT", nullable=True, index=True)
    model_id = Column(String(40), nullable=False, index=True)
    direction = Column(String(10), nullable=False)
    stage = Column(String(30), nullable=False, index=True)
    primary_reason_code = Column(String(80), nullable=True)
    reason_category = Column(String(40), nullable=True)  # STRUCTURE, MARKET_DATA, RISK, NET_RR, NEWS, SESSION, CAPITAL, EXECUTION, CONFIGURATION, SYSTEM
    reason_codes_json = Column(Text, nullable=True)
    evidence_ids_json = Column(Text, nullable=True)
    waiting_conditions_json = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)
    occurred_at_ms = Column(BigInteger, nullable=False, index=True)
    created_at_ms = Column(BigInteger, nullable=False)


class EngineIncidentRecord(Base):
    __tablename__ = "engine_incidents"

    id = Column(String(80), primary_key=True)
    severity = Column(String(20), nullable=False)  # INFO, WARNING, CRITICAL, RECOVERED
    component = Column(String(40), nullable=False)
    engine_instance_id = Column(String(80), nullable=True)
    run_id = Column(String(80), nullable=True)
    cycle_id = Column(String(80), nullable=True)
    opportunity_id = Column(String(80), nullable=True)
    order_id = Column(String(36), nullable=True)
    reason_code = Column(String(80), nullable=True)
    message = Column(Text, nullable=False)
    status = Column(String(20), default="ACTIVE", index=True)  # ACTIVE, RECOVERED
    created_at_ms = Column(BigInteger, nullable=False, index=True)
    recovered_at_ms = Column(BigInteger, nullable=True)
    payload_json = Column(Text, nullable=True)


class EngineEvaluationCycleRecord(Base):
    __tablename__ = "engine_evaluation_cycles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cycle_id = Column(String(80), nullable=False, unique=True, index=True)
    engine_instance_id = Column(String(80), nullable=False, index=True)
    run_id = Column(String(80), nullable=True, index=True)
    symbol = Column(String(20), default="XAUUSDT", index=True)
    status = Column(String(30), nullable=False, index=True)  # COMPLETED, FAILED, SKIPPED, ABORTED
    started_at_ms = Column(BigInteger, nullable=False)
    completed_at_ms = Column(BigInteger, nullable=False)
    duration_ms = Column(Float, default=0.0)
    decision_status = Column(String(30), nullable=True)  # NO_SETUP, BLOCKED, ARMED, WAITING, SKIPPED, FAILED
    skip_reason = Column(String(80), nullable=True)
    failure_reason = Column(Text, nullable=True)
    opportunities_detected = Column(Integer, default=0)
    opportunities_qualified = Column(Integer, default=0)
    opportunities_blocked = Column(Integer, default=0)
    armed_order_id = Column(String(36), nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at_ms = Column(BigInteger, nullable=False)



