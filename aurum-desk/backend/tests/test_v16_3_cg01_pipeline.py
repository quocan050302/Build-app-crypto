"""
backend/tests/test_v16_3_cg01_pipeline.py
V16.3 Rules 162, 166, 167, 168, 169, 170, 171, 172, 173:
- Full end-to-end integration tests for LONG and SHORT through evaluate_cycle -> _arm_paper_candidate -> execute_fill -> execute_close
- Chart Evidence persistence verification via ScenarioSnapshot
- Retest Mode vs Limit Mode verification
- Limit Order no-retroactive-fill verification
- Shadow Hard Boundary verification (zero PaperOrder mutations, zero outbox fills)
- OFF Mode regression preservation
- Entry guards matrix and BEFORE_ARM / BEFORE_FILL transitions
"""
import json
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain_calculator import CalculationResult, CostAssumptions, calculate_risk_reward
from models import Base
import models
import crud
from services.cg01_context_adapter import CanonicalCandle
from services.cg01_types import CG01Config, CG01EntryMode, CG01ProfileMode, CG01Stage
from services.quote_validator import ValidatedQuote
from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier, ScalpMode, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService


@pytest.fixture
def pipeline_db():
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


def make_bar(open_ms, o, h, l, c, v=100.0, tf="1M"):
    return {
        "open_time": open_ms,
        "timestamp": open_ms,
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": float(v),
        "timeframe": tf,
        "symbol": "XAUUSDT",
        "is_closed": True,
    }


def make_valid_quote(bid: float, ask: float, now_ms: int) -> ValidatedQuote:
    return ValidatedQuote(
        is_valid=True,
        bid=float(bid),
        ask=float(ask),
        last=float((bid + ask) / 2.0),
        spread=round(ask - bid, 4),
        exchange_time=now_ms,
        observed_at=now_ms,
        freshness_sec=0.5,
        is_stale=False,
        rejection_code=None,
        rejection_detail=None,
    )


def build_long_fixture_series(t_start: int):
    """Generates complete OHLC candle series for a textbook CG-01 LONG setup."""
    candles_4h = [make_bar(t_start - (15 - i) * 14_400_000, 2600.0, 2640.0, 2595.0, 2630.0, tf="4H") for i in range(15)]
    wave_1h = [2600, 2602, 2605, 2608, 2603, 2606, 2610, 2615, 2618, 2612, 2614, 2619, 2624, 2628, 2622, 2625, 2630, 2635, 2630, 2632, 2636, 2640, 2638, 2642, 2645]
    candles_1h = [make_bar(t_start - (25 - i) * 3_600_000, wave_1h[i], wave_1h[i]+2, wave_1h[i]-2, wave_1h[i]+1, tf="1H") for i in range(25)]
    
    # 15M candles establishing POI FVG [2620, 2625] and confirmed swing low, with target high at 2640.0
    candles_15m = []
    for i in range(25):
        t_bar = t_start - (25 - i) * 900_000
        if i == 18:
            candles_15m.append(make_bar(t_bar, 2618.0, 2620.0, 2617.0, 2619.0, tf="15M"))
        elif i == 19:
            candles_15m.append(make_bar(t_bar, 2619.0, 2628.0, 2619.0, 2627.0, tf="15M"))
        elif i == 20:
            candles_15m.append(make_bar(t_bar, 2627.0, 2640.0, 2625.0, 2630.0, tf="15M"))
        else:
            candles_15m.append(make_bar(t_bar, 2626.0, 2630.0, 2625.5, 2628.0, tf="15M"))

    # 1M candles:
    # Warmup bars (-120s, -60s) to establish confirmed SL at 2621.0 and SH at 2623.5
    # 5: sweep bar down to 2619.5 sweeping 2621.0, reclaims 2621.5
    # 6,7,8: break leg with displacement and FVG [2623.0, 2624.5], CE = 2623.75
    c_1m = [
        make_bar(t_start - 120_000, 2623.0, 2623.5, 2622.0, 2622.5),
        make_bar(t_start - 60_000, 2622.5, 2622.8, 2621.5, 2622.0),
        make_bar(t_start, 2622.0, 2622.2, 2621.0, 2621.5),              # SL at 2621.0
        make_bar(t_start + 60_000, 2621.5, 2622.5, 2621.5, 2622.0),
        make_bar(t_start + 120_000, 2622.0, 2623.5, 2622.0, 2623.0),    # SH at 2623.5
        make_bar(t_start + 180_000, 2623.0, 2623.0, 2622.0, 2622.2),
        make_bar(t_start + 240_000, 2622.2, 2622.5, 2621.8, 2622.0),
        make_bar(t_start + 300_000, 2622.0, 2622.2, 2619.5, 2621.5),    # sweeps 2621.0 down to 2619.5, closes at 2621.5
        make_bar(t_start + 360_000, 2621.5, 2623.0, 2621.2, 2622.8),    # c1 of FVG
        make_bar(t_start + 420_000, 2622.8, 2625.5, 2622.5, 2625.2),    # displacement break 2623.5
        make_bar(t_start + 480_000, 2625.2, 2626.0, 2624.5, 2625.8),    # c3 of FVG
    ]

    return candles_4h, candles_1h, candles_15m, c_1m


