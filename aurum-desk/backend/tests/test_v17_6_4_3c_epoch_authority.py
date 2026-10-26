"""
backend/tests/test_v17_6_4_3c_epoch_authority.py
Authoritative Test Suite for CPA-04:
Connection Epoch Authority and Epoch Mismatch Invariants.

Covers Test Cases (Section 05, Part 077):
EP01: Both epochs present and equal -> authoritative
EP02: Epoch mismatch -> CONNECTION_EPOCH_MISMATCH / not GOOD
EP03: Quote epoch missing -> CONNECTION_EPOCH_UNVERIFIED / not GOOD
EP04: Adapter epoch missing -> CONNECTION_EPOCH_UNVERIFIED / not GOOD
EP05: Both epochs missing -> CONNECTION_EPOCH_UNVERIFIED / not GOOD
EP06: Old quote after reconnect -> rejected
EP07: New quote after reconnect -> accepted
EP08: Manual dictionary spoofing epoch -> verified against adapter
EP09: Scheduler passes epoch without fabrication
EP10: Independent DB shows correct interval quality
"""
import pytest
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base
from database import run_schema_migrations
from services.scalp_watcher_orchestrator import (
    resolve_watcher_observation_provenance,
    scalp_watcher_orchestrator,
)
from services.observation_interval_service import observation_interval_service
from services.engine_observability_service import resolve_observation_evidence_quality
from services.bitget_ws_service import bitget_ws_service


@pytest.fixture
def isolated_db(tmp_path):
    path = tmp_path / "ep_test.sqlite"
    eng = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    run_schema_migrations(eng)
    Session = sessionmaker(bind=eng, autoflush=False)
    s = Session()
    try:
        yield {"session": s, "engine": eng, "Session": Session}
    finally:
        s.close()


def test_ep01_both_epochs_present_and_equal():
    """EP01: Both epochs present and equal -> authoritative."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 42
    mock_adapter.symbol = "XAUUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 42,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.is_authoritative_for_epoch is True
    assert prov.quote_valid is True
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality == "GOOD"


def test_ep02_epoch_mismatch():
    """EP02: Quote epoch 41 vs adapter epoch 42 -> CONNECTION_EPOCH_MISMATCH."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 42
    mock_adapter.symbol = "XAUUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 41,  # Prior connection epoch!
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.is_authoritative_for_epoch is False
    assert prov.quote_valid is False
    assert prov.reason_code == "CONNECTION_EPOCH_MISMATCH"
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality != "GOOD"


def test_ep03_quote_epoch_missing():
    """EP03: Quote epoch missing -> CONNECTION_EPOCH_UNVERIFIED / not authoritative."""
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
        # connection_epoch missing!
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.is_authoritative_for_epoch is False
    assert prov.quote_valid is False
    assert prov.reason_code == "CONNECTION_EPOCH_UNVERIFIED"


def test_ep04_adapter_epoch_missing():
    """EP04: Adapter epoch missing -> CONNECTION_EPOCH_UNVERIFIED."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = None  # Missing on adapter!
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
    assert prov.is_authoritative_for_epoch is False
    assert prov.quote_valid is False
    assert prov.reason_code == "CONNECTION_EPOCH_UNVERIFIED"


def test_ep05_both_epochs_missing():
    """EP05: Both epochs missing -> CONNECTION_EPOCH_UNVERIFIED."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = None
    mock_adapter.symbol = "XAUUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.is_authoritative_for_epoch is False
    assert prov.quote_valid is False


def test_ep06_old_quote_after_reconnect():
    """EP06: Disconnect advances epoch; old quote from previous epoch is rejected."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 3  # Advanced to epoch 3 on reconnect
    mock_adapter.symbol = "XAUUSDT"

    old_cached_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 2,  # Stale epoch 2!
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=old_cached_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.is_authoritative_for_epoch is False
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality != "GOOD"


def test_ep07_new_quote_after_reconnect():
    """EP07: New quote received under new epoch 3 is accepted."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 3
    mock_adapter.symbol = "XAUUSDT"

    new_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 3,  # Fresh epoch 3!
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=new_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.is_authoritative_for_epoch is True
    assert prov.quote_valid is True
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality == "GOOD"


def test_ep08_manual_dict_spoofing_epoch():
    """EP08: Manual dict with spoofed epoch != adapter epoch is rejected."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 5
    mock_adapter.symbol = "XAUUSDT"

    spoofed = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 999,  # Spoofed
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=spoofed,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.is_authoritative_for_epoch is False
    assert prov.reason_code == "CONNECTION_EPOCH_MISMATCH"


def test_ep09_scheduler_passes_epoch_without_fabrication():
    """EP09: capture_cg01_quote_snapshot preserves connection_epoch."""
    from services.cg01_scheduler_service import capture_cg01_quote_snapshot
    t0 = 1700000000000
    mock_quote = MagicMock()
    mock_quote.bid = 2650.0
    mock_quote.ask = 2650.5
    mock_quote.last = 2650.25
    mock_quote.exchange_ts_ms = t0 - 100
    mock_quote.received_at_ms = t0 - 80
    mock_quote.source = "WS"
    mock_quote.connection_epoch = 7
    mock_quote.status = "VALID"
    mock_quote.is_valid = True

    snap = capture_cg01_quote_snapshot(mock_quote, t0)
    assert snap["connection_epoch"] == 7


def test_ep10_independent_db_shows_correct_quality(isolated_db):
    """EP10: Watcher persists DEGRADED interval when epoch mismatch occurs."""
    s = isolated_db["session"]
    t0 = 1700000000000

    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "WS"
    bitget_ws_service.connection_epoch = 10

    mismatched_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "last": 2650.25,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "timestamp_ms": t0 - 100,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 9,  # Mismatch!
    }

    scalp_watcher_orchestrator.evaluate_cycle(
        db=s,
        symbol="XAUUSDT",
        now_ms=t0,
        quote=mismatched_quote,
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
        assert good_rows == 0, f"Violation: epoch mismatch produced {good_rows} GOOD intervals!"
    finally:
        s_probe.close()
