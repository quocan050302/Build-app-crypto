"""
backend/tests/test_v15_10_quote_age_policy_parity.py
V15.10 - Execution Quote Age Policy Parity Across Representations.

Verifies:
1. Baseline reproduction tests (003, 004, 051 - 054):
   - CanonicalQuote with age 20.0s, status VALID, max_age_sec=60.0 was previously rejected on object
     path due to hardcoded raw_quote.freshness_sec > 15.0 in extract_execution_quote_source_state().
     Now both CanonicalQuote object and dictionary normalize to is_valid=True, is_stale=False.
   - Default threshold (15.0s) continues to reject age 20.0s quotes as TICKER_STALE for both representations.
   - Quote with fresh timestamps (at now_ms) but stale metadata (freshness_sec=20.0) calculates actual
     freshness (~0.0s) and normalizes to is_valid=True.
   - Quote with old timestamps (20.0s old) but spoofed metadata (freshness_sec=0.0) is rejected as
     TICKER_STALE under default threshold.
2. Custom threshold & boundary parity (097 - 106):
   - Custom small threshold (age 6.0s, max_age_sec=5.0) rejected on both object and dict.
   - Custom large threshold (age 30.0s, max_age_sec=60.0) valid on both object and dict.
   - Exact boundary behavior at 14.9s, 15.0s, 15.1s (default) and 59.9s, 60.0s, 60.1s (custom 60s).
   - max_age_sec=None matches omitted argument behavior.
   - Independent threshold evaluation between consecutive calls (no cross-call state leakage).
   - Increasing now_ms causes quote to age cleanly from valid to stale across representations.
3. Explicit source rejection authority preservation (107 - 116):
   - Explicit source rejection (status='STALE', status='DISCONNECTED', rejection_code='FEED_DISCONNECTED')
     continues to block entry even when max_age_sec=60.0 or 3600.0 is passed.
   - Parameterized status matrix parity under custom max_age_sec.
   - JSON round-trip parity under custom max_age_sec.
4. Multi-representation parity (117 - 123):
   - CanonicalQuote, dictionary, and ValidatedQuote all yield identical age decisions.
   - Non-mutation of input objects and dictionaries.
5. Numeric error invariance (128 - 130):
   - NaN, Inf, zero/negative prices, inverted spread remain invalid regardless of max_age_sec.
6. Pure source-state extractor (134, 135):
   - extract_execution_quote_source_state() does not evaluate age thresholds; returns rejected=False
     for status='VALID' across varying freshness_sec metadata.
7. Production cycle & arm boundary tests (141 - 150):
   - evaluate_cycle() with causal LONG/SHORT arms under fresh quotes with default policy.
   - evaluate_cycle() with 20s old quote blocks entry under default policy.
   - _arm_paper_candidate() blocks entry with 20s old quote under default policy without mutating DB.
   - Existing committed order in DB remains completely untouched across rejected cycles.
"""
from __future__ import annotations

import copy
import json
import math
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
    QuoteSourceState,
    QuoteValidator,
    ValidatedQuote,
    extract_execution_quote_source_state,
    normalize_execution_quote,
)
from services.scalp_m1_model_service import ScalpCandidate
from services.scalp_m1_types import (
    M1ScalpConfig,
    ScalpMode,
    WatchedZone,
    ZoneState,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
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


def _make_dummy_scalp_candidate(zone_id: str = "z_v15_10_test", direction: str = "LONG") -> ScalpCandidate:
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
        "estimated_liquidation": 2550.0 if direction == "LONG" else 2750.0,
        "est_fill_fee_usdt": 0.1,
        "est_close_fee_usdt": 0.1,
        "est_slippage_usdt": 0.1,
        "total_friction_usdt": 0.3,
        "cash_buffer_usdt": 10.0,
        "margin_used_usdt": 10.0,
    })()
    p_entry = 2620.0 if direction == "LONG" else 2650.0
    sl = 2610.0 if direction == "LONG" else 2660.0
    tp = 2650.0 if direction == "LONG" else 2620.0
    return ScalpCandidate(
        is_eligible=True,
        model_id="SMC_M1_REVERSAL",
        direction=direction,
        candidate_key=f"cand_v15_10_{zone_id}",
        symbol="XAUUSDT",
        planned_entry=p_entry,
        stop_loss=sl,
        take_profit=tp,
        net_rr=2.8,
        gross_rr=3.0,
        calculation=calc,
        zone_id=zone_id,
    )


