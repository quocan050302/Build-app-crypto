"""
backend/tests/test_v17_6_4_3c_transport_authority.py
Authoritative Test Suite for CPA-01:
Trusted Transport Authority and Transport Conflict Invariants.

Covers Test Cases (Section 02, Part 038):
TA01: Adapter DISCONNECTED / raw quote claims CONNECTED -> DISCONNECTED / TRANSPORT_AUTHORITY_CONFLICT
TA02: Adapter RECONNECTING / raw quote claims CONNECTED -> RECONNECTING / TRANSPORT_AUTHORITY_CONFLICT
TA03: Adapter UNKNOWN / raw quote claims CONNECTED -> UNKNOWN / not GOOD
TA04: Adapter CONNECTED / raw quote claims DISCONNECTED -> DISCONNECTED / conflict
TA05: Adapter CONNECTED / valid matching quote -> CONNECTED / GOOD eligible
TA06: Adapter absent / raw quote claims CONNECTED -> UNKNOWN / ADAPTER_STATE_UNAVAILABLE
TA07: Adapter symbol mismatch -> SYMBOL_MISMATCH
TA08: Adapter stale connection state -> stale / degraded
TA09: Cached old-epoch quote -> CONNECTION_EPOCH_MISMATCH / not GOOD
TA10: REST fallback under disconnected WS -> REST_FALLBACK / DEGRADED
TA11: Process RUNNING but feed DOWN -> DOWN / not HEALTHY
TA12: Actual watcher evaluate_cycle persists NO GOOD interval on transport conflict
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
    resolve_authoritative_transport_state,
    scalp_watcher_orchestrator,
)
from services.observation_interval_service import observation_interval_service
from services.engine_observability_service import (
    resolve_observation_evidence_quality,
    engine_observability_service,
)
from services.bitget_ws_service import bitget_ws_service


@pytest.fixture
def isolated_db(tmp_path):
    path = tmp_path / "ta_test.sqlite"
    eng = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    run_schema_migrations(eng)
    Session = sessionmaker(bind=eng, autoflush=False)
    s = Session()
    try:
        yield {"session": s, "engine": eng, "Session": Session}
    finally:
        s.close()


def test_ta01_adapter_disconnected_raw_connected():
    """TA01: Adapter DISCONNECTED / raw quote claims CONNECTED -> effective DISCONNECTED and conflict."""
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
        "transport_state": "CONNECTED",  # Spoofed
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.transport_state == "DISCONNECTED"
    assert prov.quote_valid is False
    assert prov.reason_code == "TRANSPORT_AUTHORITY_CONFLICT"

    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality != "GOOD"


def test_ta02_adapter_reconnecting_raw_connected():
    """TA02: Adapter RECONNECTING / raw quote claims CONNECTED -> effective RECONNECTING."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "RECONNECTING"
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
        "transport_state": "CONNECTED",  # Spoofed
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.transport_state == "RECONNECTING"
    assert prov.quote_valid is False

    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality != "GOOD"


def test_ta03_adapter_unknown_raw_connected():
    """TA03: Adapter UNKNOWN / raw quote claims CONNECTED -> effective UNKNOWN."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "UNKNOWN"
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
    assert prov.transport_state == "UNKNOWN"
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality == "UNVERIFIED"


def test_ta04_adapter_connected_raw_disconnected():
    """TA04: Adapter CONNECTED / raw quote claims DISCONNECTED -> conflict / not valid."""
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
        "transport_state": "DISCONNECTED",
        "connection_epoch": 1,
    }

    prov = resolve_watcher_observation_provenance(
        raw_quote=raw_quote,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    assert prov.transport_state == "DISCONNECTED"
    assert prov.quote_valid is False
    assert prov.reason_code == "TRANSPORT_AUTHORITY_CONFLICT"


def test_ta05_adapter_connected_valid_matching_quote():
    """TA05: Adapter CONNECTED / valid matching quote -> CONNECTED / GOOD eligible."""
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
    assert prov.transport_state == "CONNECTED"
    assert prov.quote_valid is True
    assert prov.quote_source == "WS"
    assert prov.is_authoritative_for_epoch is True

    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality == "GOOD"


def test_ta06_adapter_absent_raw_connected():
    """TA06: Adapter absent / raw quote claims CONNECTED -> UNKNOWN."""
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
    assert prov.transport_state == "UNKNOWN"
    assert prov.quote_valid is False
    assert prov.reason_code == "ADAPTER_STATE_UNAVAILABLE"


def test_ta07_adapter_symbol_mismatch():
    """TA07: Adapter symbol mismatch -> SYMBOL_MISMATCH."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 1
    mock_adapter.symbol = "ETHUSDT"

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


def test_ta08_adapter_stale_connection_state():
    """TA08: Stale quote under connected adapter -> stale / not GOOD."""
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
        "exchange_ts_ms": t0 - 30_000,  # 30s old
        "received_at_ms": t0 - 29_000,
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
    assert qual.quality == "DEGRADED"


def test_ta09_cached_old_epoch_quote():
    """TA09: Quote from epoch 1 when adapter is at epoch 2 -> CONNECTION_EPOCH_MISMATCH."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "CONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 2
    mock_adapter.symbol = "XAUUSDT"

    raw_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "quote_source": "WS",
        "transport_state": "CONNECTED",
        "connection_epoch": 1,  # Old epoch!
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


def test_ta10_rest_fallback_under_disconnected_ws():
    """TA10: REST fallback under disconnected WS -> REST_FALLBACK / DEGRADED."""
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
        "quote_source": "REST_FALLBACK",
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
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality == "DEGRADED"


def test_ta11_process_running_feed_down():
    """TA11: Process RUNNING but feed DOWN -> evidence health DOWN."""
    t0 = 1700000000000
    mock_adapter = MagicMock()
    mock_adapter.transport_state = "DISCONNECTED"
    mock_adapter.feed_source = "WS"
    mock_adapter.connection_epoch = 1
    mock_adapter.symbol = "XAUUSDT"

    prov = resolve_watcher_observation_provenance(
        raw_quote=None,
        validated_quote=None,
        feed_adapter_state=mock_adapter,
        evaluated_at_ms=t0,
    )
    qual = resolve_observation_evidence_quality(feed=prov.to_snapshot_dict(), t_now=t0)
    assert qual.quality == "DOWN"


def test_ta12_actual_watcher_persists_no_good_on_conflict(isolated_db):
    """TA12: Actual watcher evaluate_cycle persists NO GOOD interval on transport conflict."""
    s = isolated_db["session"]
    t0 = 1700000000000

    # Real adapter is DISCONNECTED
    bitget_ws_service.transport_state = "DISCONNECTED"
    bitget_ws_service.connection_epoch = 1
    bitget_ws_service.feed_source = "WS"

    spoofed_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "last": 2650.25,
        "exchange_ts_ms": t0 - 100,
        "received_at_ms": t0 - 80,
        "timestamp_ms": t0 - 100,
        "quote_source": "WS",
        "transport_state": "CONNECTED",  # Conflict!
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

    # Open independent session to verify NO GOOD row was persisted
    Session = isolated_db["Session"]
    s_probe = Session()
    try:
        good_rows = s_probe.query(models.PaperObservationInterval).filter(
            models.PaperObservationInterval.symbol == "XAUUSDT",
            models.PaperObservationInterval.data_quality == "GOOD",
        ).count()
        assert good_rows == 0, f"Violation: transport conflict produced {good_rows} GOOD intervals!"
    finally:
        s_probe.close()
