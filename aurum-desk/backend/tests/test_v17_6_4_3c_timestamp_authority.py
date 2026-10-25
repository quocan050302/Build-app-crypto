"""
backend/tests/test_v17_6_4_3c_timestamp_authority.py
Authoritative Test Suite for CPA-03:
Exchange vs Receive Timestamp Policy and Non-Fabrication Invariants.

Covers Test Cases (Section 04, Part 068):
TS01: Exchange missing / receive fresh -> EXCHANGE_TIMESTAMP_UNVERIFIED / not GOOD
TS02: Exchange fresh / receive missing -> RECEIVE_TIMESTAMP_UNVERIFIED / not GOOD
TS03: Both missing -> QUOTE_TIMESTAMP_UNVERIFIED
TS04: Exchange stale / receive fresh -> QUOTE_STALE / not GOOD
TS05: Exchange fresh / receive stale -> QUOTE_STALE / not GOOD
TS06: Exchange future -> QUOTE_FUTURE_CLOCK_SKEW
TS07: Receive future -> QUOTE_FUTURE_CLOCK_SKEW
TS08: Contradictory ordering (receive << exchange) -> QUOTE_TIMESTAMP_CONTRADICTORY
TS09: Nonfinite timestamp -> INVALID_TIMESTAMP
TS10: Invalid string timestamp -> INVALID_TIMESTAMP
TS11: Wrong units seconds vs milliseconds -> TIMESTAMP_UNIT_INVALID
TS12: Quote cached after disconnect -> not GOOD
TS13: Replay clock consistency
TS14: Current-epoch fresh timestamps -> GOOD
TS15: Real watcher persisted interval readback in independent session
TS16: Scheduler propagation
"""
import math
import pytest
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base
from database import run_schema_migrations
from services.scalp_watcher_orchestrator import (
    resolve_watcher_observation_provenance,
    validate_observation_timestamp_authority,
    scalp_watcher_orchestrator,
)
from services.observation_interval_service import observation_interval_service
from services.engine_observability_service import resolve_observation_evidence_quality
from services.bitget_ws_service import bitget_ws_service


@pytest.fixture
def isolated_db(tmp_path):
    path = tmp_path / "ts_test.sqlite"
    eng = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    run_schema_migrations(eng)
    Session = sessionmaker(bind=eng, autoflush=False)
    s = Session()
    try:
        yield {"session": s, "engine": eng, "Session": Session}
    finally:
        s.close()


def test_ts01_exchange_missing_receive_fresh():
    """TS01: Exchange timestamp missing / receive fresh -> EXCHANGE_TIMESTAMP_UNVERIFIED."""
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
        "received_at_ms": t0 - 50,
        # exchange_ts_ms missing!
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
    assert prov.exchange_ts_ms is None
    assert prov.quote_valid is False
    assert prov.reason_code == "EXCHANGE_TIMESTAMP_UNVERIFIED"

    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality != "GOOD"


def test_ts02_exchange_fresh_receive_missing():
    """TS02: Exchange fresh / receive missing -> RECEIVE_TIMESTAMP_UNVERIFIED."""
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
        # received_at_ms missing!
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
    assert prov.received_at_ms is None
    assert prov.quote_valid is False
    assert prov.reason_code == "RECEIVE_TIMESTAMP_UNVERIFIED"

    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality != "GOOD"


def test_ts03_both_missing():
    """TS03: Both missing -> QUOTE_TIMESTAMP_UNVERIFIED."""
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
    assert prov.reason_code == "QUOTE_TIMESTAMP_UNVERIFIED"


def test_ts04_exchange_stale_receive_fresh():
    """TS04: Exchange timestamp 25s old, receive fresh -> QUOTE_STALE / not GOOD."""
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
        "exchange_ts_ms": t0 - 25_000,  # 25s old
        "received_at_ms": t0 - 100,     # Fresh receive!
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
    assert prov.quote_stale is True
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality != "GOOD"


def test_ts05_exchange_fresh_receive_stale():
    """TS05: Exchange fresh, receive 20s old -> QUOTE_STALE / not GOOD."""
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
        "received_at_ms": t0 - 20_000,  # Stale receive
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
    assert prov.quote_valid is False or prov.quote_stale is True
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality != "GOOD"


def test_ts06_exchange_future():
    """TS06: Exchange timestamp in future (> +5s) -> QUOTE_FUTURE_CLOCK_SKEW."""
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
        "exchange_ts_ms": t0 + 10_000,  # +10s future
        "received_at_ms": t0 - 50,
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
    assert prov.reason_code == "QUOTE_FUTURE_CLOCK_SKEW"


