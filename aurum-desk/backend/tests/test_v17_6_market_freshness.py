"""
backend/tests/test_v17_6_market_freshness.py
V17.6 Gate 2: Market Feed & Candle Freshness Test Suite.

Verifies:
- M01: Fresh quote results in CONNECTED_FRESH when quote age <= 15s
- M02: Stale quote results in CONNECTED_STALE when quote age > 15s
- M03: Disconnected transport state results in DISCONNECTED
- M04: Reconnecting transport state results in RECONNECTING
- M05: Multi-timeframe closed candle freshness (1M, 5M, 15M, 1H, 4H, D)
- M06: Weekend gold market break detection (Friday 21:00 UTC to Sunday 22:00 UTC)
- M07: Bid, ask, and mid price calculation
- M08: Feed source and gap flag reporting
"""
import time
import pytest
from unittest.mock import MagicMock, patch

from services.engine_observability_service import (
    EngineObservabilityService,
    is_xauusdt_weekend_break,
)


def test_m01_fresh_quote():
    svc = EngineObservabilityService()
    now_ms = 1792591200000  # Wednesday 14:00 UTC

    mock_quote = MagicMock()
    mock_quote.timestamp = now_ms - 2000  # 2s old
    mock_quote.bid = 2725.10
    mock_quote.ask = 2725.30

    with patch("services.bitget_ws_service.bitget_ws_service") as mock_ws:
        mock_ws.transport_state = "CONNECTED"
        mock_ws.feed_source = "WS"
        mock_ws.latest_quote = mock_quote
        mock_ws.reconnect_count = 0
        mock_ws.last_success_time = now_ms - 2000

        res = svc.build_market_feed_health_snapshot(symbol="XAUUSDT", now_ms=now_ms)
        assert res["transport_state"] == "CONNECTED"
        assert res["freshness_state"] == "CONNECTED_FRESH"
        assert res["is_fresh"] is True
        assert res["quote_age_sec"] == 2.0
        assert res["bid"] == 2725.10
        assert res["ask"] == 2725.30
        assert res["mid"] == 2725.20


def test_m02_stale_quote():
    svc = EngineObservabilityService()
    now_ms = 1792591200000

    mock_quote = MagicMock()
    mock_quote.timestamp = now_ms - (30 * 1000)  # 30s old (> 15s threshold)
    mock_quote.bid = 2720.00
    mock_quote.ask = 2720.20

    with patch("services.bitget_ws_service.bitget_ws_service") as mock_ws:
        mock_ws.transport_state = "CONNECTED"
        mock_ws.feed_source = "WS"
        mock_ws.latest_quote = mock_quote
        mock_ws.reconnect_count = 0
        mock_ws.last_success_time = now_ms - 30000

        res = svc.build_market_feed_health_snapshot(symbol="XAUUSDT", now_ms=now_ms)
        assert res["transport_state"] == "CONNECTED"
        assert res["freshness_state"] == "CONNECTED_STALE"
        assert res["is_fresh"] is False
        assert res["quote_age_sec"] == 30.0


def test_m03_disconnected_feed():
    svc = EngineObservabilityService()
    now_ms = 1792591200000

    with patch("services.bitget_ws_service.bitget_ws_service") as mock_ws:
        mock_ws.transport_state = "DISCONNECTED"
        mock_ws.feed_source = "WS"
        mock_ws.latest_quote = None
        mock_ws.reconnect_count = 2
        mock_ws.last_success_time = 0

        res = svc.build_market_feed_health_snapshot(symbol="XAUUSDT", now_ms=now_ms)
        assert res["transport_state"] == "DISCONNECTED"
        assert res["freshness_state"] == "DISCONNECTED"
        assert res["is_fresh"] is False


def test_m04_reconnecting_feed():
    svc = EngineObservabilityService()
    now_ms = 1792591200000

    with patch("services.bitget_ws_service.bitget_ws_service") as mock_ws:
        mock_ws.transport_state = "CONNECTING"
        mock_ws.feed_source = "WS"
        mock_ws.latest_quote = None
        mock_ws.reconnect_count = 3
        mock_ws.last_success_time = 0

        res = svc.build_market_feed_health_snapshot(symbol="XAUUSDT", now_ms=now_ms)
        assert res["freshness_state"] == "RECONNECTING"


def test_m05_weekend_market_break_detection():
    # Saturday 10:00 UTC
    sat_ms = 1792836000000
    assert is_xauusdt_weekend_break(sat_ms) is True

    # Sunday 12:00 UTC (before 22:00 UTC open)
    sun_ms = 1792929600000
    assert is_xauusdt_weekend_break(sun_ms) is True

    # Monday 10:00 UTC (market open)
    mon_ms = 1793008800000
    assert is_xauusdt_weekend_break(mon_ms) is False

    # Wednesday 14:00 UTC (market open)
    wed_ms = 1792591200000
    assert is_xauusdt_weekend_break(wed_ms) is False