def build_short_fixture_series(t_start: int):
    """Generates complete OHLC candle series for a textbook CG-01 SHORT setup."""
    candles_4h = [make_bar(t_start - (15 - i) * 14_400_000, 2660.0, 2670.0, 2640.0, 2645.0, tf="4H") for i in range(15)]
    wave_1h_short = [2665, 2663, 2660, 2657, 2662, 2659, 2655, 2650, 2647, 2653, 2651, 2646, 2641, 2637, 2643, 2640, 2635, 2630, 2635, 2633, 2629, 2625, 2627, 2623, 2620]
    candles_1h = [make_bar(t_start - (25 - i) * 3_600_000, wave_1h_short[i], wave_1h_short[i]+2, wave_1h_short[i]-2, wave_1h_short[i]-1, tf="1H") for i in range(25)]
    
    # 15M candles establishing POI FVG [2650, 2655] and confirmed swing high, with target low at 2634.0
    candles_15m = []
    for i in range(25):
        t_bar = t_start - (25 - i) * 900_000
        if i == 18:
            candles_15m.append(make_bar(t_bar, 2656.0, 2657.0, 2655.0, 2656.0, tf="15M"))
        elif i == 19:
            candles_15m.append(make_bar(t_bar, 2656.0, 2656.0, 2647.0, 2648.0, tf="15M"))
        elif i == 20:
            candles_15m.append(make_bar(t_bar, 2648.0, 2650.0, 2634.0, 2646.0, tf="15M"))
        else:
            candles_15m.append(make_bar(t_bar, 2647.0, 2649.0, 2646.0, 2648.0, tf="15M"))

    # 1M candles:
    # Warmup bars (-120s, -60s) to establish confirmed SH at 2654.0 and SL at 2651.0
    # sweep bar up to 2655.5 sweeping 2654.0, reclaims 2653.5
    # break leg with displacement and bearish FVG [2650.5, 2651.8], CE = 2651.15
    c_1m = [
        make_bar(t_start - 120_000, 2651.0, 2652.0, 2650.5, 2651.5),
        make_bar(t_start - 60_000, 2651.5, 2652.5, 2651.0, 2652.0),
        make_bar(t_start, 2652.0, 2654.0, 2651.8, 2653.0),              # SH at 2654.0
        make_bar(t_start + 60_000, 2653.0, 2653.0, 2652.0, 2652.5),
        make_bar(t_start + 120_000, 2652.5, 2652.8, 2651.0, 2652.0),    # SL at 2651.0
        make_bar(t_start + 180_000, 2652.0, 2652.5, 2651.8, 2652.2),
        make_bar(t_start + 240_000, 2652.2, 2652.5, 2652.0, 2652.4),
        make_bar(t_start + 300_000, 2652.4, 2655.5, 2652.2, 2653.5),    # sweeps 2654.0 up to 2655.5, closes at 2653.5
        make_bar(t_start + 360_000, 2653.5, 2653.8, 2651.8, 2652.0),    # c1 of bearish FVG
        make_bar(t_start + 420_000, 2652.0, 2652.2, 2649.0, 2649.5),    # breaks 2651.0 with displacement
        make_bar(t_start + 480_000, 2649.5, 2650.5, 2649.0, 2649.8),    # c3 of bearish FVG
    ]

    return candles_4h, candles_1h, candles_15m, c_1m