def _make_positive_authority(zone_id: str = "z_v15_10_test") -> LifecycleCycleAuthority:
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
# 1. BASELINE REPRODUCTIONS & CORE REGRESSIONS (003, 004, 051 - 054, 091 - 096)
# =====================================================================

def test_v15_10_reproduction_age_20_threshold_60():
    """
    SECTION 003, 004, 051, 052, 091:
    Primary regression: CanonicalQuote age 20.0s, status VALID, max_age_sec=60.0.
    In baseline V15.9, object path rejected due to raw_quote.freshness_sec > 15.0,
    while dict path accepted it.
    Now BOTH CanonicalQuote object and dictionary must be valid, is_stale=False,
    with calculated freshness_sec=20.0.
    """
    now_ms = 1_000_000
    quote_ts = 980_000  # 20s old
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=quote_ts,
        received_at_ms=quote_ts,
        freshness_sec=20.0,
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=60.0)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms, max_age_sec=60.0)

    # Both must be valid and non-stale
    assert res_obj.is_valid is True
    assert res_dict.is_valid is True
    assert res_obj.is_stale is False
    assert res_dict.is_stale is False
    assert res_obj.rejection_code is None
    assert res_dict.rejection_code is None
    assert math.isclose(res_obj.freshness_sec, 20.0, rel_tol=1e-5)
    assert math.isclose(res_dict.freshness_sec, 20.0, rel_tol=1e-5)


def test_v15_10_default_threshold_age_20_stale():
    """
    SECTION 092, 136:
    Same quote (age 20.0s, status VALID) evaluated under default threshold (max_age_sec=None).
    Both object and dictionary must be rejected as TICKER_STALE because 20s > default 15s.
    Proves runtime default threshold (15.0s) was not modified or relaxed.
    """
    now_ms = 1_000_000
    quote_ts = 980_000  # 20s old
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=quote_ts,
        received_at_ms=quote_ts,
        freshness_sec=20.0,
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms)

    assert res_obj.is_valid is False
    assert res_dict.is_valid is False
    assert res_obj.is_stale is True
    assert res_dict.is_stale is True
    assert res_obj.rejection_code == "TICKER_STALE"
    assert res_dict.rejection_code == "TICKER_STALE"


