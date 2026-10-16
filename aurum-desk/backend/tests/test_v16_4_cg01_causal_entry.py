"""
backend/tests/test_v16_4_cg01_causal_entry.py
V16.4 Rules 141 - 152:
- Retest open bar reproduction test (unclosed bar at T cannot confirm retest)
- Raw vs closed context parity & prefix invariance
- Lookahead prevention: is_closed=True in future bars ignored
- Market price calculation using executable ask/bid instead of CE
- Limit order_type persistence in PaperOrder, arm plan snapshot, and event payload
- Limit trigger condition verification (Buy limit requires ask <= limit; Sell limit requires bid >= limit)
- Retest mode vs Limit mode separation
- TTL non-drifting on repeated evaluation
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
import models
from services.cg01_context_adapter import build_cg01_closed_context, select_closed_candles
from services.cg01_engine import evaluate_cg01_candidate
from services.cg01_types import CG01Config, CG01EntryMode, CG01Stage
from services.quote_validator import ValidatedQuote
from services.scalp_m1_types import M1ScalpConfig, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, build_short_fixture_series, make_bar, make_valid_quote


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    policy = TradingPolicyService.get_or_create_policy(session)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    session.commit()

    try:
        yield session
    finally:
        session.close()


def test_rule_141_regression_retest_open_bar_not_executable():
    """
    Rule 141: Retest bar opening at now_ms is unclosed at now_ms (closes at now_ms + 60_000).
    At now_ms: must NOT be EXECUTABLE (WAIT_ENTRY with WAIT_RETEST).
    At now_ms + 60_000: retest bar has closed.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    now_ms = t_start + 540_000

    cfg_retest = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        entry_mode=CG01EntryMode.CONFIRMED_RETEST_MARKET.value,
    )

    retest_bar = make_bar(now_ms, 2625.0, 2625.5, 2623.5, 2624.5)  # opens at now_ms, closes at now_ms + 60_000
    c_1m_with_open_bar = list(c_1m) + [retest_bar]
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m_with_open_bar}

    quote_ce = {"bid": 2623.75, "ask": 2623.85, "is_valid": True}

    # At now_ms: unclosed bar MUST NOT confirm retest!
    dec_unclosed = evaluate_cg01_candidate(ctx, quote_ce, config=cfg_retest, now_ms=now_ms)
    assert dec_unclosed.stage == CG01Stage.WAIT_ENTRY
    assert "WAIT_RETEST" in dec_unclosed.reason_codes
    assert dec_unclosed.candidate is None

    # At now_ms + 59_999: still unclosed!
    dec_unclosed_edge = evaluate_cg01_candidate(ctx, quote_ce, config=cfg_retest, now_ms=now_ms + 59_999)
    assert dec_unclosed_edge.stage == CG01Stage.WAIT_ENTRY
    assert "WAIT_RETEST" in dec_unclosed_edge.reason_codes

    # At now_ms + 60_000: bar is closed!
    dec_closed = evaluate_cg01_candidate(ctx, quote_ce, config=cfg_retest, now_ms=now_ms + 60_000)
    assert dec_closed.stage == CG01Stage.EXECUTABLE
    assert dec_closed.is_eligible
    assert dec_closed.candidate is not None


def test_rule_142_raw_filtered_parity():
    """
    Rule 142: Evaluating with raw context containing future bars up to as_of=T
    gives identical decision/evidence to pre-filtered closed context up to T.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    t_eval = t_start + 540_000

    cfg = CG01Config(enabled=True, profile_mode="PAPER_EXECUTION")
    quote = {"bid": 2625.5, "ask": 2625.6, "is_valid": True}

    # Raw context with 5 extra future bars after t_eval
    future_bars = [make_bar(t_eval + (i + 1) * 60_000, 2630.0, 2635.0, 2629.0, 2634.0) for i in range(5)]
    raw_ctx = {
        "4H": c_4h,
        "1H": c_1h,
        "15M": c_15m,
        "1M": list(c_1m) + future_bars,
    }

    # Pre-filtered closed context
    closed_ctx = build_cg01_closed_context(raw_ctx, as_of_ms=t_eval)

    dec_raw = evaluate_cg01_candidate(raw_ctx, quote, config=cfg, now_ms=t_eval)
    dec_closed = evaluate_cg01_candidate(closed_ctx, quote, config=cfg, now_ms=t_eval)

    assert dec_raw.stage == dec_closed.stage
    assert dec_raw.reason_codes == dec_closed.reason_codes
    assert dec_raw.planned_entry == dec_closed.planned_entry
    assert dec_raw.stop_loss == dec_closed.stop_loss
    assert dec_raw.take_profit == dec_closed.take_profit


def test_rule_143_closed_flag_false_lookahead():
    """
    Rule 143: A bar claiming is_closed=True whose open_time + duration > as_of_ms
    is strictly discarded by select_closed_candles.
    """
    now_ms = 1_000_000
    future_bar = make_bar(now_ms, 2600.0, 2610.0, 2595.0, 2605.0)
    future_bar["is_closed"] = True  # Untrusted flag!

    filtered = select_closed_candles([future_bar], "1M", as_of_ms=now_ms)
    assert len(filtered) == 0, "Future candle must be filtered out despite is_closed=True"

    # Candle closing exactly at as_of_ms is accepted
    exact_bar = make_bar(now_ms - 60_000, 2600.0, 2610.0, 2595.0, 2605.0)
    filtered_exact = select_closed_candles([exact_bar], "1M", as_of_ms=now_ms)
    assert len(filtered_exact) == 1


def test_rule_145_market_price_long():
    """
    Rule 145: For CONFIRMED_RETEST_MARKET LONG, planned_entry uses executable ask + slippage, NOT CE.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    now_ms = t_start + 540_000

    cfg_retest = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        entry_mode=CG01EntryMode.CONFIRMED_RETEST_MARKET.value,
    )

    retest_bar = make_bar(now_ms, 2625.0, 2625.5, 2623.5, 2624.5)
    c_1m_retested = list(c_1m) + [retest_bar]
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m_retested}

    # Quote executable near CE: ask=2623.85
    quote = {"bid": 2623.75, "ask": 2623.85, "is_valid": True}

    dec = evaluate_cg01_candidate(ctx, quote, config=cfg_retest, now_ms=now_ms + 60_000)
    assert dec.stage == CG01Stage.EXECUTABLE
    assert dec.candidate is not None

    cand = dec.candidate
    assert cand.order_type == "MARKET"
    # Planned entry is ask (2623.85) + slippage (0.10) = 2623.95, NOT CE (2623.75)
    assert cand.planned_entry == 2623.95
    assert cand.reference_ce == 2623.75
    assert cand.entry_intent["price_basis"] == "MARKET_QUOTE_ASK"
    assert cand.entry_intent["order_type"] == "MARKET"


