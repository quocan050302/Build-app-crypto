"""
backend/tests/test_v15_8_execution_quote_validation.py
V15.8 - Execution Quote Validation and Provenance Test Suite.

Covers:
- Rejection of 999s stale quotes via normalize_execution_quote().
- Rejection of unprovenanced quotes (missing timestamps) with TIME_UNAVAILABLE.
- Acceptance of fresh quotes with accurate timestamp provenance.
- Aging of ValidatedQuote and CanonicalQuote (cannot be re-used after aging past max_age_sec).
- Spoofed freshness rejection: quotes claiming freshness_sec=0.0 on old timestamps are recalculated and rejected.
- Non-mutation of input quote dictionaries.
- Strict boundary checks (14.9s valid, 15.0s valid, 15.1s stale).
- Future timestamp rejection (>5s into future).
- Non-finite price rejection (NaN, Inf in bid, ask, last).
- Inverted spread rejection (ask < bid).
- Zero and negative price rejection.
- Timestamp alias recognition (server_time_ms, exchange_ts_ms, received_at_ms).
- Unsupported input types (None, list, string, int) safely rejected.
- Direct invocation of _arm_paper_candidate() with 999s stale dict returns None without mutating WatchSetup or ScenarioSnapshot.
- Direct invocation of _arm_paper_candidate() with missing timestamp returns None.
- Direct invocation of _arm_paper_candidate() with fresh quote creates armed PaperOrder.
- evaluate_cycle() with causal LONG candles blocks entry when quote is stale.
- evaluate_cycle() with causal SHORT candles blocks entry when quote is stale.
- evaluate_cycle() with causal LONG candles arms order when quote is fresh.
- evaluate_cycle() with causal SHORT candles arms order when quote is fresh.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import models
from services.clock import FakeClock
from services.liquidity_scenario_service import (
    HypothesisWriteOutcome,
    LifecycleCycleAuthority,
    LifecycleStageResult,
)
from services.quote_validator import (
    CanonicalQuote,
    QuoteValidator,
    ValidatedQuote,
    normalize_execution_quote,
)
from services.risk_settings_service import risk_settings_service
from services.scalp_m1_model_service import ScalpCandidate
from services.scalp_m1_types import (
    M1ScalpConfig,
    ScalpMode,
    WatchedZone,
    ZoneState,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.trading_policy_service import TradingPolicyService
from tests.test_v15_1_execution_lifecycle import (
    _make_causal_long_m1_candles,
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def isolated_db() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def base_cfg() -> M1ScalpConfig:
    return M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )


def _make_dummy_scalp_candidate(zone_id: str = "z_quote_test") -> ScalpCandidate:
    calc = type("CalcStub", (), {
        "is_valid": True,
        "can_execute": True,
        "meets_min_rr": True,
        "quantity": 0.1,
        "gross_rr": 3.0,
        "net_rr": 2.8,
        "net_reward_usdt": 50.0,
        "net_risk_usdt": 2.5,
        "leverage": 30.0,
        "margin_mode": "ISOLATED",
        "effective_risk_pct": 0.25,
        "estimated_liquidation": 2550.0,
        "est_fill_fee_usdt": 0.1,
        "est_close_fee_usdt": 0.1,
        "est_slippage_usdt": 0.1,
        "total_friction_usdt": 0.3,
        "cash_buffer_usdt": 10.0,
        "margin_used_usdt": 10.0,
    })()
    return ScalpCandidate(
        is_eligible=True,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        candidate_key="cand_direct_quote_test",
        symbol="XAUUSDT",
        planned_entry=2620.0,
        stop_loss=2610.0,
        take_profit=2650.0,
        net_rr=2.8,
        gross_rr=3.0,
        calculation=calc,
        zone_id=zone_id,
    )


def _make_positive_authority(zone_id: str = "z_quote_test") -> LifecycleCycleAuthority:
    auth = LifecycleCycleAuthority()
    auth.zone_outcomes[zone_id] = HypothesisWriteOutcome(
        hypothesis_id=f"hyp_{zone_id}",
        storage_key=f"hlc:XAUUSDT:{zone_id}",
        zone_id=zone_id,
        operation="UPSERT_HYPOTHESIS",
        result=LifecycleStageResult.CREATED,
        is_success=True,
    )
    return auth


# =====================================================================
# UNIT TESTS: normalize_execution_quote()
# =====================================================================

def test_v15_8_normalize_quote_999s_stale_rejected():
    """
    SECTION 017, 047:
    Exact 999s stale quote rejection.
    now_ms = 1_000_000, observed_at = 1_000, server_time = 1_000.
    """
    now_ms = 1_000_000
    stale_quote = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "is_valid": True,
        "server_time": 1_000,
        "observed_at": 1_000,
    }
    vq = normalize_execution_quote(stale_quote, now_ms=now_ms)
    assert vq.is_valid is False
    assert vq.is_stale is True
    assert vq.rejection_code == "TICKER_STALE"
    assert vq.freshness_sec >= 999.0


def test_v15_8_normalize_quote_missing_timestamp_rejected():
    """
    SECTION 047, 127:
    Quote missing both server_time and observed_at is rejected with TIME_UNAVAILABLE.
    Does NOT fabricate now_ms as exchange timestamp.
    """
    now_ms = 1_000_000
    missing_ts_quote = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
    }
    vq = normalize_execution_quote(missing_ts_quote, now_ms=now_ms)
    assert vq.is_valid is False
    assert vq.rejection_code == "TIME_UNAVAILABLE"


def test_v15_8_normalize_quote_fresh_accepted():
    """
    SECTION 047, 127:
    Fresh quote with consistent timestamps is accepted with is_valid=True, is_stale=False.
    """
    now_ms = 1_000_000
    fresh_quote = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    vq = normalize_execution_quote(fresh_quote, now_ms=now_ms)
    assert vq.is_valid is True
    assert vq.is_stale is False
    assert vq.rejection_code is None
    assert vq.freshness_sec <= 0.1


def test_v15_8_normalize_quote_validated_quote_aging():
    """
    SECTION 087:
    ValidatedQuote created fresh at t0 becomes stale when evaluated at t0 + 20s.
    """
    t0 = 1_000_000
    fresh_dict = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "server_time": t0,
        "observed_at": t0,
    }
    vq0 = QuoteValidator.validate_ticker(fresh_dict, now_ms=t0)
    assert vq0.is_valid is True
    assert vq0.is_stale is False

    # 20 seconds later
    t1 = t0 + 20_000
    vq1 = normalize_execution_quote(vq0, now_ms=t1)
    assert vq1.is_valid is False
    assert vq1.is_stale is True
    assert vq1.rejection_code == "TICKER_STALE"
    assert vq1.freshness_sec == 20.0


def test_v15_8_normalize_quote_canonical_quote_aging():
    """
    SECTION 107:
    CanonicalQuote created fresh at t0 becomes stale when evaluated at t0 + 20s.
    """
    t0 = 1_000_000
    cq0 = CanonicalQuote(
        symbol="XAUUSDT",
        last=2620.25,
        bid=2620.0,
        ask=2620.5,
        mark_price=2620.25,
        exchange_ts_ms=t0,
        received_at_ms=t0,
        source="binance",
        connection_epoch=1,
        status="VALID",
        spread=0.5,
        freshness_sec=0.0,
    )
    assert cq0.is_valid is True

    # 20 seconds later
    t1 = t0 + 20_000
    vq1 = normalize_execution_quote(cq0, now_ms=t1)
    assert vq1.is_valid is False
    assert vq1.is_stale is True
    assert vq1.rejection_code == "TICKER_STALE"
    assert vq1.freshness_sec == 20.0


def test_v15_8_normalize_quote_spoofed_freshness_rejected():
    """
    SECTION 067:
    Caller attempts to spoof freshness by passing freshness_sec=0.0 and is_valid=True
    on a dictionary with an old timestamp. normalize_execution_quote must recalculate
    freshness against now_ms and reject it.
    """
    now_ms = 1_000_000
    spoofed = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "is_valid": True,
        "is_stale": False,
        "freshness_sec": 0.0,
        "server_time": 1_000,
        "observed_at": 1_000,
    }
    vq = normalize_execution_quote(spoofed, now_ms=now_ms)
    assert vq.is_valid is False
    assert vq.is_stale is True
    assert vq.rejection_code == "TICKER_STALE"


def test_v15_8_normalize_quote_does_not_mutate_input_dict():
    """
    SECTION 207:
    normalize_execution_quote() does not mutate the input dictionary.
    """
    now_ms = 1_000_000
    input_dict = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "server_time": now_ms,
        "observed_at": now_ms,
        "custom_marker": "do_not_alter",
    }
    original_copy = dict(input_dict)
    _ = normalize_execution_quote(input_dict, now_ms=now_ms)
    assert input_dict == original_copy


def test_v15_8_normalize_quote_boundary_conditions():
    """
    SECTION 147:
    Threshold boundary tests (threshold = 15.0s):
    - 14.9s: valid
    - 15.0s: valid (not > 15.0)
    - 15.1s: stale (rejection_code='TICKER_STALE')
    """
    now_ms = 1_000_000

    # 14.9s
    ts_14_9 = now_ms - 14_900
    q_14_9 = {"bid": 2620.0, "ask": 2620.5, "last": 2620.25, "server_time": ts_14_9, "observed_at": ts_14_9}
    vq_14_9 = normalize_execution_quote(q_14_9, now_ms=now_ms)
    assert vq_14_9.is_valid is True
    assert vq_14_9.is_stale is False

    # 15.0s
    ts_15_0 = now_ms - 15_000
    q_15_0 = {"bid": 2620.0, "ask": 2620.5, "last": 2620.25, "server_time": ts_15_0, "observed_at": ts_15_0}
    vq_15_0 = normalize_execution_quote(q_15_0, now_ms=now_ms)
    assert vq_15_0.is_valid is True
    assert vq_15_0.is_stale is False

    # 15.1s
    ts_15_1 = now_ms - 15_100
    q_15_1 = {"bid": 2620.0, "ask": 2620.5, "last": 2620.25, "server_time": ts_15_1, "observed_at": ts_15_1}
    vq_15_1 = normalize_execution_quote(q_15_1, now_ms=now_ms)
    assert vq_15_1.is_valid is False
    assert vq_15_1.is_stale is True
    assert vq_15_1.rejection_code == "TICKER_STALE"


def test_v15_8_normalize_quote_future_timestamp_rejected():
    """
    SECTION 167:
    Timestamp >5s into the future is rejected with FUTURE_TIMESTAMP.
    """
    now_ms = 1_000_000
    future_quote = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "server_time": now_ms + 10_000,
        "observed_at": now_ms,
    }
    vq = normalize_execution_quote(future_quote, now_ms=now_ms)
    assert vq.is_valid is False
    assert vq.rejection_code == "FUTURE_TIMESTAMP"


def test_v15_8_normalize_quote_non_finite_price_rejected():
    """
    SECTION 167:
    Prices containing NaN or Inf in bid, ask, or last are rejected with NON_FINITE_QUOTE.
    """
    now_ms = 1_000_000
    # NaN in bid
    q_nan_bid = {"bid": float("nan"), "ask": 2620.5, "last": 2620.25, "server_time": now_ms, "observed_at": now_ms}
    vq1 = normalize_execution_quote(q_nan_bid, now_ms=now_ms)
    assert vq1.is_valid is False
    assert vq1.rejection_code == "NON_FINITE_QUOTE"

    # Inf in ask
    q_inf_ask = {"bid": 2620.0, "ask": float("inf"), "last": 2620.25, "server_time": now_ms, "observed_at": now_ms}
    vq2 = normalize_execution_quote(q_inf_ask, now_ms=now_ms)
    assert vq2.is_valid is False
    assert vq2.rejection_code == "NON_FINITE_QUOTE"

    # NaN in last
    q_nan_last = {"bid": 2620.0, "ask": 2620.5, "last": float("nan"), "server_time": now_ms, "observed_at": now_ms}
    vq3 = normalize_execution_quote(q_nan_last, now_ms=now_ms)
    assert vq3.is_valid is False
    assert vq3.rejection_code == "NON_FINITE_QUOTE"


def test_v15_8_normalize_quote_inverted_spread_rejected():
    """
    SECTION 167:
    Spread with ask < bid is rejected with INVERTED_SPREAD.
    """
    now_ms = 1_000_000
    inv_quote = {
        "bid": 2625.0,
        "ask": 2620.0,
        "last": 2622.5,
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    vq = normalize_execution_quote(inv_quote, now_ms=now_ms)
    assert vq.is_valid is False
    assert vq.rejection_code == "INVERTED_SPREAD"


def test_v15_8_normalize_quote_zero_or_negative_price_rejected():
    """
    SECTION 167:
    Zero or negative prices are rejected with ZERO_OR_NEGATIVE_PRICE.
    """
    now_ms = 1_000_000
    zero_quote = {"bid": 0.0, "ask": 2620.5, "last": 2620.0, "server_time": now_ms, "observed_at": now_ms}
    vq1 = normalize_execution_quote(zero_quote, now_ms=now_ms)
    assert vq1.is_valid is False
    assert vq1.rejection_code == "ZERO_OR_NEGATIVE_PRICE"

    neg_quote = {"bid": -10.0, "ask": 2620.5, "last": 2620.0, "server_time": now_ms, "observed_at": now_ms}
    vq2 = normalize_execution_quote(neg_quote, now_ms=now_ms)
    assert vq2.is_valid is False
    assert vq2.rejection_code == "ZERO_OR_NEGATIVE_PRICE"


def test_v15_8_normalize_quote_timestamp_aliases_supported():
    """
    SECTION 187:
    Supports timestamp aliases: server_time_ms, exchange_ts_ms, received_at_ms.
    """
    now_ms = 1_000_000
    alias_quote = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "server_time_ms": now_ms - 200,
        "received_at_ms": now_ms - 100,
    }
    vq = normalize_execution_quote(alias_quote, now_ms=now_ms)
    assert vq.is_valid is True
    assert vq.exchange_time == now_ms - 200
    assert vq.observed_at == now_ms - 100


def test_v15_8_normalize_quote_unsupported_types_rejected():
    """
    SECTION 227:
    Rejection of unsupported types (None, list, string, int).
    """
    now_ms = 1_000_000

    vq_none = normalize_execution_quote(None, now_ms=now_ms)
    assert vq_none.is_valid is False
    assert vq_none.rejection_code == "FEED_DISCONNECTED"

    vq_list = normalize_execution_quote([2620.0, 2620.5], now_ms=now_ms)
    assert vq_list.is_valid is False
    assert vq_list.rejection_code == "MALFORMED_QUOTE"

    vq_str = normalize_execution_quote("2620.0", now_ms=now_ms)
    assert vq_str.is_valid is False
    assert vq_str.rejection_code == "MALFORMED_QUOTE"

    vq_int = normalize_execution_quote(2620, now_ms=now_ms)
    assert vq_int.is_valid is False
    assert vq_int.rejection_code == "MALFORMED_QUOTE"


# =====================================================================
# INTEGRATION TESTS: _arm_paper_candidate() & evaluate_cycle()
# =====================================================================

def test_v15_8_arm_direct_stale_999s_quote_returns_none_zero_mutations(isolated_db: Session):
    """
    SECTION 247:
    Direct invocation of _arm_paper_candidate() with 999s stale quote returns None
    and leaves WatchSetup and ScenarioSnapshot untouched.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cand = _make_dummy_scalp_candidate("z_stale_arm")
    auth = _make_positive_authority("z_stale_arm")

    now_ms = 1_000_000
    stale_quote = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "is_valid": True,
        "server_time": 1_000,
        "observed_at": 1_000,
    }

    order = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=now_ms, clock=FakeClock(now_ms),
        quote=stale_quote,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=auth,
        auto_enabled=True,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
    )

    assert order is None

    # Verify zero persistence mutations
    assert isolated_db.query(models.WatchSetup).count() == 0
    assert isolated_db.query(models.SMCScenarioSnapshot).count() == 0
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_8_arm_direct_missing_timestamp_returns_none(isolated_db: Session):
    """
    SECTION 267:
    Direct invocation of _arm_paper_candidate() with missing timestamps returns None.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cand = _make_dummy_scalp_candidate("z_no_ts_arm")
    auth = _make_positive_authority("z_no_ts_arm")

    now_ms = 1_000_000
    no_ts_quote = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
    }

    order = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=now_ms, clock=FakeClock(now_ms),
        quote=no_ts_quote,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=auth,
        auto_enabled=True,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
    )

    assert order is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_8_arm_direct_fresh_quote_creates_armed_order(isolated_db: Session):
    """
    SECTION 287:
    Direct invocation of _arm_paper_candidate() with fresh quote creates armed PaperOrder.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cand = _make_dummy_scalp_candidate("z_fresh_arm")
    auth = _make_positive_authority("z_fresh_arm")

    now_ms = 1_000_000
    fresh_quote = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    order = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=now_ms, clock=FakeClock(now_ms),
        quote=fresh_quote,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=auth,
        auto_enabled=True,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
    )

    assert order is not None
    assert order.state == "armed"
    assert isolated_db.query(models.PaperOrder).count() == 1