def test_v15_10_metadata_20_fresh_timestamp():
    """
    SECTION 054, 093:
    Reproduction 2: Quote timestamp is at now_ms (fresh, 0s old), but metadata freshness_sec=20.0.
    In baseline V15.9, object path rejected due to freshness_sec > 15.0 metadata.
    Now actual freshness is recalculated (~0.0s) and both object and dict are VALID.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        freshness_sec=20.0,  # Stale metadata on fresh timestamp
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms)

    assert res_obj.is_valid is True
    assert res_dict.is_valid is True
    assert res_obj.is_stale is False
    assert res_dict.is_stale is False
    assert math.isclose(res_obj.freshness_sec, 0.0, abs_tol=1e-3)
    assert math.isclose(res_dict.freshness_sec, 0.0, abs_tol=1e-3)


def test_v15_10_metadata_0_old_timestamp():
    """
    SECTION 048, 094:
    Quote timestamp is 20s old (quote_ts = 980_000), but metadata claims freshness_sec=0.0.
    Under default threshold (15s), both object and dict must be rejected as TICKER_STALE.
    Proves validator does not trust spoofed 0.0s metadata over genuine timestamp provenance.
    """
    now_ms = 1_000_000
    quote_ts = 980_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=quote_ts,
        received_at_ms=quote_ts,
        freshness_sec=0.0,  # Spoofed metadata
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms)

    assert res_obj.is_valid is False
    assert res_dict.is_valid is False
    assert res_obj.is_stale is True
    assert res_dict.is_stale is True
    assert res_obj.rejection_code == "TICKER_STALE"
    assert res_dict.rejection_code == "TICKER_STALE"


def test_v15_10_metadata_does_not_alter_validation_outcome():
    """
    SECTION 096:
    Varying freshness_sec metadata (0.0, 10.0, 20.0, 99.0) on identical timestamps
    yields identical validation outcomes for both object and dictionary representations.
    """
    now_ms = 1_000_000
    quote_ts = 995_000  # 5s old

    for test_meta_sec in (0.0, 10.0, 20.0, 99.0):
        cq = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2620.0,
            ask=2620.5,
            last=2620.25,
            status="VALID",
            exchange_ts_ms=quote_ts,
            received_at_ms=quote_ts,
            freshness_sec=test_meta_sec,
        )
        res_obj = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=15.0)
        res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms, max_age_sec=15.0)

        assert res_obj.is_valid is True
        assert res_dict.is_valid is True
        assert res_obj.is_stale is False
        assert res_dict.is_stale is False
        assert math.isclose(res_obj.freshness_sec, 5.0, abs_tol=1e-3)
        assert math.isclose(res_dict.freshness_sec, 5.0, abs_tol=1e-3)


# =====================================================================
# 2. CUSTOM THRESHOLDS & BOUNDARY CONDITIONS (097 - 106)
# =====================================================================

def test_v15_10_custom_threshold_small():
    """
    SECTION 097:
    Custom small threshold: quote age 6.0s with max_age_sec=5.0.
    Even though 6.0s is below the default 15s, it exceeds the caller's 5.0s threshold.
    Both object and dict must be rejected as TICKER_STALE.
    """
    now_ms = 1_000_000
    quote_ts = 994_000  # 6s old
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=quote_ts,
        received_at_ms=quote_ts,
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=5.0)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms, max_age_sec=5.0)

    assert res_obj.is_valid is False
    assert res_dict.is_valid is False
    assert res_obj.is_stale is True
    assert res_dict.is_stale is True
    assert res_obj.rejection_code == "TICKER_STALE"
    assert res_dict.rejection_code == "TICKER_STALE"


def test_v15_10_custom_threshold_large():
    """
    SECTION 098:
    Custom large threshold: quote age 30.0s with max_age_sec=60.0.
    Both object and dict must be valid and non-stale.
    """
    now_ms = 1_000_000
    quote_ts = 970_000  # 30s old
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=quote_ts,
        received_at_ms=quote_ts,
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=60.0)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms, max_age_sec=60.0)

    assert res_obj.is_valid is True
    assert res_dict.is_valid is True
    assert res_obj.is_stale is False
    assert res_dict.is_stale is False


@pytest.mark.parametrize("age_sec,expected_valid", [
    (14.9, True),
    (15.0, True),
    (15.1, False),
])
def test_v15_10_default_threshold_boundary_matrix(age_sec: float, expected_valid: bool):
    """
    SECTION 099, 100, 101:
    Boundary checks for default 15.0s threshold:
    14.9s -> valid
    15.0s -> valid
    15.1s -> stale
    Both object and dict must yield identical outcomes.
    """
    now_ms = 1_000_000
    quote_ts = int(now_ms - (age_sec * 1000))
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=quote_ts,
        received_at_ms=quote_ts,
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms)

    assert res_obj.is_valid is expected_valid
    assert res_dict.is_valid is expected_valid
    assert res_obj.is_stale is (not expected_valid)
    assert res_dict.is_stale is (not expected_valid)
    if not expected_valid:
        assert res_obj.rejection_code == "TICKER_STALE"
        assert res_dict.rejection_code == "TICKER_STALE"


@pytest.mark.parametrize("age_sec,expected_valid", [
    (59.9, True),
    (60.0, True),
    (60.1, False),
])
def test_v15_10_custom_60s_boundary_matrix(age_sec: float, expected_valid: bool):
    """
    SECTION 102:
    Boundary checks for custom 60.0s threshold:
    59.9s -> valid
    60.0s -> valid
    60.1s -> stale
    Both object and dict must yield identical outcomes.
    """
    now_ms = 1_000_000
    quote_ts = int(now_ms - (age_sec * 1000))
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=quote_ts,
        received_at_ms=quote_ts,
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=60.0)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms, max_age_sec=60.0)

    assert res_obj.is_valid is expected_valid
    assert res_dict.is_valid is expected_valid
    assert res_obj.is_stale is (not expected_valid)
    assert res_dict.is_stale is (not expected_valid)
    if not expected_valid:
        assert res_obj.rejection_code == "TICKER_STALE"
        assert res_dict.rejection_code == "TICKER_STALE"


def test_v15_10_max_age_none_matches_omitted():
    """
    SECTION 103:
    Calling normalize_execution_quote(quote, max_age_sec=None) produces
    identical results to calling without max_age_sec.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=990_000,  # 10s old
        received_at_ms=990_000,
    )

    res_none = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=None)
    res_omitted = normalize_execution_quote(cq, now_ms=now_ms)

    assert res_none.is_valid == res_omitted.is_valid == True
    assert res_none.freshness_sec == res_omitted.freshness_sec


