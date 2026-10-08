from sqlalchemy import Column, Integer, String, Float, BigInteger, Boolean, Text, UniqueConstraint, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

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
