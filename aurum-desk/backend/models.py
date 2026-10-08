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
    risk_pct = Column(Float, default=0.5)
    gross_rr = Column(Float, default=2.0)
    estimated_net_rr = Column(Float, default=1.9)
    fees_assumption = Column(Float, default=0.10)
    slippage_assumption = Column(Float, default=0.10)

    # Execution & PnL
    realized_pnl_net = Column(Float, nullable=True)
    realized_r = Column(Float, nullable=True)
    exit_cause = Column(String(30), nullable=True)  # TP_HIT, SL_HIT, MANUAL_CLOSE, EXPIRED, INVALIDATED
    invalidation_reason = Column(Text, nullable=True)
    strategy_version = Column(String(20), default="1.0.0")

    # Timestamps (epoch ms)
    created_at = Column(BigInteger, nullable=False)
    armed_at = Column(BigInteger, nullable=True)
    opened_at = Column(BigInteger, nullable=True)
    closed_at = Column(BigInteger, nullable=True)
    expires_at = Column(BigInteger, nullable=True)

    # Context snapshot
    checklist_snapshot = Column(Text, nullable=True)  # JSON
    lessons_retrieved = Column(Text, nullable=True)   # JSON


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

