import pytest
from services.quote_validator import QuoteValidator

def test_missing_timestamp_rejected():
    now_ms = 1788220800000
    # No timestamp, no observed_at, no received_at_ms
    ticker = {'bid': 2650.0, 'ask': 2650.2, 'last': 2650.0}
    res = QuoteValidator.validate_ticker(ticker, now_ms=now_ms)
    assert res.is_valid is False
    assert res.rejection_code == 'TIME_UNAVAILABLE'
    assert 'Thiếu timestamp' in res.rejection_detail

def test_future_time_rejected():
    now_ms = 1788220800000
    # Timestamp is 10 seconds in the future (> 5s tolerance)
    ticker = {'bid': 2650.0, 'ask': 2650.2, 'last': 2650.0, 'timestamp': now_ms + 10000}
    res = QuoteValidator.validate_ticker(ticker, now_ms=now_ms)
    assert res.is_valid is False
    assert res.rejection_code == 'FUTURE_TIMESTAMP'
    assert 'tương lai' in res.rejection_detail

def test_receipt_not_refreshed():
    now_ms = 1788220800000
    # Quote from 20 seconds ago (> 10s default max age)
    ticker = {'bid': 2650.0, 'ask': 2650.2, 'last': 2650.0, 'timestamp': now_ms - 20000}
    res = QuoteValidator.validate_ticker(ticker, now_ms=now_ms, max_age_sec=10.0)
    assert res.is_valid is False
    assert res.is_stale is True
    assert res.rejection_code == 'TICKER_STALE'
    assert res.freshness_sec >= 20.0

def test_canonical_missing_timestamp_rejected():
    now_ms = 1788220800000
    raw = {'symbol': 'XAUUSDT', 'bid': 2650.0, 'ask': 2650.2, 'last': 2650.0}
    canon = QuoteValidator.validate_canonical(raw, now_ms=now_ms)
    assert canon.is_valid is False
    assert canon.status == 'TIME_UNAVAILABLE'
    assert canon.rejection_code == 'TIME_UNAVAILABLE'

def test_valid_quote_accepted():
    now_ms = 1788220800000
    ticker = {'bid': 2650.0, 'ask': 2650.2, 'last': 2650.0, 'timestamp': now_ms - 1000}
    res = QuoteValidator.validate_ticker(ticker, now_ms=now_ms)
    assert res.is_valid is True
    assert res.is_stale is False
    assert res.freshness_sec == 1.0
    assert res.rejection_code is None