def test_v15_8_evaluate_cycle_causal_long_stale_quote_blocks_entry(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 307:
    evaluate_cycle() with causal LONG candles blocks entry when quote is 999s stale.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_eval_long_stale", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    stale_quote = {
        "bid": 2622.0,
        "ask": 2622.5,
        "last": 2622.25,
        "is_valid": True,
        "server_time": 1_000,  # 999s stale
        "observed_at": 1_000,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=stale_quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    # Order must NOT be armed
    assert res.get("armed_order_id") is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_8_evaluate_cycle_causal_short_stale_quote_blocks_entry(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 327:
    evaluate_cycle() with causal SHORT candles blocks entry when quote is 999s stale.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_eval_short_stale", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    stale_quote = {
        "bid": 2647.5,
        "ask": 2648.0,
        "last": 2647.75,
        "is_valid": True,
        "server_time": 1_000,  # 999s stale
        "observed_at": 1_000,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=stale_quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res.get("armed_order_id") is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_8_evaluate_cycle_causal_long_fresh_quote_arms_order(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 347:
    evaluate_cycle() with causal LONG candles arms order when quote is fresh.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_eval_long_fresh", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    fresh_quote = {
        "bid": 2622.0,
        "ask": 2622.5,
        "last": 2622.25,
        "is_valid": True,
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=fresh_quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res.get("armed_order_id") is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "LONG"
    assert order.state == "armed"


def test_v15_8_evaluate_cycle_causal_short_fresh_quote_arms_order(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 367:
    evaluate_cycle() with causal SHORT candles arms order when quote is fresh.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_eval_short_fresh", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    fresh_quote = {
        "bid": 2647.5,
        "ask": 2648.0,
        "last": 2647.75,
        "is_valid": True,
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=fresh_quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res.get("armed_order_id") is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "SHORT"
    assert order.state == "armed"