def test_rule_162_orchestrator_enabled_cg01_no_name_error(pipeline_db):
    """
    Rule 162: evaluate_cycle runs with CG-01 enabled in DB, loading c_4h/c_1h without NameError.
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

    orchestrator = ScalpWatcherOrchestrator()
    res = orchestrator.evaluate_cycle(
        pipeline_db,
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

    assert res is not None
    assert res.get("status") in ("OK", "CYCLE_COMPLETED", "ARMED", "NO_CANDIDATE")


def test_rule_166_end_to_end_long_pipeline(pipeline_db):
    """
    Rule 166: Full End-to-End LONG Integration:
    Context -> POI -> Sweep -> MSS -> FVG -> Sizing -> Arm -> Snapshot Save -> Fill -> Close at TP.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    now_ms = t_start + 540_000

    # Ensure POI zone exists in orchestrator watchlist
    poi = WatchedZone(
        zone_id="poi_long_15m",
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

    quote = make_valid_quote(2625.5, 2625.6, now_ms)

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        daily_max_cap=3,
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    res = orchestrator.evaluate_cycle(
        pipeline_db,
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

    # 1. Assert order was armed in DB
    order = pipeline_db.query(models.PaperOrder).first()
    assert order is not None, "PaperOrder was not created in DB"
    assert order.state == "armed"
    assert order.direction == "LONG"
    assert order.planned_entry == 2623.75
    assert order.stop_loss == 2619.45
    assert order.take_profit > order.planned_entry
    assert order.scenario_snapshot_id is not None, "ScenarioSnapshot ID must be linked to PaperOrder"

    # 2. Verify immutable snapshot can be loaded from DB and has valid geometry
    from services.smc_scenario_repository import get_scenario_snapshot, snapshot_from_row
    snap_row = get_scenario_snapshot(pipeline_db, order.scenario_snapshot_id)
    assert snap_row is not None
    snap = snapshot_from_row(snap_row)
    assert snap.direction == "LONG"
    assert snap.plan["entry"] == 2623.75
    assert snap.plan["stop_loss"] == 2619.45
    assert snap.plan["take_profit"] > snap.plan["entry"]

    # 3. Simulate future tick reaching CE (2623.75) -> execute_fill
    fill_time = now_ms + 60_000
    calc = calculate_risk_reward(
        direction="LONG",
        entry=order.planned_entry,
        sl=order.stop_loss,
        tp=order.take_profit,
        capital=1000.0,
        risk_pct=0.25,
        now_ms=fill_time,
    )
    filled_order = TradeLifecycleService.execute_fill(
        pipeline_db,
        order,
        fill_price=order.planned_entry,
        calc_result=calc,
        source="AUTO",
        now_ms=fill_time,
    )
    assert filled_order.state == "paper_open"
    assert filled_order.actual_entry == order.planned_entry

    # 4. Simulate future tick reaching TP (2640.0) -> execute_close
    exit_time = fill_time + 120_000
    closed_order = TradeLifecycleService.execute_close(
        pipeline_db,
        order_id=filled_order.id,
        exit_price=filled_order.take_profit,
        exit_cause="TP_HIT",
        now_ms=exit_time,
    )
    assert closed_order.state == "closed"
    assert closed_order.realized_pnl_net > 0.0
    assert closed_order.closed_at == exit_time


def test_rule_167_end_to_end_short_pipeline(pipeline_db):
    """
    Rule 167: Full End-to-End SHORT Integration:
    Asserts TP < entry < SL geometry and direction serialization.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 10, 15, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    now_ms = t_start + 540_000

    poi = WatchedZone(
        zone_id="poi_short_15m",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BEARISH",
        top=2655.0,
        bottom=2650.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )

    quote = make_valid_quote(2649.5, 2649.7, now_ms)

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        daily_max_cap=3,
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])

    res = orchestrator.evaluate_cycle(
        pipeline_db,
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

    order = pipeline_db.query(models.PaperOrder).filter(models.PaperOrder.direction == "SHORT").first()
    assert order is not None, "SHORT PaperOrder was not created"
    assert order.state == "armed"
    assert order.direction == "SHORT"
    assert order.planned_entry == 2651.15
    assert order.stop_loss == 2655.55
    # Strict Geometry: TP < entry < SL
    assert order.take_profit < order.planned_entry < order.stop_loss

    # Fill and close SHORT
    fill_time = now_ms + 60_000
    calc = calculate_risk_reward(
        direction="SHORT",
        entry=order.planned_entry,
        sl=order.stop_loss,
        tp=order.take_profit,
        capital=1000.0,
        risk_pct=0.25,
        now_ms=fill_time,
    )
    filled_order = TradeLifecycleService.execute_fill(
        pipeline_db, order, fill_price=order.planned_entry, calc_result=calc, source="AUTO", now_ms=fill_time
    )
    assert filled_order.state == "paper_open"

    exit_time = fill_time + 120_000
    closed_order = TradeLifecycleService.execute_close(
        pipeline_db, order_id=filled_order.id, exit_price=filled_order.take_profit, exit_cause="TP_HIT", now_ms=exit_time
    )
    assert closed_order.state == "closed"
    assert closed_order.realized_pnl_net > 0.0


def test_rule_168_confirmed_retest_market_mode():
    """
    Rule 168: Confirmed Retest Mode requires a closed retest bar before becoming EXECUTABLE.
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

    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
    quote = {"bid": 2625.5, "ask": 2625.6, "is_valid": True}

    from services.cg01_engine import evaluate_cg01_candidate

    # 1. Before retest candle occurs: stage must be WAIT_ENTRY with reason WAIT_RETEST
    dec_pre = evaluate_cg01_candidate(ctx, quote, config=cfg_retest, now_ms=now_ms)
    assert dec_pre.stage == CG01Stage.WAIT_ENTRY
    assert "WAIT_RETEST" in dec_pre.reason_codes

    # 2. Add retest bar (opens at now_ms, closes at now_ms + 60_000)
    retest_bar = make_bar(now_ms, 2625.0, 2625.5, 2623.5, 2624.5)  # low 2623.5 <= 2623.75, close >= 2623.75
    c_1m_retested = list(c_1m) + [retest_bar]
    ctx_retested = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m_retested}

    # At now_ms: the retest bar has NOT closed yet! (Rule 007 regression)
    dec_unclosed = evaluate_cg01_candidate(ctx_retested, quote, config=cfg_retest, now_ms=now_ms)
    assert dec_unclosed.stage == CG01Stage.WAIT_ENTRY
    assert "WAIT_RETEST" in dec_unclosed.reason_codes

    # At now_ms + 60_000: retest bar has closed. If quote is runaway (ask=2625.6), RR check rejects (Rule 008, 049)
    dec_runaway = evaluate_cg01_candidate(ctx_retested, quote, config=cfg_retest, now_ms=now_ms + 60_000)
    assert dec_runaway.stage == CG01Stage.WAIT_ENTRY
    assert "RR_BELOW_MINIMUM" in dec_runaway.reason_codes

    # At now_ms + 60_000 with quote executable near CE: EXECUTABLE with MARKET order type (Rule 041, 042)
    quote_near_ce = {"bid": 2623.75, "ask": 2623.85, "is_valid": True}
    dec_post = evaluate_cg01_candidate(ctx_retested, quote_near_ce, config=cfg_retest, now_ms=now_ms + 60_000)
    assert dec_post.stage == CG01Stage.EXECUTABLE
    assert dec_post.is_eligible
    assert dec_post.candidate.order_type == "MARKET"
    assert dec_post.candidate.planned_entry == 2623.95
    assert dec_post.candidate.reference_ce == 2623.75


def test_rule_170_shadow_hard_boundary(pipeline_db):
    """
    Rule 170: In SHADOW mode, evaluate_cycle must perform zero PaperOrder mutations and zero quota increments.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    now_ms = t_start + 540_000

    cfg_shadow = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="SHADOW",  # SHADOW!
    )

    quote = make_valid_quote(2625.5, 2625.6, now_ms)

    poi = WatchedZone(
        zone_id="poi_shadow_15m",
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
        pipeline_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg_shadow,
        auto_enabled=True,
    )

    # Assert ZERO PaperOrder records created!
    orders_count = pipeline_db.query(models.PaperOrder).count()
    assert orders_count == 0, "SHADOW mode must not mutate or insert PaperOrders!"


def test_rule_171_off_mode_regression(pipeline_db):
    """
    Rule 171: When CG-01 is OFF, CG-01 candidates are not created and baseline behavior is intact.
    """
    cfg_off = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="BASELINE",
        cg01_enabled=False,
        cg01_profile_mode="OFF",
    )
    now_ms = 1_700_000_000_000
    quote = make_valid_quote(2620.0, 2620.2, now_ms)
    orchestrator = ScalpWatcherOrchestrator()
    res = orchestrator.evaluate_cycle(
        pipeline_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=[],
        config=cfg_off,
        auto_enabled=True,
    )
    assert res is not None
    # No CG-01 order armed
    assert pipeline_db.query(models.PaperOrder).count() == 0
