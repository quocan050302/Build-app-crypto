"""
backend/tests/test_v17_6_4_3c_source_authority.py
Authoritative Test Suite for CPA-02:
Quote Source Authority and Non-Self-Authorizing Invariants.

Covers Test Cases (Section 03, Part 049):
SA01: Raw WS / adapter REST -> conflict / conservative REST
SA02: Raw WS / adapter source UNKNOWN -> UNKNOWN
SA03: Raw WS / no adapter -> UNKNOWN / cannot self-authorize
SA04: Raw REST / adapter WS -> conflict / not WS GOOD
SA05: Genuine adapter WS / matching quote -> WS / GOOD eligible
SA06: Missing quote source -> UNKNOWN
SA07: Wrong-symbol quote -> SYMBOL_MISMATCH
SA08: Cached quote after adapter disconnect -> DISCONNECTED / not GOOD
SA09: Manual CanonicalQuote cannot impersonate runtime feed without adapter
SA10: End-to-end no unauthorized GOOD in DB
"""
import pytest
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base
from database import run_schema_migrations
from services.quote_validator import CanonicalQuote
from services.scalp_watcher_orchestrator import (
    resolve_watcher_observation_provenance,
    scalp_watcher_orchestrator,
)
from services.observation_interval_service import observation_interval_service
from services.engine_observability_service import resolve_observation_evidence_quality
from services.bitget_ws_service import bitget_ws_service


@pytest.fixture
def isolated_db(tmp_path):
    path = tmp_path / "sa_test.sqlite"
    eng = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    run_schema_migrations(eng)
    Session = sessionmaker(bind=eng, autoflush=False)
    s = Session()
    try:
        yield {"session": s, "engine": eng, "Session": Session}
    finally:
        s.close()


def test_sa01_raw_ws_adapter_rest():
    """SA01: Raw WS / adapter REST -> conflict / REST_FALLBACK."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "DISCONNECTED"
    mock_adapter.feed_source = "REST_FALLBACK"
    mock_adapter.connection_epoch = 1
    mock_adapter.symbol = "XAUUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",  # Spoofed WS
        "transport_state": "DISCONNECTED",
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.quote_source == "REST_FALLBACK"
    assert prov.quote_valid is False
    assert prov.reason_code == "SOURCE_AUTHORITY_CONFLICT"


def test_sa02_raw_ws_adapter_unknown():
    """SA02: Raw WS / adapter source UNKNOWN -> UNKNOWN."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "UNKNOWN"
    mock_adapter.connection_epoch = 1
    mock_adapter.symbol = "XAUUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.quote_source == "UNKNOWN"
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality == "UNVERIFIED"


def test_sa03_raw_ws_no_adapter():
    """SA03: Raw WS / no adapter -> UNKNOWN / cannot self-authorize."""
    t0 = 1700000000000
    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=None,  # No adapter!
        evaluated_at_ms=t0,
    )
    assert prov.quote_source == "UNKNOWN"
    assert prov.quote_valid is False
    assert prov.reason_code == "ADAPTER_STATE_UNAVAILABLE"


def test_sa04_raw_rest_adapter_ws():
    """SA04: Raw REST / adapter WS -> conflict / not WS GOOD."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 1
    mock_adapter.symbol = "XAUUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "REST_FALLBACK",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.quote_valid is False
    assert prov.reason_code == "SOURCE_AUTHORITY_CONFLICT"


def test_sa05_genuine_adapter_ws():
    """SA05: Genuine adapter WS / matching quote -> WS / GOOD eligible."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 1
    mock_adapter.symbol = "XAUUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.quote_source == "WS"
    assert prov.quote_valid is True
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality == "GOOD"


def test_sa06_missing_quote_source():
    """SA06: Missing quote source -> UNKNOWN."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = None
    mock_adapter.connection_epoch = 1
    mock_adapter.symbol = "XAUUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.quote_source == "UNKNOWN"


def test_sa07_wrong_symbol_quote():
    """SA07: Wrong-symbol quote -> SYMBOL_MISMATCH."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 1
    mock_adapter.symbol = "BTCUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.quote_valid is False
    assert "SYMBOL_MISMATCH" in str(prov.reason_code)


def test_sa08_cached_quote_after_disconnect():
    """SA08: Cached quote after adapter disconnect -> DISCONNECTED / not GOOD."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "DISCONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 1
    mock_adapter.symbol = "XAUUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.transport_state == "DISCONNECTED"
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality != "GOOD"


def test_sa09_manual_canonical_quote_without_adapter():
    """SA09: Manual CanonicalQuote cannot impersonate runtime feed without adapter."""
    t0 = 1700000000000
    manual_cq = CanonicalQuote(
        symbol="XAUUSDT",
        last=2650.25,
        bid=2650.0,
        ask=2650.5,
        exchange_ts_ms=t0 - 100,
        received_at_ms=t0 - 80,
        source="WS",
        connection_epoch=1,
        status="VALID",
    )

    prov = resolve_watcher_observation_provenance(
        raw_quote=manual_cq,
        validated_quote=None,
        feed_adapter_state=None,  # No adapter!
        evaluated_at_ms=t0,
    )
    assert prov.transport_state == "UNKNOWN"
    assert prov.quote_valid is False


def test_sa10_end_to_end_no_unauthorized_good(isolated_db):
    """SA10: End-to-end watcher execution rejects source conflict and writes no GOOD rows."""
    s = isolated_db["session"]
    t0 = 1700000000000

    orig_transport = getattr(bitget_ws_service, "transport_state", "CONNECTED")
    orig_source = getattr(bitget_ws_service, "feed_source", "WS")
    orig_epoch = getattr(bitget_ws_service, "connection_epoch", 1)
    try:
        bitget_ws_service.transport_state = "CONNECTED"
        bitget_ws_service.feed_source = "REST_FALLBACK"  # Adapter is in REST fallback!
        bitget_ws_service.connection_epoch = 1

        spoofed_quote = {
            "symbol": "XAUUSDT",
            "bid": 2650.0,
            "ask": 2650.5,
            "last": 2650.25,
            "exchange_ts_ms": t0 - 100,
            "received_at_ms": t0 - 80,
            "timestamp_ms": t0 - 100,
            "quote_source": "WS",  # Spoofed WS
            "transport_state": "CONNECTED",
            "connection_epoch": 1,
        }

        scalp_watcher_orchestrator.evaluate_cycle(
            db=s,
            symbol="XAUUSDT",
            now_ms=t0,
            quote=spoofed_quote,
            execution_scope="CG01",
        )
        observation_interval_service.flush_active_intervals(s)

        Session = isolated_db["Session"]
        s_probe = Session()
        try:
            good_rows = s_probe.query(models.PaperObservationInterval).filter(
                models.PaperObservationInterval.symbol == "XAUUSDT",
                models.PaperObservationInterval.data_quality == "GOOD",
            ).count()
            assert good_rows == 0, f"Violation: source conflict produced {good_rows} GOOD intervals!"
        finally:
            s_probe.close()
    finally:
        bitget_ws_service.transport_state = orig_transport
        bitget_ws_service.feed_source = orig_source
        bitget_ws_service.connection_epoch = orig_epoch
