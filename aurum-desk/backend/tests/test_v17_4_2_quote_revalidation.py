"""
backend/tests/test_v17_4_2_quote_revalidation.py
Comprehensive test suite for QuoteValidator revalidation contract (Q01 - Q09).
"""
import math
import pytest
from services.quote_validator import QuoteValidator, ValidatedQuote, CanonicalQuote


def test_q01_raw_and_dto_fresh_parity():
    """Q01: Raw dict and ValidatedQuote DTO both validate to fresh valid quotes with identical semantics."""
    t0 = 1700000000000
    raw_dict = {
        "bid": 2620.10,
        "ask": 2620.35,
        "last": 2620.25,
        "server_time": t0,
        "observed_at": t0,
    }
    q1 = QuoteValidator.validate_ticker(raw_dict, now_ms=t0, max_age_sec=15)
    assert q1.is_valid
    assert q1.is_stale is False
    assert q1.freshness_sec == 0.0
    assert q1.bid == 2620.10
    assert q1.ask == 2620.35

    # Re-validate DTO at t0 + 1000 (1 second later)
    q2 = QuoteValidator.validate_ticker(q1, now_ms=t0 + 1000, max_age_sec=15)
    assert q2.is_valid
    assert q2.is_stale is False
    assert q2.freshness_sec == 1.0
    assert q2.bid == q1.bid
    assert q2.ask == q1.ask


def test_q02_dto_t0_plus_60_stale():
    """Q02: DTO validated at T0 becomes stale when revalidated at T0+60s with max_age=15s."""
    t0 = 1700000000000
    q = QuoteValidator.validate_ticker({
        "bid": 2625.0,
        "ask": 2625.3,
        "last": 2625.1,
        "server_time": t0,
        "observed_at": t0,
    }, now_ms=t0, max_age_sec=15)
    assert q.is_valid

    stale_q = QuoteValidator.validate_ticker(q, now_ms=t0 + 60000, max_age_sec=15)
    assert not stale_q.is_valid
    assert stale_q.is_stale is True
    assert stale_q.freshness_sec == 60.0
    assert stale_q.rejection_code == "TICKER_STALE"


def test_q03_input_unchanged():
    """Q03: Revalidating a quote never mutates the original quote instance."""
    t0 = 1700000000000
    original = QuoteValidator.validate_ticker({
        "bid": 2630.0,
        "ask": 2630.2,
        "last": 2630.1,
        "server_time": t0,
        "observed_at": t0,
    }, now_ms=t0)
    assert original.is_valid
    assert original.freshness_sec == 0.0

    revalidated = QuoteValidator.validate_ticker(original, now_ms=t0 + 30000, max_age_sec=15)
    assert revalidated is not original
    assert not revalidated.is_valid
    # Original must remain untouched!
    assert original.is_valid is True
    assert original.freshness_sec == 0.0


def test_q04_stricter_max_age():
    """Q04: A quote validated with max_age=15s fails when revalidated with a stricter max_age=2s at age 3s."""
    t0 = 1700000000000
    q = QuoteValidator.validate_ticker({
        "bid": 2600.0,
        "ask": 2600.5,
        "last": 2600.2,
        "server_time": t0,
        "observed_at": t0,
    }, now_ms=t0, max_age_sec=15)
    assert q.is_valid

    # At t0 + 3s, max_age 15s passes
    q_lenient = QuoteValidator.validate_ticker(q, now_ms=t0 + 3000, max_age_sec=15)
    assert q_lenient.is_valid

    # At t0 + 3s, max_age 2s fails
    q_strict = QuoteValidator.validate_ticker(q, now_ms=t0 + 3000, max_age_sec=2)
    assert not q_strict.is_valid
    assert q_strict.is_stale is True
    assert q_strict.rejection_code == "TICKER_STALE"