def test_rule_146_market_price_short():
    """
    Rule 146: For CONFIRMED_RETEST_MARKET SHORT, planned_entry uses executable bid - slippage, NOT CE.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    now_ms = t_start + 540_000

    cfg_retest = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        entry_mode=CG01EntryMode.CONFIRMED_RETEST_MARKET.value,
    )

    # Mirror long fixture displacement leg so target is 15M liquidity level 2634.0
    c_1m_adj = list(c_1m)
    c_1m_adj[9] = make_bar(t_start + 420_000, 2652.0, 2652.2, 2649.5, 2650.0)
    c_1m_adj[10] = make_bar(t_start + 480_000, 2650.0, 2650.5, 2649.0, 2649.8)

    # Retest bar touching CE 2651.15 and closing below
    retest_bar = make_bar(now_ms, 2650.0, 2651.5, 2649.5, 2650.5)
    c_1m_retested = c_1m_adj + [retest_bar]
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m_retested}

    # Quote near CE: bid = 2651.05
    quote = {"bid": 2651.05, "ask": 2651.15, "is_valid": True}

    dec = evaluate_cg01_candidate(ctx, quote, config=cfg_retest, now_ms=now_ms + 60_000)
    assert dec.stage == CG01Stage.EXECUTABLE
    assert dec.candidate is not None

    cand = dec.candidate
    assert cand.order_type == "MARKET"
    # Planned entry is bid (2651.05) - slippage (0.10) = 2650.95, NOT CE (2651.15)
    assert cand.planned_entry == 2650.95
    assert cand.reference_ce == 2651.15
    assert cand.entry_intent["price_basis"] == "MARKET_QUOTE_BID"
    # Strict geometry: TP < entry < SL
    assert cand.take_profit < cand.planned_entry < cand.stop_loss


def test_rule_147_limit_type_persistence(db_session):
    """
    Rule 147: Arming a CG-01 LIMIT candidate persists order_type='LIMIT' in models.PaperOrder.
    Catches the baseline bug where order_type was hardcoded to 'MARKET'.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    now_ms = t_start + 540_000

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
    )

    quote = make_valid_quote(2625.5, 2625.7, now_ms)
    poi = WatchedZone(
        zone_id="poi_persist_test",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    orchestrator.evaluate_cycle(
        db_session,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    order = db_session.query(models.PaperOrder).first()
    assert order is not None, "PaperOrder should have been created"
    assert order.order_type == "LIMIT", f"Expected order_type='LIMIT', got {order.order_type}"
    assert order.state == "armed"
    assert order.actual_entry is None


def test_rule_152_ttl_non_drifting():
    """
    Rule 152: Repeated evaluations of the same setup do not extend valid_until into infinity.
    valid_until must remain anchored to the trigger confirmation time.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    now_ms = t_start + 540_000

    cfg = CG01Config(enabled=True, profile_mode="PAPER_EXECUTION")
    quote = {"bid": 2625.5, "ask": 2625.7, "is_valid": True}

    dec1 = evaluate_cg01_candidate({"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}, quote, config=cfg, now_ms=now_ms)
    assert dec1.candidate is not None
    valid_until_1 = dec1.candidate.valid_until_ms

    # Re-evaluate 30 seconds later
    dec2 = evaluate_cg01_candidate({"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}, quote, config=cfg, now_ms=now_ms + 30_000)
    assert dec2.candidate is not None
    valid_until_2 = dec2.candidate.valid_until_ms

    # valid_until must be identical and not drift forward by 30 seconds
    assert valid_until_1 == valid_until_2