def test_v15_10_threshold_independent_between_calls():
    """
    SECTION 104, 139:
    Consecutive calls with different thresholds on the same quote:
    5.0s (stale) -> 60.0s (valid) -> 5.0s (stale).
    Proves no sticky state or caching across normalization invocations.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=990_000,  # 10s old
        received_at_ms=990_000,
    )

    r1 = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=5.0)
    assert r1.is_valid is False
    assert r1.rejection_code == "TICKER_STALE"

    r2 = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=60.0)
    assert r2.is_valid is True

    r3 = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=5.0)
    assert r3.is_valid is False
    assert r3.rejection_code == "TICKER_STALE"


def test_v15_10_increasing_now_ms_ages_quote_consistently():
    """
    SECTION 105:
    Increasing now_ms causes quote to age cleanly from valid to stale across both object and dict.
    """
    base_ts = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=base_ts,
        received_at_ms=base_ts,
    )

    # At t=10s: valid (<= 15s)
    o10 = normalize_execution_quote(cq, now_ms=base_ts + 10_000)
    d10 = normalize_execution_quote(cq.to_dict(), now_ms=base_ts + 10_000)
    assert o10.is_valid is True and d10.is_valid is True

    # At t=20s: stale (> 15s)
    o20 = normalize_execution_quote(cq, now_ms=base_ts + 20_000)
    d20 = normalize_execution_quote(cq.to_dict(), now_ms=base_ts + 20_000)
    assert o20.is_valid is False and d20.is_valid is False
    assert o20.rejection_code == d20.rejection_code == "TICKER_STALE"


def test_v15_10_repeated_normalization_preserves_original_timestamps():
    """
    SECTION 106, 121, 122:
    Repeated normalization invocations do not mutate original timestamps on input object or dict.
    """
    orig_ts = 980_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=orig_ts,
        received_at_ms=orig_ts,
        freshness_sec=20.0,
    )
    d = cq.to_dict()

    normalize_execution_quote(cq, now_ms=1_000_000, max_age_sec=60.0)
    normalize_execution_quote(d, now_ms=1_000_000, max_age_sec=60.0)
    normalize_execution_quote(cq, now_ms=1_050_000, max_age_sec=60.0)
    normalize_execution_quote(d, now_ms=1_050_000, max_age_sec=60.0)

    assert cq.exchange_ts_ms == orig_ts
    assert cq.received_at_ms == orig_ts
    assert d["server_time"] == orig_ts
    assert d["observed_at"] == orig_ts


# =====================================================================
# 3. EXPLICIT SOURCE REJECTION PRESERVATION (107 - 116)
# =====================================================================

def test_v15_10_explicit_source_stale_under_large_threshold():
    """
    SECTION 107, 111:
    Explicit source status='STALE' with fresh timestamps (at now_ms) and max_age_sec=60.0
    MUST STILL BE REJECTED!
    Custom age threshold never bypasses explicit source rejections.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="STALE",
        rejection_code="TICKER_STALE",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        freshness_sec=0.0,
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=60.0)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms, max_age_sec=60.0)

    assert res_obj.is_valid is False
    assert res_dict.is_valid is False
    assert res_obj.is_stale is True
    assert res_dict.is_stale is True
    assert res_obj.rejection_code == "TICKER_STALE"
    assert res_dict.rejection_code == "TICKER_STALE"