def test_q05_threshold_boundary_15s():
    """Q05: Parity at threshold boundary: age <= 15s valid, age > 15s stale."""
    t0 = 1700000000000
    q = QuoteValidator.validate_ticker({
        "bid": 2610.0,
        "ask": 2610.4,
        "last": 2610.2,
        "server_time": t0,
        "observed_at": t0,
    }, now_ms=t0, max_age_sec=15)

    # 14999 ms -> freshness 14.99s <= 15 -> valid
    q_under = QuoteValidator.validate_ticker(q, now_ms=t0 + 14999, max_age_sec=15)
    assert q_under.is_valid is True

    # 15000 ms -> freshness 15.0s <= 15 -> valid
    q_exact = QuoteValidator.validate_ticker(q, now_ms=t0 + 15000, max_age_sec=15)
    assert q_exact.is_valid is True

    # 15001 ms -> freshness 15.001s > 15 -> stale
    q_over = QuoteValidator.validate_ticker(q, now_ms=t0 + 15001, max_age_sec=15)
    assert q_over.is_valid is False
    assert q_over.rejection_code == "TICKER_STALE"


def test_q06_nan_and_inverted_mutated_object():
    """Q06: If a mutable ValidatedQuote has NaN or inverted spread injected, revalidation rejects it."""
    t0 = 1700000000000
    q = QuoteValidator.validate_ticker({
        "bid": 2615.0,
        "ask": 2615.5,
        "last": 2615.2,
        "server_time": t0,
        "observed_at": t0,
    }, now_ms=t0)
    assert q.is_valid

    # Mutate ask to NaN
    q.ask = float("nan")
    re_nan = QuoteValidator.validate_ticker(q, now_ms=t0)
    assert not re_nan.is_valid
    assert re_nan.rejection_code == "NON_FINITE_QUOTE"

    # Mutate to inverted spread: ask < bid
    q.ask = 2600.0
    q.bid = 2620.0
    re_inv = QuoteValidator.validate_ticker(q, now_ms=t0)
    assert not re_inv.is_valid
    assert re_inv.rejection_code == "INVERTED_SPREAD"


def test_q07_missing_and_future_timestamps():
    """Q07: Future timestamps and malformed timestamps are rejected on revalidation."""
    t0 = 1700000000000
    # Future timestamp (> now + 5000ms)
    q_future = QuoteValidator.validate_ticker({
        "bid": 2600.0, "ask": 2600.5, "last": 2600.2,
        "server_time": t0 + 6000,
        "observed_at": t0 + 6000,
    }, now_ms=t0)
    assert not q_future.is_valid
    assert q_future.rejection_code == "FUTURE_TIMESTAMP"

    # Missing timestamps
    q_missing = QuoteValidator.validate_ticker({
        "bid": 2600.0, "ask": 2600.5, "last": 2600.2,
    }, now_ms=t0)
    assert not q_missing.is_valid
    assert q_missing.rejection_code == "TIME_UNAVAILABLE"


def test_q08_source_rejected_quote_not_resurrected():
    """Q08: A quote previously rejected for TIME_UNAVAILABLE or FEED_DISCONNECTED cannot be resurrected by revalidating."""
    t0 = 1700000000000
    q_disconn = QuoteValidator.validate_ticker(None, now_ms=t0)
    assert not q_disconn.is_valid
    assert q_disconn.rejection_code == "FEED_DISCONNECTED"

    # Re-validate disconnected quote later
    q_re = QuoteValidator.validate_ticker(q_disconn, now_ms=t0 + 1000)
    assert not q_re.is_valid
    assert q_re.rejection_code == "FEED_DISCONNECTED"

    # Re-validate TIME_UNAVAILABLE quote
    q_notime = QuoteValidator.validate_ticker({
        "bid": 2600.0, "ask": 2600.5, "last": 2600.2,
    }, now_ms=t0)
    assert not q_notime.is_valid
    assert q_notime.rejection_code == "TIME_UNAVAILABLE"

    q_notime_re = QuoteValidator.validate_ticker(q_notime, now_ms=t0 + 1000)
    assert not q_notime_re.is_valid
    assert q_notime_re.rejection_code == "TIME_UNAVAILABLE"


def test_q09_canonical_quote_conversion_regression():
    """Q09: CanonicalQuote.to_dict converts cleanly and passes validate_ticker."""
    t0 = 1700000000000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        last=2610.5,
        bid=2610.2,
        ask=2610.6,
        exchange_ts_ms=t0,
        received_at_ms=t0,
        status="VALID",
        spread=0.4,
    )
    d = cq.to_dict()
    q = QuoteValidator.validate_ticker(d, now_ms=t0)
    assert q.is_valid
    assert q.bid == 2610.2
    assert q.ask == 2610.6
