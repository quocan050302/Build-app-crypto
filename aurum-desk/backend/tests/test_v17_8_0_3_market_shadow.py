"""
backend/tests/test_v17_8_0_3_market_shadow.py
AURUM DESK V17.8.0.3 — Comprehensive Backend Test Suite for:
- Safe MARKET_SHADOW Runtime Profile & Capability Matrix (G59, G60, G61)
- All 30 Mandatory Negative Controls (Section 12.1)
- Safe Public Market Data WS/REST Ingestion & Multi-Timeframe Candle Cache (G62, G63)
- Isolated Shadow Research Storage & Path/Symlink/Hardlink/Traversal Guards (G66)
- Continuous SMC/ICT Unicorn Shadow Analysis, Structural SL/TP & Risk Evaluation (G64, G65)
- Hypothetical Opportunity Lifecycle & Monitoring (Phase F)
- Full Production-Path Integration Test (Section 12.2)
- Read-Only Shadow API Endpoints & HTTP Mutation Blocking (G71)
- Real Historical Backtest Engine, Anti-Lookahead Guards & Artifact Verification (G67, G68, G69, G70)
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import crud
import database
import models
import paper_broker
from main import app
from services.bitget_ws_service import BitgetWSService, bitget_ws_service
from services.candle_cache_service import candle_cache_service
from services.execution_consumer import execution_consumer
from services.execution_coordinator import execution_coordinator
from services.market_shadow_service import (
    MarketShadowService,
    ShadowCandleRecord,
    ShadowFeedState,
    ShadowModelClassification,
    ShadowOpportunityState,
    market_shadow_service,
)
from services.quote_validator import CanonicalQuote
from services.runtime_profile_service import (
    ENV_RUNTIME_PROFILE,
    MARKET_SHADOW_ALLOWED_WORKERS,
    OBSERVE_ONLY_ALLOWED_WORKERS,
    PROFILE_MARKET_SHADOW,
    PROFILE_OBSERVE_ONLY,
    PROFILE_TRADING,
    assert_runtime_capability,
    assert_trading_mutation_allowed,
    assert_worker_allowed_in_current_profile,
    get_runtime_capabilities,
    get_runtime_profile,
    get_runtime_safety_snapshot,
    is_market_shadow_runtime,
    is_non_trading_runtime,
    reset_runtime_safety_counters,
)
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
from services.shadow_research_storage import (
    ENV_SHADOW_DB_PATH,
    EXECUTION_STATUS_NEVER,
    RESEARCH_ONLY_BADGE,
    ShadowResearchStorage,
    resolve_and_validate_shadow_db_path,
)
from services.stop_placement_policy_service import update_stop_placement_policy
from services.stop_placement_types import StopPlacementMode, StopPlacementPolicy
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService
from services.v17_8_backtest_runner import (
     DEFAULT_BACKTEST_OUT_DIR,
    REQUIRED_BACKTEST_FILES,
    BacktestDataInsufficientError,
    BacktestLookaheadViolationError,
    assert_no_future_data_leakage,
    evaluate_and_simulate_variants_on_opportunities,
    get_latest_backtest_summary,
    run_historical_data_quality_gate,
)


def _make_bullish_unicorn_m1_bars(base_open_ms: int, count: int = 35) -> List[Dict[str, Any]]:
    """
    Constructs a deterministic sequence of closed M1 bars with realistic OHLCV geometry,
    swing highs/lows, and a bullish FVG retest for end-to-end shadow evaluation testing.
    """
    bars: List[Dict[str, Any]] = []
    # Bars 0..19: oscillating wave to establish confirmed swing highs and swing lows
    wave_closes = [
        2640.0, 2641.2, 2642.5, 2641.0, 2639.5,
        2638.2, 2639.8, 2641.5, 2643.2, 2644.8,
        2643.1, 2641.4, 2639.9, 2638.5, 2637.2,
        2638.9, 2640.5, 2642.1, 2643.8, 2642.0,
    ]
    for i, c in enumerate(wave_closes):
        ot = base_open_ms + i * 60_000
        o = wave_closes[i - 1] if i > 0 else c - 0.5
        h = round(max(o, c) + 0.8, 2)
        l = round(min(o, c) - 0.8, 2)
        bars.append(
            {
                "open_time": ot,
                "close_time": ot + 60_000,
                "open": round(o, 2),
                "high": h,
                "low": l,
                "close": round(c, 2),
                "volume": 100.0 + i * 5.0,
                "is_closed": True,
            }
        )

    # Create a swing high around bar 22, dip to swing low at bar 25, then strong displacement creating Bullish FVG
    extra = [
        # i=20..22: swing high peak at 2648.5
        (2642.0, 2645.5, 2641.8, 2645.0),
        (2645.0, 2648.5, 2644.6, 2647.8),
        (2647.8, 2649.2, 2646.0, 2646.5),
        # i=23..25: pullback to swing low at 2640.0
        (2646.5, 2646.8, 2643.0, 2643.5),
        (2643.5, 2644.0, 2640.0, 2640.5),
        (2640.5, 2642.0, 2640.2, 2641.8),
        # i=26..28: displacement up creating Bullish FVG between bar 26 high (2642.5) and bar 28 low (2644.2)
        (2641.8, 2642.5, 2641.5, 2642.3),
        (2642.3, 2645.8, 2642.2, 2645.5),
        (2645.5, 2646.9, 2644.2, 2646.2),
        # i=29..34: higher swing target at 2655.0 then gentle retest into FVG [2642.5, 2644.2]
        (2646.2, 2651.0, 2646.0, 2650.5),
        (2650.5, 2655.0, 2650.0, 2654.2),
        (2654.2, 2654.5, 2649.0, 2649.5),
        (2649.5, 2650.0, 2646.0, 2646.5),
        (2646.5, 2647.0, 2643.2, 2643.8),
        (2643.8, 2644.8, 2643.0, 2644.0),
    ]
    for idx, (o, h, l, c) in enumerate(extra, start=len(bars)):
        if idx >= count:
            break
        ot = base_open_ms + idx * 60_000
        bars.append(
            {
                "open_time": ot,
                "close_time": ot + 60_000,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": 150.0 + idx * 3.0,
                "is_closed": True,
            }
        )
    return bars


def _make_m5_bars(base_open_ms: int, count: int = 16) -> List[Dict[str, Any]]:
    bars: List[Dict[str, Any]] = []
    for i in range(count):
        ot = base_open_ms + i * 300_000
        o = 2635.0 + i * 0.6
        c = o + (1.1 if i % 2 == 0 else -0.4)
        bars.append(
            {
                "open_time": ot,
                "close_time": ot + 300_000,
                "open": round(o, 2),
                "high": round(max(o, c) + 1.5, 2),
                "low": round(min(o, c) - 1.2, 2),
                "close": round(c, 2),
                "volume": 600.0 + i * 10.0,
                "is_closed": True,
            }
        )
    return bars


def _make_m15_bars(base_open_ms: int, count: int = 8) -> List[Dict[str, Any]]:
    bars: List[Dict[str, Any]] = []
    for i in range(count):
        ot = base_open_ms + i * 900_000
        o = 2630.0 + i * 1.8
        c = o + 1.5
        bars.append(
            {
                "open_time": ot,
                "close_time": ot + 900_000,
                "open": round(o, 2),
                "high": round(max(o, c) + 2.5, 2),
                "low": round(min(o, c) - 2.0, 2),
                "close": round(c, 2),
                "volume": 1800.0 + i * 25.0,
                "is_closed": True,
            }
        )
    return bars


# ==============================================================================
# 1. RUNTIME PROFILE CONTRACT & 30 MANDATORY NEGATIVE CONTROLS (SECTION 12.1)
# ==============================================================================


class TestMarketShadowRuntimeAndNegativeControls:
    def test_nc27_invalid_runtime_profile_fails_closed(self, monkeypatch):
        """Negative Control #27: Unknown or invalid AURUM_BE_RUNTIME_PROFILE fails closed."""
        monkeypatch.setenv(ENV_RUNTIME_PROFILE, "BOGUS_UNSAFE_PROFILE")
        with pytest.raises(RuntimeError, match="INVALID_.*RUNTIME_PROFILE"):
            get_runtime_profile()

    def test_market_shadow_capability_matrix(self, monkeypatch):
        """G59: MARKET_SHADOW capability matrix enables public data + shadow eval + shadow storage and forbids all trading."""
        monkeypatch.setenv(ENV_RUNTIME_PROFILE, PROFILE_MARKET_SHADOW)
        assert is_market_shadow_runtime() is True
        assert is_non_trading_runtime() is True

        caps = get_runtime_capabilities()
        assert caps.can_read_public_market_data is True
        assert caps.can_evaluate_shadow_strategies is True
        assert caps.can_write_shadow_research is True
        assert caps.can_read_operational_data is True
        assert caps.can_write_operational_data is False
        assert caps.can_arm_paper is False
        assert caps.can_fill_paper is False
        assert caps.can_close_paper is False
        assert caps.can_submit_live_exchange_order is False
        assert caps.can_modify_trading_policy is False

    def test_nc01_to_nc07_execution_and_operational_mutation_guards_in_market_shadow(self, monkeypatch):
        """
        Negative Controls #1..#7:
        1. MARKET_SHADOW attempts to create a PaperOrder -> rejected
        2. MARKET_SHADOW attempts to arm a paper candidate -> rejected
        3. MARKET_SHADOW attempts to fill a paper order -> rejected
        4. MARKET_SHADOW attempts to close a paper order -> rejected
        5. MARKET_SHADOW attempts to send a live order -> rejected
        6. MARKET_SHADOW attempts to write to operational aurum_desk.db -> rejected
        7. MARKET_SHADOW attempts to mutate stop or trading policy -> rejected
        """
        monkeypatch.setenv(ENV_RUNTIME_PROFILE, PROFILE_MARKET_SHADOW)
        reset_runtime_safety_counters()

        db = database.SessionLocal()
        try:
            # #1: crud.create_paper_order
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                crud.create_paper_order(
                    db,
                    MagicMock(
                        setup_id="setup_1",
                        signal_id="sig_1",
                        instrument="XAUUSDT",
                        direction="LONG",
                        state="armed",
                        order_type="LIMIT",
                        timeframe="1M",
                        planned_entry=2650.0,
                        actual_entry=None,
                        stop_loss=2645.0,
                        take_profit=2660.0,
                        quantity=0.1,
                        initial_risk_usdt=2.5,
                        risk_pct=0.25,
                        gross_rr=2.0,
                        estimated_net_rr=1.8,
                        fees_assumption=0.05,
                        slippage_assumption=0.05,
                        strategy_version="v17.8",
                        checklist_snapshot="{}",
                        lessons_retrieved="[]",
                    ),
                    "ord_forbidden_shadow_01",
                )

            # #2: ScalpWatcherOrchestrator._arm_paper_candidate
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                scalp_watcher_orchestrator._arm_paper_candidate(
                    db=db,
                    cand=MagicMock(),
                    now_ms=int(time.time() * 1000),
                    clock=MagicMock(),
                    quote={"bid": 2650.0, "ask": 2650.25},
                    policy_settings={},
                )

            # #3: TradeLifecycleService.execute_fill
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                TradeLifecycleService.execute_fill(
                    db=db,
                    order=MagicMock(id="ord_1"),
                    fill_price=2650.0,
                    calc_result=MagicMock(),
                    now_ms=int(time.time() * 1000),
                )

            # #4: TradeLifecycleService.execute_close & process_exit_tick
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                TradeLifecycleService.execute_close(
                    db=db,
                    order_id="ord_1",
                    exit_price=2660.0,
                    exit_cause="TAKE_PROFIT",
                    now_ms=int(time.time() * 1000),
                )
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                TradeLifecycleService.process_exit_tick(
                    db=db,
                    current_bid=2650.0,
                    current_ask=2650.25,
                )

            # #5: Live exchange order capability guard & PaperBroker.process_price_tick
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                assert_runtime_capability("can_submit_live_exchange_order", operation="submit_live_order")
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                paper_broker.PaperBroker.process_price_tick(db, current_bid=2650.0, current_ask=2650.25)
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                execution_coordinator.evaluate_orders_sync(db, ticker_override={"bid": 2650.0, "ask": 2650.25})
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                execution_consumer.evaluate_quote_sync(
                    db,
                    CanonicalQuote(
                        symbol="XAUUSDT",
                        bid=2650.0,
                        ask=2650.25,
                        last=2650.1,
                        mark_price=2650.1,
                        spread=0.25,
                        exchange_ts_ms=int(time.time() * 1000),
                        received_at_ms=int(time.time() * 1000),
                        freshness_sec=0.1,
                        source="WS",
                        connection_epoch=1,
                        status="VALID",
                        rejection_code=None,
                    ),
                )

            # #6: Direct operational SQLite write blocked by SQLAlchemy before_cursor_execute hook
            from sqlalchemy import text
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                db.execute(
                    text(
                        "INSERT INTO candles (symbol, timeframe, timestamp, open, high, low, close, volume, is_closed) "
                        "VALUES ('XAUUSDT', '1M', 1000, 1.0, 1.0, 1.0, 1.0, 1.0, 1)"
                    )
                )
            db.rollback()

            # #7: update_stop_placement_policy & TradingPolicyService.update_policy
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                update_stop_placement_policy(
                    db,
                    payload={"mode": "PAPER_STRUCTURAL"},
                    expected_version=1,
                )
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                TradingPolicyService.update_policy(
                    db,
                    {"entry_session_policy": "NY_ONLY"},
                )
        finally:
            db.close()

        snap = get_runtime_safety_snapshot()
        assert snap["counters"]["PAPER_ORDERS_CREATED"] == 0
        assert snap["counters"]["PAPER_ORDERS_FILLED"] == 0
        assert snap["counters"]["PAPER_POSITIONS_CLOSED"] == 0
        assert snap["counters"]["LIVE_EXCHANGE_ORDERS_SENT"] == 0
        assert snap["counters"]["OPERATIONAL_DB_WRITES"] == 0

    def test_nc08_to_nc13_worker_allowlist_boundaries(self, monkeypatch):
        """
        Negative Controls #8..#13:
        8. OBSERVE_ONLY attempts to start WebSocket feed -> forbidden
        9. MARKET_SHADOW attempts to start execution_consumer -> forbidden
        10. MARKET_SHADOW attempts to start execution_coordinator -> forbidden
        11. MARKET_SHADOW attempts to start exit_monitor -> forbidden
        12. MARKET_SHADOW attempts to start scalp_watcher_orchestrator -> forbidden
        13. MARKET_SHADOW attempts to start cg01_evaluation_scheduler -> forbidden
        """
        # #8: OBSERVE_ONLY blocks all workers including bitget_ws_service and bitget_ws_shadow_consumer
        monkeypatch.setenv(ENV_RUNTIME_PROFILE, PROFILE_OBSERVE_ONLY)
        assert OBSERVE_ONLY_ALLOWED_WORKERS == ()
        for w in ("bitget_ws_service", "bitget_ws_shadow_consumer", "collector_service"):
            with pytest.raises(RuntimeError, match="OBSERVE_ONLY_WORKER_FORBIDDEN"):
                assert_worker_allowed_in_current_profile(w)

        # #9..#13: MARKET_SHADOW allows only ("bitget_ws_service.run_ws_loop[MARKET_SHADOW]", "market_shadow_service.run_shadow_loop")
        monkeypatch.setenv(ENV_RUNTIME_PROFILE, PROFILE_MARKET_SHADOW)
        assert MARKET_SHADOW_ALLOWED_WORKERS == (
            "bitget_ws_service.run_ws_loop[MARKET_SHADOW]",
            "market_shadow_service.run_shadow_loop",
        )
        for allowed in MARKET_SHADOW_ALLOWED_WORKERS:
            assert_worker_allowed_in_current_profile(allowed)

        forbidden_workers = (
            "execution_consumer",
            "execution_coordinator",
            "exit_monitor",
            "scalp_watcher_orchestrator",
            "cg01_evaluation_scheduler",
            "candle_persistence_worker",
            "collector_service",
            "strategy_service",
            "proximity_service",
            "market_reconciliation_service",
            "notification_outbox_worker",
            "forward_repair_worker",
            "lesson_repair_worker",
            "cg01_projection_worker",
            "cg01_trace_persistence_worker",
        )
        for fw in forbidden_workers:
            with pytest.raises(RuntimeError, match="MARKET_SHADOW_EXECUTION_FORBIDDEN"):
                assert_worker_allowed_in_current_profile(fw)

    def test_nc14_to_nc18_shadow_analysis_fail_closed_controls(self, tmp_path):
        """
        Negative Controls #14..#18:
        14. Stale quote attempts to produce confirmed Unicorn setup -> rejected
        15. Unclosed M1 candle attempts to confirm Breaker or FVG -> rejected
        16. Unclosed M5 candle attempts to authorize setup -> rejected
        17. Missing ATR_M1 attempts to calculate structural SL -> rejected
        18. Missing target attempts to produce eligible shadow setup -> rejected
        """
        svc = MarketShadowService(symbol="XAUUSDT", storage=None)
        now_ms = 1_793_100_000_000
        m1_bars = _make_bullish_unicorn_m1_bars(now_ms - 36 * 60_000, count=35)
        m5_bars = _make_m5_bars(now_ms - 17 * 300_000, count=16)
        m15_bars = _make_m15_bars(now_ms - 9 * 900_000, count=8)

        # #14: Stale quote (exchange_ts_ms is 60 seconds old > 15s threshold)
        stale_quote = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2644.0,
            ask=2644.25,
            last=2644.1,
            mark_price=2644.1,
            spread=0.25,
            exchange_ts_ms=now_ms - 60_000,
            received_at_ms=now_ms - 60_000,
            freshness_sec=60.0,
            source="WS",
            connection_epoch=1,
            status="STALE",
            rejection_code="TICKER_STALE",
        )
        res_stale = svc.evaluate_shadow_cycle(
            as_of_ms=now_ms,
            closed_1m=m1_bars,
            closed_5m=m5_bars,
            closed_15m=m15_bars,
            quote=stale_quote,
        )
        assert res_stale["confirmed_unicorns_count"] == 0
        assert res_stale["eligible_count"] == 0
        for m_out in res_stale["models_evaluated"].values():
            if m_out["classification"] != "MODEL_DISABLED":
                assert m_out["reason_code"] == "STALE_OR_INVALID_QUOTE"

        # Fresh quote for remaining checks
        fresh_quote = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2644.0,
            ask=2644.25,
            last=2644.1,
            mark_price=2644.1,
            spread=0.25,
            exchange_ts_ms=now_ms - 500,
            received_at_ms=now_ms - 400,
            freshness_sec=0.5,
            source="WS",
            connection_epoch=1,
            status="VALID",
            rejection_code=None,
        )

        # #15: Unclosed M1 candles (`is_closed=False`) are stripped and never confirm Breaker/FVG
        unclosed_m1 = [dict(b, is_closed=False) for b in m1_bars]
        res_unclosed_m1 = svc.evaluate_shadow_cycle(
            as_of_ms=now_ms,
            closed_1m=unclosed_m1,
            closed_5m=m5_bars,
            closed_15m=m15_bars,
            quote=fresh_quote,
        )
        assert res_unclosed_m1["confirmed_unicorns_count"] == 0
        assert res_unclosed_m1["eligible_count"] == 0

        # #16: Unclosed M5 candles (`is_closed=False`) rejected from authorizing setup
        unclosed_m5 = [dict(b, is_closed=False) for b in m5_bars]
        res_unclosed_m5 = svc.evaluate_shadow_cycle(
            as_of_ms=now_ms,
            closed_1m=m1_bars,
            closed_5m=unclosed_m5,
            closed_15m=m15_bars,
            quote=fresh_quote,
        )
        assert res_unclosed_m5["m5_status"] == "UNCLOSED_REJECTED"
        assert res_unclosed_m5["eligible_count"] == 0
        for m_out in res_unclosed_m5["models_evaluated"].values():
            if m_out["classification"] != "MODEL_DISABLED":
                assert m_out["reason_code"] == "UNCLOSED_M5_CANDLE_REJECTED"

        # #17: Missing ATR_M1 (e.g. M1 bars have a cadence gap breaking ATR continuity)
        gapped_m1 = list(m1_bars)
        gapped_m1[-3] = dict(gapped_m1[-3], open_time=gapped_m1[-3]["open_time"] - 180_000)
        res_no_atr = svc.evaluate_shadow_cycle(
            as_of_ms=now_ms,
            closed_1m=gapped_m1,
            closed_5m=m5_bars,
            closed_15m=m15_bars,
            quote=fresh_quote,
        )
        assert res_no_atr["eligible_count"] == 0
        for opp in res_no_atr["opportunities"]:
            assert "ATR_MISSING" in opp["rejection_reason_codes"]
            assert opp["structural_sl"] is None

        # #18: Missing target (when resolve_executable_target returns NO_VALID_TARGET)
        no_target_mock = MagicMock(
            status="NO_VALID_TARGET",
            price=None,
            target_type=None,
            source_level_id=None,
            reason="NO_LIQUIDITY_TARGET",
        )
        with patch("services.market_shadow_service.resolve_executable_target", return_value=no_target_mock):
            res_no_target = svc.evaluate_shadow_cycle(
                as_of_ms=now_ms,
                closed_1m=m1_bars,
                closed_5m=m5_bars,
                closed_15m=m15_bars,
                quote=fresh_quote,
            )
        assert res_no_target["eligible_count"] == 0
        for opp in res_no_target["opportunities"]:
            assert "SHADOW_REJECTED_NO_VALID_TARGET" in opp["rejection_reason_codes"]
            assert opp["shadow_eligibility_status"] == "SHADOW_REJECTED"

    def test_nc19_to_nc21_shadow_storage_isolation_and_path_guards(self, tmp_path, monkeypatch):
        """
        Negative Controls #19..#21:
        19. Failed research persistence queue is reported, not hidden.
        20. AURUM_SHADOW_DB_PATH pointing to aurum_desk.db fails closed.
        21. AURUM_SHADOW_DB_PATH using relative traversal ('..'), symlink, or hardlink alias to operational DB fails closed.
        """
        # #20: Operational DB path rejected
        with pytest.raises(RuntimeError, match="SHADOW_DB_OPERATIONAL_CONFLICT"):
            resolve_and_validate_shadow_db_path(str(database.RUNTIME_DEFAULT_DB_PATH))
        with pytest.raises(RuntimeError, match="SHADOW_DB_OPERATIONAL_CONFLICT"):
            resolve_and_validate_shadow_db_path(str(tmp_path / "aurum_desk.db"))

        # #21a: Relative traversal '..' rejected
        with pytest.raises(RuntimeError, match="SHADOW_DB_PATH_TRAVERSAL_REJECTED"):
            resolve_and_validate_shadow_db_path("shadow_data/../aurum_desk.db")

        # #21b: Symlink target rejected
        real_shadow = tmp_path / "sub" / "real_shadow.db"
        real_shadow.parent.mkdir(parents=True, exist_ok=True)
        real_shadow.write_bytes(b"")
        symlink_shadow = tmp_path / "sub" / "symlink_shadow.db"
        symlink_shadow.symlink_to(real_shadow)
        with pytest.raises(RuntimeError, match="SHADOW_DB_SYMLINK_REJECTED"):
            resolve_and_validate_shadow_db_path(str(symlink_shadow))

        # #21c: Hardlink target rejected
        hardlink_shadow = tmp_path / "sub" / "hardlink_shadow.db"
        os.link(real_shadow, hardlink_shadow)
        with pytest.raises(RuntimeError, match="SHADOW_DB_HARDLINK_REJECTED"):
            resolve_and_validate_shadow_db_path(str(hardlink_shadow))

        # #21d: Existing SQLite file containing operational table `paper_orders` rejected
        contaminated_db = tmp_path / "sub" / "contaminated.db"
        conn = sqlite3.connect(str(contaminated_db))
        conn.execute("CREATE TABLE paper_orders (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="SHADOW_DB_OWNERSHIP_VIOLATION"):
            resolve_and_validate_shadow_db_path(str(contaminated_db))

        # #19: Failed research persistence queue & queue overflow are reported in health snapshot
        valid_shadow_db = tmp_path / "shadow_isolated" / "market_shadow.db"
        storage = ShadowResearchStorage(db_path=str(valid_shadow_db), max_queue_size=10, max_retries=2)
        storage.initialize()
        assert storage.get_health_snapshot()["status"] == "HEALTHY"

        storage.set_simulate_failure(True)
        ok = storage.record_quote(
            {
                "quote_id": "q_fail_1",
                "symbol": "XAUUSDT",
                "bid": 2650.0,
                "ask": 2650.25,
                "exchange_time_ms": 1793100000000,
                "receive_time_ms": 1793100000010,
            }
        )
        assert ok is False
        health = storage.get_health_snapshot()
        assert health["status"] == "DEGRADED"
        assert health["has_persistence_gap"] is True
        assert health["writes_failed"] >= 1
        assert health["stream_complete"] is False
        storage.dispose()

    def test_nc22_nc28_nc29_quote_and_rest_fallback_controls(self):
        """
        Negative Controls #22, #28, #29:
        22. REST ticker fallback attempts to synthesize OHLCV candles -> rejected
        28. Quote without executable Bid or Ask cannot silently substitute Last price
        29. Late or duplicate quotes are counted and rejected from current decision state
        """
        svc = MarketShadowService(symbol="XAUUSDT", storage=None)
        now_ms = int(time.time() * 1000)

        # #22: Synthesizing candle from ticker is strictly forbidden
        with pytest.raises(RuntimeError, match="REST_FALLBACK_CANDLE_SYNTHESIS_FORBIDDEN"):
            svc.synthesize_candle_from_ticker()
        with pytest.raises(RuntimeError, match="REST_FALLBACK_CANDLE_SYNTHESIS_FORBIDDEN"):
            svc.ingest_candle_payload(
                symbol="XAUUSDT",
                timeframe="1M",
                data_rows=[{"open_time": now_ms, "open": 2650, "high": 2651, "low": 2649, "close": 2650}],
                source="REST_TICKER_SYNTHETIC",
            )

        # #28: Quote with Last only (missing Bid or Ask) is rejected
        ok_no_bid, reason_no_bid, _ = svc.ingest_raw_quote(
            {"symbol": "XAUUSDT", "last": 2650.0, "ask": 2650.2, "exchange_ts_ms": now_ms},
            now_ms=now_ms,
        )
        assert ok_no_bid is False
        assert reason_no_bid == "MISSING_EXECUTABLE_BID_OR_ASK"

        ok_no_ask, reason_no_ask, _ = svc.ingest_raw_quote(
            {"symbol": "XAUUSDT", "last": 2650.0, "bid": 2649.8, "exchange_ts_ms": now_ms},
            now_ms=now_ms,
        )
        assert ok_no_ask is False
        assert reason_no_ask == "MISSING_EXECUTABLE_BID_OR_ASK"

        # #29: Accept first valid quote, then reject duplicate and late out-of-order quotes
        ok1, r1, _ = svc.ingest_raw_quote(
            {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.25, "exchange_ts_ms": now_ms - 1000},
            connection_epoch=2,
            now_ms=now_ms,
        )
        assert ok1 is True and r1 == "ACCEPTED"

        ok_dup, r_dup, _ = svc.ingest_raw_quote(
            {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.25, "exchange_ts_ms": now_ms - 1000},
            connection_epoch=2,
            now_ms=now_ms,
        )
        assert ok_dup is False and r_dup == "DUPLICATE_QUOTE"
        assert svc.quotes_duplicate == 1

        ok_late, r_late, _ = svc.ingest_raw_quote(
            {"symbol": "XAUUSDT", "bid": 2649.5, "ask": 2649.75, "exchange_ts_ms": now_ms - 2000},
            connection_epoch=2,
            now_ms=now_ms,
        )
        assert ok_late is False and r_late == "LATE_OUT_OF_ORDER_QUOTE"
        assert svc.quotes_late == 1
        assert svc.latest_quote.bid == 2650.0

    def test_nc23_nc24_nc30_backtest_lookahead_missing_data_and_calibration_controls(self):
        """
        Negative Controls #23, #24, #30:
        23. Future candle data injected into historical replay fails immediately.
        24. Missing historical candle window fails or marks REAL_HISTORICAL_BACKTEST_BLOCKED.
        30. Uncalibrated Variant D cannot report as CALIBRATED_EMPIRICAL_P90.
        """
        # #23: Future candle injection raises BacktestLookaheadViolationError
        with pytest.raises(BacktestLookaheadViolationError, match="FUTURE_CANDLE_INJECTION_DETECTED"):
            assert_no_future_data_leakage(
                [{"open_time": 1000, "close_time": 61_000, "is_closed": True}],
                decision_ms=60_000,
                cadence_ms=60_000,
            )

        # #23b: Unclosed candle injection raises BacktestLookaheadViolationError
        with pytest.raises(BacktestLookaheadViolationError, match="UNCLOSED_CANDLE_INJECTION_DETECTED"):
            assert_no_future_data_leakage(
                [{"open_time": 0, "close_time": 60_000, "is_closed": False}],
                decision_ms=60_000,
                cadence_ms=60_000,
            )

        # #24: Missing historical M1/M5/M15 candles raises BacktestDataInsufficientError
        with pytest.raises(BacktestDataInsufficientError, match="REAL_HISTORICAL_BACKTEST_BLOCKED"):
            run_historical_data_quality_gate(
                {"1m": [], "5m": [], "15m": [], "1H": [], "4H": [], "1D": []},
                requested_start_ms=1000,
                requested_end_ms=2000,
            )

        # #30: Variant D with < 10 prior samples reports EXPERIMENTAL_FIXED_MULTIPLIER, never EMPIRICAL_P90_QUANTILE
        fake_opp = {
            "opportunity_id": "opp_test_d",
            "symbol": "XAUUSDT",
            "model_id": "SMC_M1_FVG_CONTINUATION",
            "setup_category": "UNICORN_QUALIFIED",
            "unicorn_valid": True,
            "direction": "LONG",
            "session": "NY_SESSION",
            "decision_ms": 1_793_100_000_000,
            "entry_price": 2650.0,
            "spread": 0.25,
            "atr_m1": 1.0,
            "atr_m5": 2.0,
            "legacy_sl": 2648.0,
            "structural_anchor_price": 2647.5,
            "unicorn_invalidation_price": 2647.5,
            "take_profit": 2662.0,
            "forward_bars": [
                {"open_time": 1_793_100_060_000, "close_time": 1_793_100_120_000, "open": 2650.0, "high": 2665.0, "low": 2649.5, "close": 2663.0}
            ],
            "noise_sample": {"timestamp_ms": 1_793_100_000_000, "adverse_excursion_usd": 0.5, "regime": "NORMAL"},
        }
        eval_res = evaluate_and_simulate_variants_on_opportunities(
            [fake_opp],
            prior_noise_samples_pool=[],  # 0 prior samples
        )
        assert eval_res["variant_d_calibration_counts"]["EMPIRICAL_P90_QUANTILE"] == 0
        assert eval_res["variant_d_calibration_counts"]["EXPERIMENTAL_FIXED_MULTIPLIER"] == 1
        assert eval_res["variants"]["D"]["calibration_status"] == "EXPERIMENTAL_FIXED_MULTIPLIER"

    def test_nc25_nc26_ws_dispatch_and_candle_cache_isolation_in_market_shadow(self, monkeypatch):
        """
        Negative Controls #25 & #26:
        25. ExecutionConsumer.enqueue_quote() is not called when MARKET_SHADOW processes live quotes.
        26. CandleCacheService.process_candle_payload() does not notify CG01 or operational persistence in MARKET_SHADOW.
        """
        monkeypatch.setenv(ENV_RUNTIME_PROFILE, PROFILE_MARKET_SHADOW)
        market_shadow_service.reset_for_test()
        bitget_ws_service.register_shadow_sink(market_shadow_service)

        now_ms = int(time.time() * 1000)
        ws_ticker_msg = {
            "action": "snapshot",
            "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "XAUUSDT"},
            "data": [
                {
                    "instId": "XAUUSDT",
                    "bidPr": "2650.10",
                    "askPr": "2650.35",
                    "lastPr": "2650.20",
                    "markPrice": "2650.22",
                    "ts": str(now_ms - 200),
                }
            ],
        }
        ws_candle_msg = {
            "action": "snapshot",
            "arg": {"instType": "USDT-FUTURES", "channel": "candle1m", "instId": "XAUUSDT"},
            "data": [
                [str(now_ms - 120_000), "2649.0", "2651.0", "2648.5", "2650.2", "145.5"],
                [str(now_ms - 60_000), "2650.2", "2650.8", "2649.9", "2650.4", "80.0"],
            ],
        }

        with (
            patch.object(execution_consumer, "enqueue_quote") as mock_enqueue,
            patch.object(candle_cache_service, "process_quote") as mock_cc_quote,
            patch.object(candle_cache_service, "process_candle_payload") as mock_cc_candle,
        ):
            res_q = asyncio.run(bitget_ws_service.process_ws_message(ws_ticker_msg, connection_epoch=3))
            res_c = asyncio.run(bitget_ws_service.process_ws_message(ws_candle_msg, connection_epoch=3))

            assert res_q["route"] == "MARKET_SHADOW"
            assert res_c["route"] == "MARKET_SHADOW"
            mock_enqueue.assert_not_called()
            mock_cc_quote.assert_not_called()
            mock_cc_candle.assert_not_called()

        assert market_shadow_service.quotes_accepted == 1
        assert market_shadow_service.candles_closed >= 1


# ==============================================================================
# 2. FULL PRODUCTION-PATH INTEGRATION TEST (SECTION 12.2)
# ==============================================================================


class TestMarketShadowFullProductionPath:
    def test_end_to_end_ws_to_shadow_storage_and_shadow_api(self, tmp_path, monkeypatch):
        """
        Section 12.2 Mandatory End-to-End Integration Test:
        REAL PUBLIC WS MESSAGE
          -> CANONICAL QUOTE
          -> MARKET_SHADOW ROUTER
          -> SHADOW CANDLE CACHE
          -> CONFIRMED CANDLE
          -> SMC/UNICORN EVALUATION
          -> STRUCTURAL SL/TP
          -> RISK CALCULATION
          -> SHADOW RESEARCH SNAPSHOT
          -> SHADOW API
        Verifies zero operational DB writes and zero paper/live orders.
        """
        shadow_db_file = tmp_path / "shadow_e2e" / "market_shadow.db"
        monkeypatch.setenv(ENV_RUNTIME_PROFILE, PROFILE_MARKET_SHADOW)
        monkeypatch.setenv(ENV_SHADOW_DB_PATH, str(shadow_db_file))
        reset_runtime_safety_counters()

        storage = ShadowResearchStorage(db_path=str(shadow_db_file))
        storage.initialize()
        market_shadow_service.reset_for_test()
        market_shadow_service.bind_storage(storage)
        bitget_ws_service.register_shadow_sink(market_shadow_service)

        try:
            now_ms = int(time.time() * 1000)
            m1_bars = _make_bullish_unicorn_m1_bars(now_ms - 36 * 60_000, count=35)
            m5_bars = _make_m5_bars(now_ms - 17 * 300_000, count=16)
            m15_bars = _make_m15_bars(now_ms - 9 * 900_000, count=8)

            # 1. Dispatch real Bitget WS candle messages through BitgetWSService.process_ws_message
            ws_m1 = {
                "action": "snapshot",
                "arg": {"instType": "USDT-FUTURES", "channel": "candle1m", "instId": "XAUUSDT"},
                "data": [
                    [str(b["open_time"]), str(b["open"]), str(b["high"]), str(b["low"]), str(b["close"]), str(b["volume"])]
                    for b in m1_bars
                ],
            }
            ws_m5 = {
                "action": "snapshot",
                "arg": {"instType": "USDT-FUTURES", "channel": "candle5m", "instId": "XAUUSDT"},
                "data": [
                    [str(b["open_time"]), str(b["open"]), str(b["high"]), str(b["low"]), str(b["close"]), str(b["volume"])]
                    for b in m5_bars
                ],
            }
            ws_m15 = {
                "action": "snapshot",
                "arg": {"instType": "USDT-FUTURES", "channel": "candle15m", "instId": "XAUUSDT"},
                "data": [
                    [str(b["open_time"]), str(b["open"]), str(b["high"]), str(b["low"]), str(b["close"]), str(b["volume"])]
                    for b in m15_bars
                ],
            }
            ws_ticker = {
                "action": "snapshot",
                "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "XAUUSDT"},
                "data": [
                    {
                        "instId": "XAUUSDT",
                        "bidPr": "2644.00",
                        "askPr": "2644.25",
                        "lastPr": "2644.10",
                        "markPrice": "2644.12",
                        "ts": str(now_ms - 300),
                    }
                ],
            }

            asyncio.run(bitget_ws_service.process_ws_message(ws_m1, connection_epoch=5))
            asyncio.run(bitget_ws_service.process_ws_message(ws_m5, connection_epoch=5))
            asyncio.run(bitget_ws_service.process_ws_message(ws_m15, connection_epoch=5))
            asyncio.run(bitget_ws_service.process_ws_message(ws_ticker, connection_epoch=5))

            # 2. Run continuous shadow evaluation cycle
            cycle = market_shadow_service.evaluate_shadow_cycle(as_of_ms=now_ms)
            assert cycle["feed_status"] == ShadowFeedState.CONNECTED_FRESH.value
            assert cycle["m1_status"] == "READY"
            assert cycle["m5_status"] == "READY"
            assert cycle["m15_status"] == "READY"
            assert cycle["detected_setups_count"] >= 1
            assert len(cycle["opportunities"]) >= 1

            opp0 = cycle["opportunities"][0]
            assert opp0["execution_status"] == EXECUTION_STATUS_NEVER
            assert opp0["research_disclosure"] == RESEARCH_ONLY_BADGE
            assert opp0["structural_sl"] is not None

            # 3. Verify hypothetical opportunity monitoring on subsequent quotes (never fills)
            touch_quote = CanonicalQuote(
                symbol="XAUUSDT",
                bid=2643.80,
                ask=2644.00,
                last=2643.90,
                mark_price=2643.90,
                spread=0.20,
                exchange_ts_ms=now_ms + 500,
                received_at_ms=now_ms + 550,
                freshness_sec=0.05,
                source="WS",
                connection_epoch=5,
                status="VALID",
                rejection_code=None,
            )
            market_shadow_service.ingest_quote(touch_quote, now_ms=now_ms + 600)
            assert opp0["execution_status"] == EXECUTION_STATUS_NEVER

            # 4. Query all read-only Shadow API endpoints via FastAPI TestClient
            client = TestClient(app)

            r_prof = client.get("/api/v1/runtime/profile")
            assert r_prof.status_code == 200
            prof_json = r_prof.json()
            assert prof_json["profile"] == PROFILE_MARKET_SHADOW
            assert prof_json["counters"]["PAPER_ORDERS_CREATED"] == 0
            assert prof_json["counters"]["PAPER_ORDERS_FILLED"] == 0
            assert prof_json["counters"]["PAPER_POSITIONS_CLOSED"] == 0
            assert prof_json["counters"]["LIVE_EXCHANGE_ORDERS_SENT"] == 0
            assert prof_json["counters"]["OPERATIONAL_DB_WRITES"] == 0
            assert prof_json["counters"]["SHADOW_DB_WRITES"] > 0

            r_status = client.get("/api/v1/shadow/market/status")
            assert r_status.status_code == 200
            st_json = r_status.json()
            assert st_json["feed_status"] == "CONNECTED_FRESH"
            assert st_json["research_disclosure"] == RESEARCH_ONLY_BADGE

            r_quotes = client.get("/api/v1/shadow/market/quotes?limit=10")
            assert r_quotes.status_code == 200
            assert r_quotes.json()["count"] >= 1

            r_candles = client.get("/api/v1/shadow/market/candles?timeframe=1M&limit=20")
            assert r_candles.status_code == 200
            assert len(r_candles.json()["closed_candles"]) >= 20

            r_opps = client.get("/api/v1/shadow/opportunities")
            assert r_opps.status_code == 200
            opps_list = r_opps.json()["opportunities"]
            assert len(opps_list) >= 1

            r_opp_detail = client.get(f"/api/v1/shadow/opportunities/{opp0['opportunity_id']}")
            assert r_opp_detail.status_code == 200
            assert r_opp_detail.json()["execution_status"] == "NEVER_EXECUTED"

            r_unicorn = client.get("/api/v1/shadow/unicorn/evidence")
            assert r_unicorn.status_code == 200
            assert "unicorn_evidence" in r_unicorn.json()

            r_bt = client.get("/api/v1/shadow/research/backtests")
            assert r_bt.status_code == 200
            assert "backtests" in r_bt.json()

            # 5. Verify HTTP mutating endpoints return 403 MARKET_SHADOW_EXECUTION_FORBIDDEN
            for method, path in (
                ("POST", "/api/v1/orders/paper"),
                ("POST", "/api/v1/orders/close/ord_1"),
                ("PUT", "/api/v1/policy/stop-placement"),
                ("POST", "/api/v1/policy/trading"),
                ("POST", "/api/v1/reports/generate"),
                ("GET", "/api/v1/reports/generate"),
            ):
                resp = client.request(method, path, json={})
                assert resp.status_code == 403
                assert resp.json()["detail"]["code"] == "MARKET_SHADOW_EXECUTION_FORBIDDEN"
        finally:
            market_shadow_service.bind_storage(None)
            storage.dispose()


# ==============================================================================
# 3. REAL HISTORICAL BACKTEST ARTIFACTS & DATA QUALITY VERIFICATION (G67..G70)
# ==============================================================================


class TestRealHistoricalBacktestArtifacts:
    def test_all_12_backtest_artifacts_exist_and_contain_honest_metrics(self):
        """G67..G70: Verifies all required backtest files exist in `backtest/` and are non-fabricated."""
        for fname in REQUIRED_BACKTEST_FILES:
            fpath = DEFAULT_BACKTEST_OUT_DIR / fname
            assert fpath.exists(), f"Missing required backtest file: {fpath}"
            assert fpath.stat().st_size > 0, f"Empty backtest file: {fpath}"

        manifest = json.loads((DEFAULT_BACKTEST_OUT_DIR / "dataset_manifest.json").read_text(encoding="utf-8"))
        assert manifest["symbol"] == "XAUUSDT"
        assert manifest["m1_bars_count"] >= 10_000
        assert manifest["m5_bars_count"] >= 20_000
        assert manifest["m15_bars_count"] >= 10_000
        assert len(manifest["dataset_sha256"]) == 64

        comp = json.loads((DEFAULT_BACKTEST_OUT_DIR / "matched_comparison.json").read_text(encoding="utf-8"))
        variants = comp["all_valid_setups_matched_comparison"]
        for vid in ("A", "B", "C", "D"):
            v = variants[vid]
            assert v["total_opportunities"] == variants["A"]["total_opportunities"]
            # Honest 30s stopout reporting when only M1 OHLC is available
            assert v["early_stopout_30s"] == "UNAVAILABLE_FROM_M1_OHLC"
            assert isinstance(v["early_stopout_60s"], int)
            assert isinstance(v["early_stopout_180s"], int)

        oos = json.loads((DEFAULT_BACKTEST_OUT_DIR / "out_of_sample_report.json").read_text(encoding="utf-8"))
        assert oos["split_protocol"].startswith("CHRONOLOGICAL_60_20_20")
        assert "train_60_pct" in oos or "in_sample_60_pct" in oos
        assert "validation_20_pct" in oos
        assert "out_of_sample_20_pct" in oos

        sens = json.loads((DEFAULT_BACKTEST_OUT_DIR / "sensitivity_report.json").read_text(encoding="utf-8"))
        scenarios = sens["scenarios"]
        for req_s in (
            "normal_spread",
            "increased_spread",
            "increased_slippage",
            "higher_volatility",
            "quote_gaps",
            "delayed_entry",
            "missing_candles",
            "stale_feed",
            "sessions",
        ):
            assert req_s in scenarios

        summary = get_latest_backtest_summary()
        assert summary["available"] is True
        assert summary["backtest_verdict"] == "REAL_HISTORICAL_BACKTEST_COMPLETED"