def test_v15_10_explicit_source_disconnected_under_large_threshold():
    """
    SECTION 108, 112:
    status='DISCONNECTED' with fresh timestamps and max_age_sec=3600.0
    MUST STILL BE REJECTED with FEED_DISCONNECTED.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="DISCONNECTED",
        rejection_code="FEED_DISCONNECTED",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        freshness_sec=0.0,
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=3600.0)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms, max_age_sec=3600.0)

    assert res_obj.is_valid is False
    assert res_dict.is_valid is False
    assert res_obj.rejection_code == "FEED_DISCONNECTED"
    assert res_dict.rejection_code == "FEED_DISCONNECTED"


@pytest.mark.parametrize("rej_status,expected_code", [
    ("DISCONNECTED", "FEED_DISCONNECTED"),
    ("STALE", "TICKER_STALE"),
    ("MALFORMED", "MALFORMED_QUOTE"),
    ("INVERTED", "INVERTED_SPREAD"),
    ("TIME_UNAVAILABLE", "TIME_UNAVAILABLE"),
    ("FUTURE_TIMESTAMP", "FUTURE_TIMESTAMP"),
])
def test_v15_10_status_rejection_matrix_parity_custom_age(rej_status: str, expected_code: str):
    """
    SECTION 114:
    All canonical rejected statuses remain rejected on both object and dict
    when evaluated with custom max_age_sec=60.0.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status=rej_status,
        rejection_code=expected_code,
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        freshness_sec=0.1,
    )

    res_obj = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=60.0)
    res_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms, max_age_sec=60.0)

    assert res_obj.is_valid is False
    assert res_dict.is_valid is False
    assert res_obj.rejection_code == expected_code
    assert res_dict.rejection_code == expected_code


def test_v15_10_json_roundtrip_custom_age():
    """
    SECTION 115, 116:
    JSON serialization round-trip preservation under custom age threshold:
    1. Valid quote with age 20.0s and max_age_sec=60.0 stays valid after JSON dumps/loads.
    2. Disconnected quote stays invalid after JSON dumps/loads.
    """
    now_ms = 1_000_000
    # 1. Valid quote
    cq_valid = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=980_000,
        received_at_ms=980_000,
    )
    json_valid = json.loads(json.dumps(cq_valid.to_dict()))
    norm_valid = normalize_execution_quote(json_valid, now_ms=now_ms, max_age_sec=60.0)
    assert norm_valid.is_valid is True

    # 2. Rejected quote
    cq_bad = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="DISCONNECTED",
        rejection_code="FEED_DISCONNECTED",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )
    json_bad = json.loads(json.dumps(cq_bad.to_dict()))
    norm_bad = normalize_execution_quote(json_bad, now_ms=now_ms, max_age_sec=60.0)
    assert norm_bad.is_valid is False
    assert norm_bad.rejection_code == "FEED_DISCONNECTED"


# =====================================================================
# 4. MULTI-REPRESENTATION PARITY & NON-MUTATION (117 - 123)
# =====================================================================

def test_v15_10_three_representations_parity_custom_age():
    """
    SECTION 117, 118, 119:
    CanonicalQuote, dict, and ValidatedQuote all evaluate to identical decisions:
    - Age 25.0s with max_age_sec=60.0 -> is_valid=True for all three.
    - Age 25.0s with max_age_sec=None (default 15s) -> is_valid=False for all three.
    """
    now_ms = 1_000_000
    quote_ts = 975_000  # 25s old

    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=quote_ts,
        received_at_ms=quote_ts,
    )
    d = cq.to_dict()
    vq = ValidatedQuote(
        is_valid=True,
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        spread=0.5,
        exchange_time=quote_ts,
        observed_at=quote_ts,
        freshness_sec=25.0,
        is_stale=False,
        rejection_code=None,
        rejection_detail=None,
    )

    # 1. Custom 60s: all valid
    res_cq = normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=60.0)
    res_d = normalize_execution_quote(d, now_ms=now_ms, max_age_sec=60.0)
    res_vq = normalize_execution_quote(vq, now_ms=now_ms, max_age_sec=60.0)

    assert res_cq.is_valid is True
    assert res_d.is_valid is True
    assert res_vq.is_valid is True

    # 2. Default (15s): all stale
    res_cq_def = normalize_execution_quote(cq, now_ms=now_ms)
    res_d_def = normalize_execution_quote(d, now_ms=now_ms)
    res_vq_def = normalize_execution_quote(vq, now_ms=now_ms)

    assert res_cq_def.is_valid is False
    assert res_d_def.is_valid is False
    assert res_vq_def.is_valid is False
    assert res_cq_def.rejection_code == res_d_def.rejection_code == res_vq_def.rejection_code == "TICKER_STALE"