def test_ts07_receive_future():
    """TS07: Receive timestamp in future (> +5s) -> QUOTE_FUTURE_CLOCK_SKEW."""
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
        "received_at_ms": t0 + 8_000,  # +8s future
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
    assert prov.reason_code == "QUOTE_FUTURE_CLOCK_SKEW"


def test_ts08_contradictory_ordering():
    """TS08: Receive timestamp earlier than exchange timestamp by > 1s -> QUOTE_TIMESTAMP_CONTRADICTORY."""
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
        "received_at_ms": t0 - 5_000,  # Received 5 seconds before it was supposedly generated!
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
    assert prov.reason_code == "QUOTE_TIMESTAMP_CONTRADICTORY"


def test_ts09_nonfinite_timestamp():
    """TS09: Nonfinite timestamp -> INVALID_EXCHANGE_TIMESTAMP."""
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
        "exchange_ts_ms": float("nan"),
        "received_at_ms": t0 - 50,
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
    assert prov.reason_code == "INVALID_EXCHANGE_TIMESTAMP"


def test_ts10_invalid_string_timestamp():
    """TS10: Invalid string timestamp -> INVALID_EXCHANGE_TIMESTAMP."""
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
        "exchange_ts_ms": "not_a_number",
        "received_at_ms": t0 - 50,
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
    assert prov.reason_code == "INVALID_EXCHANGE_TIMESTAMP"


def test_ts11_wrong_units_seconds():
    """TS11: Timestamp in seconds instead of milliseconds -> TIMESTAMP_UNIT_INVALID."""
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
        "exchange_ts_ms": 1700000000,  # Seconds (< 10_000_000_000)
        "received_at_ms": 1700000000,
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
    assert prov.reason_code == "TIMESTAMP_UNIT_INVALID"


def test_ts12_quote_cached_after_disconnect():
    """TS12: Quote cached after disconnect -> not GOOD."""
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
        "transport_state": "DISCONNECTED",
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality != "GOOD"


def test_ts13_replay_clock_consistency():
    """TS13: Replay clock consistency."""
    t_replay = 1680000000000
    res = validate_observation_timestamp_authority(
        exchange_ts_ms=t_replay - 100,
        received_at_ms=t_replay - 80,
        evaluated_at_ms=t_replay,
    )
    assert res.is_valid is True
    assert res.is_stale is False


def test_ts14_current_epoch_fresh_timestamps():
    """TS14: Current-epoch fresh timestamps -> GOOD."""
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
    assert prov.quote_valid is True
    assert prov.quote_stale is False
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality == "GOOD"


def test_ts15_real_watcher_persisted_interval_readback(isolated_db):
    """TS15: Real watcher persisted interval readback in independent session."""
    s = isolated_db["session"]
    t0 = 1700000000000

    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 1

    valid_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "last": 2650.25,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "timestamp_ms": t0 - 100,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,
    }

    # Two cycles to extend and flush interval
    scalp_watcher_orchestrator.evaluate_cycle(
        db=s,
        symbol="XAUUSDT",
        now_ms=t0,
        quote=valid_quote,
        execution_scope="CG01",
    )
    valid_quote2 = dict(valid_quote)
    valid_quote2["exchange_ts_ms"] = t0 + 5000
    valid_quote2["received_at_ms"] = t0 + 5010
    scalp_watcher_orchestrator.evaluate_cycle(
        db=s,
        symbol="XAUUSDT",
        now_ms=t0 + 5000,
        quote=valid_quote2,
        execution_scope="CG01",
    )
    observation_interval_service.flush_active_intervals(s)

    Session = isolated_db["Session"]
    s_probe = Session()
    try:
        good_rows = s_probe.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.symbol == "XAUUSDT",
            models.PaperObservationInterval.data_quality == "GOOD",
        ).all()
        assert len(good_rows) >= 1
    finally:
        s_probe.close()


def test_ts16_scheduler_propagation():
    """TS16: Scheduler captures quote snapshot with validated timestamps."""
    from services.cg01_scheduler_service import capture_cg01_quote_snapshot
    t0 = 1700000000000
    mock_quote = MagicMock()
    mock_quote.bid = 2650.0
    mock_quote.ask = 2650.5
    mock_quote.last = 2650.25
    mock_quote.exchange_ts_ms = t0 - 100
    mock_quote.received_at_ms = t0 - 80
    mock_quote.source = "WS"
    mock_quote.connection_epoch = 1
    mock_quote.status = "VALID"
    mock_quote.is_valid = True

    snap = capture_cg01_quote_snapshot(mock_quote, t0)
    assert snap["bid"] == 2650.0
    assert snap["ask"] == 2650.5
    assert snap["is_quote_valid"] is True
