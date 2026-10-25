"""
backend/tests/test_v17_6_4_2_quote_authority.py
===============================================
ECI-02: Authoritative Feed Quality & Verified Freshness Contract Test Suite (QA01 - QA18).
Verifies that GOOD observation coverage is strictly authorized ONLY with proven fresh quotes,
connected transport authority, matching symbol, positive finite bid/ask, and consistent timestamps.
"""
import math
import pytest
from services.engine_observability_service import (
    ObservationEvidenceQuality,
    resolve_observation_evidence_quality,
)


class TestQuoteAuthorityContract:
    """QA01 - QA18: Tests enforcing strict quote authority and verified freshness."""

    def test_qa01_missing_age(self):
        """QA01: Feed with connected transport and valid bid/ask but missing quote_age -> NOT GOOD."""
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": None,
                "last_quote_at_ms": None,
            },
            symbol="XAUUSDT",
        )
        assert res.quality != "GOOD"
        assert res.quality == "UNVERIFIED"
        assert "AGE" in res.reason_code or "MISSING" in res.reason_code or "TIMESTAMP" in res.reason_code

    def test_qa02_missing_timestamp_and_age(self):
        """QA02: Missing both timestamp and age cannot be assumed fresh -> UNVERIFIED."""
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
            },
            symbol="XAUUSDT",
        )
        assert res.quality != "GOOD"
        assert res.quality == "UNVERIFIED"

    def test_qa03_missing_transport(self):
        """QA03: Fresh quote without transport authority cannot be GOOD -> UNVERIFIED."""
        res = resolve_observation_evidence_quality(
            feed_health={
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
            },
            now_ms=1_700_000_000_500,
            symbol="XAUUSDT",
        )
        assert res.quality != "GOOD"
        assert res.quality == "UNVERIFIED"
        assert "TRANSPORT" in res.reason_code

    def test_qa04_unknown_transport(self):
        """QA04: transport_state == 'UNKNOWN' must resolve to UNVERIFIED, never GOOD."""
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "UNKNOWN",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
            },
            now_ms=1_700_000_000_500,
            symbol="XAUUSDT",
        )
        assert res.quality != "GOOD"
        assert res.quality == "UNVERIFIED"
        assert "TRANSPORT" in res.reason_code

    def test_qa05_stale_quote(self):
        """QA05: Quote older than threshold resolves to DEGRADED."""
        now_ms = 1_700_000_000_000
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 20.0,
                "last_quote_at_ms": now_ms - 20_000,
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
        )
        assert res.quality == "DEGRADED"
        assert "STALE" in res.reason_code

    def test_qa06_valid_fresh_ws_quote(self):
        """QA06: Connected WS + fresh valid quote + consistent timestamps -> GOOD."""
        now_ms = 1_700_000_000_500
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
                "symbol": "XAUUSDT",
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
            evidence_origin="LIVE_FORWARD",
        )
        assert res.quality == "GOOD"
        assert res.reason_code == "FEED_VERIFIED_FRESH"
        assert res.quote_valid is True

    def test_qa07_contradictory_age_and_timestamp(self):
        """QA07: quote_age_sec = 0.5 but last_quote_at_ms is 1 hour ago -> DEGRADED."""
        now_ms = 1_700_000_000_000
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": now_ms - 3600_000,  # 1 hour ago!
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
        )
        assert res.quality != "GOOD"
        assert res.quality == "DEGRADED"
        assert "CONFLICT" in res.reason_code or "STALE" in res.reason_code

    def test_qa08_future_timestamp(self):
        """QA08: Future quote timestamp beyond clock skew threshold resolves to DEGRADED."""
        now_ms = 1_700_000_000_000
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": -65.0,
                "last_quote_at_ms": now_ms + 65_000,
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
        )
        assert res.quality == "DEGRADED"
        assert "CLOCK_SKEW" in res.reason_code

    def test_qa09_wrong_symbol(self):
        """QA09: BTCUSDT quote provided for XAUUSDT evaluation fails closed -> UNVERIFIED."""
        now_ms = 1_700_000_000_500
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": 65000.0,
                "ask": 65001.0,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
                "symbol": "BTCUSDT",
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
        )
        assert res.quality != "GOOD"
        assert res.quality == "UNVERIFIED"
        assert "SYMBOL" in res.reason_code

    def test_qa10_missing_source(self):
        """QA10: Absent or UNKNOWN quote source cannot be GOOD."""
        now_ms = 1_700_000_000_500
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "UNKNOWN",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
        )
        assert res.quality != "GOOD"
        assert res.quality == "UNVERIFIED"

    def test_qa11_rest_fallback(self):
        """QA11: REST fallback is conservatively classified as DEGRADED, not full GOOD."""
        now_ms = 1_700_000_000_500
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "REST_FALLBACK",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
        )
        assert res.quality == "DEGRADED"
        assert "REST_FALLBACK" in res.reason_code

    def test_qa12_invalid_status(self):
        """QA12: Quote with status INVALID or MALFORMED cannot be GOOD."""
        now_ms = 1_700_000_000_500
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "status": "INVALID",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
        )
        assert res.quality != "GOOD"
        assert res.quality in ("DEGRADED", "UNVERIFIED")

    def test_qa13_nonfinite_price(self):
        """QA13: Non-finite bid or ask (NaN, Inf) resolves to DEGRADED."""
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": float("nan"),
                "ask": 2730.2,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
            },
            now_ms=1_700_000_000_500,
            symbol="XAUUSDT",
        )
        assert res.quality == "DEGRADED"

    def test_qa14_inverted_spread(self):
        """QA14: Crossed book (ask < bid) resolves to DEGRADED."""
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": 2730.5,
                "ask": 2730.0,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
            },
            now_ms=1_700_000_000_500,
            symbol="XAUUSDT",
        )
        assert res.quality == "DEGRADED"
        assert "CROSSED" in res.reason_code or "ASK_LESS_THAN_BID" in res.reason_code

    def test_qa15_disconnected_socket(self):
        """QA15: DISCONNECTED transport_state resolves to DOWN / DEGRADED."""
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "DISCONNECTED",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.5,
            },
            symbol="XAUUSDT",
        )
        assert res.quality in ("DOWN", "DEGRADED")
        assert res.quality != "GOOD"

    def test_qa16_recent_quote_but_disconnected(self):
        """QA16: Fresh quote timestamp but socket disconnected resolves to DOWN/DEGRADED."""
        now_ms = 1_700_000_000_500
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "DISCONNECTED",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
        )
        assert res.quality in ("DOWN", "DEGRADED")
        assert res.quality != "GOOD"

    def test_qa17_trusted_adapter_source(self):
        """QA17: Primary adapter source (WS) with valid feed resolves to GOOD."""
        now_ms = 1_700_000_000_500
        res = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.5,
                "last_quote_at_ms": 1_700_000_000_000,
                "symbol": "XAUUSDT",
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
        )
        assert res.quality == "GOOD"

    def test_qa18_reconnect_and_fresh_quote(self):
        """QA18: After reconnect, transition to CONNECTED + fresh quote restores GOOD."""
        now_ms = 1_700_000_000_500
        res_down = resolve_observation_evidence_quality(
            feed_health={"transport_state": "RECONNECTING", "quote_source": "WS"},
            now_ms=now_ms,
        )
        assert res_down.quality in ("DEGRADED", "DOWN")

        res_ok = resolve_observation_evidence_quality(
            feed_health={
                "transport_state": "CONNECTED",
                "quote_source": "WS",
                "bid": 2730.0,
                "ask": 2730.2,
                "quote_age_sec": 0.2,
                "last_quote_at_ms": now_ms - 200,
                "symbol": "XAUUSDT",
            },
            now_ms=now_ms,
            symbol="XAUUSDT",
        )
        assert res_ok.quality == "GOOD"