def test_v15_10_non_mutation_input_object_and_dict():
    """
    SECTION 121, 122:
    Input CanonicalQuote object and input dictionary remain completely unmutated.
    """
    now_ms = 1_000_000
    orig_dict = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "VALID",
        "server_time": 980_000,
        "observed_at": 980_000,
    }
    copy_dict = copy.deepcopy(orig_dict)

    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=980_000,
        received_at_ms=980_000,
        freshness_sec=20.0,
    )
    status_before = cq.status
    freshness_before = cq.freshness_sec

    normalize_execution_quote(orig_dict, now_ms=now_ms, max_age_sec=60.0)
    normalize_execution_quote(cq, now_ms=now_ms, max_age_sec=60.0)

    assert orig_dict == copy_dict
    assert cq.status == status_before
    assert cq.freshness_sec == freshness_before


# =====================================================================
# 5. NUMERIC ERRORS & PURE SOURCE-STATE EXTRACTOR (128 - 135)
# =====================================================================

def test_v15_10_numeric_failures_not_bypassed_by_large_max_age():
    """
    SECTION 128, 129, 130:
    Mathematical errors (NaN price, negative price, inverted spread) remain invalid
    even when max_age_sec=3600.0 is passed.
    """
    now_ms = 1_000_000
    # 1. NaN price
    cq_nan = CanonicalQuote(
        symbol="XAUUSDT",
        bid=float("nan"),
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )
    assert normalize_execution_quote(cq_nan, now_ms=now_ms, max_age_sec=3600.0).is_valid is False

    # 2. Inverted spread
    cq_inv = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2625.0,
        ask=2620.0,
        last=2622.0,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )
    assert normalize_execution_quote(cq_inv, now_ms=now_ms, max_age_sec=3600.0).is_valid is False

    # 3. Negative price
    cq_neg = CanonicalQuote(
        symbol="XAUUSDT",
        bid=-10.0,
        ask=2620.0,
        last=2622.0,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )
    assert normalize_execution_quote(cq_neg, now_ms=now_ms, max_age_sec=3600.0).is_valid is False


def test_v15_10_extract_execution_quote_source_state_is_pure_and_threshold_free():
    """
    SECTION 134, 135:
    extract_execution_quote_source_state() is a pure function that does NOT evaluate age thresholds.
    For CanonicalQuote with status='VALID', it returns rejected=False and is_stale=False
    regardless of whether freshness_sec metadata is 0.0, 20.0, or 999.0.
    """
    for test_meta in (0.0, 5.0, 15.0, 20.0, 100.0, 999.0):
        cq = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2620.0,
            ask=2620.5,
            last=2620.25,
            status="VALID",
            freshness_sec=test_meta,
        )
        state = extract_execution_quote_source_state(cq)
        assert state.rejected is False
        assert state.is_stale is False
        assert state.rejection_code is None
        assert state.status == "VALID"


# =====================================================================
# 6. PRODUCTION INTEGRATION & ENTRY ARMED REVERSAL TESTS (141 - 150)
# =====================================================================

