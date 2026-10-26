"""
backend/tests/test_v17_6_1_quote_timestamps.py
V17.6.1 OBS-01 & OBS-02: Canonical Market Quote Authority & Closed Candle Freshness Tests.
"""

import pytest
from unittest.mock import patch, MagicMock
from services.engine_observability_service import (
    resolve_canonical_quote_timestamps,
    engine_observability_service,
    EngineObservabilityService,
)
from services.quote_validator import CanonicalQuote
from services.candle_cache_service import candle_cache_service
import schemas


def test_q01_canonical_quote_authority_precedence():
    """OBS-01: Validates CanonicalQuote extraction with exchange and transport receive timestamps."""
    now_ms = 1792591200000  # Wednesday 14:00 UTC
    exch_ts = now_ms - 2500  # 2.5s old
    recv_ts = now_ms - 2300  # 2.3s old (transport took 200ms)

    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2730.50,
        ask=2730.80,
        last=2730.65,
        exchange_ts_ms=exch_ts,
        received_at_ms=recv_ts,
        source="WS",
        connection_epoch=1,
        status="VALID",
    )

    res = resolve_canonical_quote_timestamps(quote, now_ms=now_ms)
    assert res["status"] == "VALID"
    assert res["exchange_ts_ms"] == exch_ts
    assert res["received_at_ms"] == recv_ts
    assert res["exchange_age_ms"] == 2500
    assert res["receive_age_ms"] == 2300
    assert res["transport_delay_ms"] == 200
    assert res["bid"] == 2730.50
    assert res["ask"] == 2730.80
    assert res["last"] == 2730.65
    assert len(res["reason_codes"]) == 0


def test_q02_missing_exchange_ts_fallback_unverified():
    """OBS-01: When exchange_ts_ms is missing but received_at_ms is present, status is UNVERIFIED_EXCHANGE."""
    now_ms = 1792591200000
    recv_ts = now_ms - 1000

    quote = {
        "symbol": "XAUUSDT",
        "bid": 2730.0,
        "ask": 2730.2,
        "last": 2730.1,
        "received_at_ms": recv_ts,
    }

    res = resolve_canonical_quote_timestamps(quote, now_ms=now_ms)
    assert res["status"] == "UNVERIFIED_EXCHANGE"
    assert res["exchange_ts_ms"] is None
    assert res["received_at_ms"] == recv_ts
    assert res["exchange_age_ms"] is None
    assert res["receive_age_ms"] == 1000
    assert "EXCHANGE_TS_MISSING_FALLBACK_TO_RECEIVE" in res["reason_codes"]


def test_q03_future_clock_skew_and_malformed_rejected():
    """OBS-01: Timestamps far in future (> 60s) or non-numeric types fail closed."""
    now_ms = 1792591200000

    # 1. Future clock skew > 60s
    quote_future = {
        "exchange_ts_ms": now_ms + 120_000,
        "received_at_ms": now_ms,
    }
    res_f = resolve_canonical_quote_timestamps(quote_future, now_ms=now_ms)
    assert res_f["exchange_ts_ms"] is None
    assert "INVALID_EXCHANGE_TS" in res_f["reason_codes"]

    # 2. Boolean timestamp
    quote_bool = {
        "exchange_ts_ms": True,
        "received_at_ms": now_ms,
    }
    res_b = resolve_canonical_quote_timestamps(quote_bool, now_ms=now_ms)
    assert res_b["exchange_ts_ms"] is None

    # 3. None quote
    res_none = resolve_canonical_quote_timestamps(None, now_ms=now_ms)
    assert res_none["status"] == "UNAVAILABLE"
    assert "QUOTE_IS_NONE" in res_none["reason_codes"]


def test_q04_multi_timeframe_closed_candle_freshness():
    """OBS-02: Verified closed candles are used for TF age calculation, ignoring in-flight unclosed bars."""
    svc = EngineObservabilityService()
    now_ms = 1792591200000  # Wednesday 14:00 UTC

    # Clear cache
    candle_cache_service._cache.clear()

    # Add candles for 15M: last candle is in-flight (started 2 min ago, unclosed),
    # penultimate candle is closed (started 17 min ago)
    unclosed_bar = schemas.CandleCreate(
        symbol="XAUUSDT",
        timeframe="15M",
        timestamp=now_ms - 2 * 60 * 1000,
        open=2730.0,
        high=2735.0,
        low=2729.0,
        close=2732.0,
        volume=100.0,
        is_closed=False,
    )
    closed_bar = schemas.CandleCreate(
        symbol="XAUUSDT",
        timeframe="15M",
        timestamp=now_ms - 17 * 60 * 1000,
        open=2725.0,
        high=2731.0,
        low=2724.0,
        close=2730.0,
        volume=500.0,
        is_closed=True,
    )
    candle_cache_service._cache[("XAUUSDT", "15M")] = [closed_bar, unclosed_bar]

    with patch("services.bitget_ws_service.bitget_ws_service") as mock_ws:
        mock_ws.transport_state = "CONNECTED"
        mock_ws.feed_source = "WS"
        mock_ws.latest_quote = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2732.0,
            ask=2732.2,
            last=2732.1,
            exchange_ts_ms=now_ms - 1000,
            received_at_ms=now_ms - 900,
            source="WS",
            connection_epoch=1,
            status="VALID",
        )
        mock_ws.reconnect_count = 0
        mock_ws.last_success_time = now_ms - 1000

        snap = svc.build_market_feed_health_snapshot(symbol="XAUUSDT", now_ms=now_ms)
        tf_15m = snap["timeframe_health"]["15M"]
        # Expected closed candle end ts = closed_bar.timestamp + 15M (900_000ms) = now_ms - 2min
        expected_closed_end = (now_ms - 17 * 60 * 1000) + 15 * 60 * 1000
        assert tf_15m["latest_closed_candle_ts"] == expected_closed_end
        assert tf_15m["candle_age_sec"] == 120.0  # 2 minutes = 120s
        assert tf_15m["freshness_state"] == "FRESH"


def test_q05_gap_detection_during_open_market():
    """OBS-02: Gap detection triggers during open market when candle intervals exceed expected step."""
    svc = EngineObservabilityService()
    now_ms = 1792591200000  # Wednesday 14:00 UTC

    candle_cache_service._cache.clear()

    # Two 1M candles with a 5-minute gap in active Wednesday trading hours
    bar1 = schemas.CandleCreate(
        symbol="XAUUSDT",
        timeframe="1M",
        timestamp=now_ms - 10 * 60 * 1000,
        open=2730.0,
        high=2731.0,
        low=2729.0,
        close=2730.0,
        volume=10.0,
        is_closed=True,
    )
    bar2 = schemas.CandleCreate(
        symbol="XAUUSDT",
        timeframe="1M",
        timestamp=now_ms - 5 * 60 * 1000,  # 5 min jump > 1 min
        open=2730.0,
        high=2731.0,
        low=2729.0,
        close=2730.0,
        volume=10.0,
        is_closed=True,
    )
    candle_cache_service._cache[("XAUUSDT", "1M")] = [bar1, bar2]

    with patch("services.bitget_ws_service.bitget_ws_service") as mock_ws:
        mock_ws.transport_state = "CONNECTED"
        mock_ws.feed_source = "WS"
        mock_ws.latest_quote = CanonicalQuote(
            symbol="XAUUSDT",
            bid=2730.0,
            ask=2730.2,
            last=2730.1,
            exchange_ts_ms=now_ms - 1000,
            received_at_ms=now_ms - 900,
            source="WS",
            connection_epoch=1,
            status="VALID",
        )
        mock_ws.reconnect_count = 0
        mock_ws.last_success_time = now_ms - 1000

        snap = svc.build_market_feed_health_snapshot(symbol="XAUUSDT", now_ms=now_ms)
        assert snap["has_gap"] is True