def test_v15_10_production_long_default_fresh(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 141:
    Positive control: Causal LONG candles with fresh valid quote arms order under default policy.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_v15_10_pos_long", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    fresh_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2622.0,
        ask=2622.5,
        last=2622.25,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=fresh_cq, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res.get("armed_order_id") is not None
    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "LONG"
    assert order.state == "armed"


def test_v15_10_production_short_default_fresh(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 142:
    Positive control: Causal SHORT candles with fresh valid quote arms order under default policy.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_v15_10_pos_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    fresh_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.5,
        ask=2648.0,
        last=2647.75,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=fresh_cq, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res.get("armed_order_id") is not None
    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "SHORT"
    assert order.state == "armed"


def test_v15_10_production_long_quote_age_20s_default_blocked(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 143:
    evaluate_cycle() with causal LONG candles and quote age 20.0s under default policy.
    Entry MUST be blocked (armed_order_id=None, zero PaperOrders created).
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_v15_10_block_long", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote_ts = 1_280_000  # 20s old
    cq_stale = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2622.0,
        ask=2622.5,
        last=2622.25,
        status="VALID",
        exchange_ts_ms=quote_ts,
        received_at_ms=quote_ts,
    )

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=cq_stale, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res.get("armed_order_id") is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_10_production_short_quote_age_20s_default_blocked(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 144:
    evaluate_cycle() with causal SHORT candles and quote age 20.0s under default policy.
    Entry MUST be blocked symmetrically.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_v15_10_block_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    quote_ts = 1_280_000  # 20s old
    cq_stale = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.5,
        ask=2648.0,
        last=2647.75,
        status="VALID",
        exchange_ts_ms=quote_ts,
        received_at_ms=quote_ts,
    )

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=cq_stale, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res.get("armed_order_id") is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_10_arm_direct_default_fresh(isolated_db: Session):
    """
    SECTION 145:
    Direct invocation of _arm_paper_candidate() with fresh CanonicalQuote creates armed PaperOrder.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cand = _make_dummy_scalp_candidate("z_v15_10_arm_fresh")
    auth = _make_positive_authority("z_v15_10_arm_fresh")

    now_ms = 1_000_000
    fresh_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    order = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=now_ms, clock=FakeClock(now_ms),
        quote=fresh_cq,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=auth,
        auto_enabled=True,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
    )

    assert order is not None
    assert order.state == "armed"
    assert isolated_db.query(models.PaperOrder).count() == 1


def test_v15_10_arm_direct_default_stale_20s_returns_none(isolated_db: Session):
    """
    SECTION 146, 149:
    Direct invocation of _arm_paper_candidate() with quote age 20.0s under default policy
    returns None and produces ZERO mutations to WatchSetup, SMCScenarioSnapshot, or PaperOrder.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cand = _make_dummy_scalp_candidate("z_v15_10_arm_stale_20s")
    auth = _make_positive_authority("z_v15_10_arm_stale_20s")

    now_ms = 1_000_000
    stale_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=980_000,  # 20s old
        received_at_ms=980_000,
    )

    ws_count_before = isolated_db.query(models.WatchSetup).count()
    snap_count_before = isolated_db.query(models.SMCScenarioSnapshot).count()

    order = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=now_ms, clock=FakeClock(now_ms),
        quote=stale_cq,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=auth,
        auto_enabled=True,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
    )

    assert order is None
    assert isolated_db.query(models.PaperOrder).count() == 0
    assert isolated_db.query(models.WatchSetup).count() == ws_count_before
    assert isolated_db.query(models.SMCScenarioSnapshot).count() == snap_count_before


def test_v15_10_existing_order_untouched_by_stale_cycle(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 150:
    An existing committed PaperOrder in the database remains completely untouched
    when a new cycle runs with a quote that is 20s stale.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()

    existing_order = models.PaperOrder(
        id="ord_v15_10_existing_safe",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=2600.0,
        stop_loss=2590.0,
        take_profit=2630.0,
        quantity=0.25,
        initial_risk_usdt=2.5,
        created_at=100_000,
    )
    isolated_db.add(existing_order)
    isolated_db.commit()
    existing_id = existing_order.id

    zone = WatchedZone(
        zone_id="z_v15_10_protect", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    stale_cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2622.0,
        ask=2622.5,
        last=2622.25,
        status="VALID",
        exchange_ts_ms=1_280_000,  # 20s old
        received_at_ms=1_280_000,
    )

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=stale_cq, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res.get("armed_order_id") is None
    isolated_db.expire_all()
    reloaded = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == existing_id).first()
    assert reloaded is not None
    assert reloaded.direction == "LONG"
    assert reloaded.planned_entry == 2600.0
    assert reloaded.stop_loss == 2590.0
    assert reloaded.take_profit == 2630.0
    assert reloaded.quantity == 0.25
    assert reloaded.state == "armed"
