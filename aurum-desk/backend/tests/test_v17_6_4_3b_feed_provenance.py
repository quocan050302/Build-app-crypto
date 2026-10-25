"""
backend/tests/test_v17_6_4_3b_feed_provenance.py
Authoritative Integration and Unit Suite for V17.6.4.3b:
Canonical Feed Provenance, Anti-Fabrication Invariants, and Authority Verification.

Covers Test Matrix (Section D, Parts 051–060):
FP01: Missing exchange and receive timestamp
FP02: Missing quote source
FP03: Missing transport state
FP04: WS DISCONNECTED
FP05: WS RECONNECTING
FP06: WS DEGRADED
FP07: Old connection epoch
FP08: Wrong symbol
FP09: Invalid Bid
FP10: Invalid Ask
FP11: Stale quote
FP12: Future timestamp
FP13: Contradictory quote timestamps
FP14: ValidatedQuote lacks source authority
FP15: REST fallback must not be labeled WS
FP16: Cached quote after disconnect
FP17: Process heartbeat without feed evidence
FP18: Caller-supplied fake GOOD flags
FP19: No fabricated last_quote_at_ms
FP20: No fabricated CONNECTED
FP21: Genuine fresh WS canonical quote (positive control)
FP22: Verified current connection epoch
FP23: Correct receive timestamp preserved separately
FP24: Verified source preserved through normalization
FP25: Reconnect followed by new current-epoch quote
FP26: Proper interval GOOD after authoritative verification
"""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base
from database import run_schema_migrations
from services.quote_validator import (
    CanonicalQuote,
    ValidatedQuote,
    normalize_execution_quote,
)
from services.bitget_ws_service import bitget_ws_service
from services.engine_observability_service import (
    engine_observability_service,
    resolve_observation_evidence_quality,
)
from services.observation_interval_service import observation_interval_service
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator


@pytest.fixture
def isolated_dbs(tmp_path):
    path_a = tmp_path / "feed_prov_a.sqlite"
    eng_a = create_engine(f"sqlite:///{path_a.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng_a)
    run_schema_migrations(eng_a)
    s_a = sessionmaker(bind=eng_a, autoflush=False)()

    # Save original adapter state
    orig_transport = bitget_ws_service.transport_state
    orig_source = bitget_ws_service.feed_source
    orig_epoch = bitget_ws_service.connection_epoch
    orig_quote = bitget_ws_service.latest_quote

    try:
        yield {"session": s_a, "engine": eng_a}
    finally:
        s_a.close()
        # Restore adapter state
        bitget_ws_service.transport_state = orig_transport
        bitget_ws_service.feed_source = orig_source
        bitget_ws_service.connection_epoch = orig_epoch
        bitget_ws_service.latest_quote = orig_quote


# -------------------------------------------------------------------------
# FP01–FP20: Negative Invariant Tests (Anti-Fabrication)
# -------------------------------------------------------------------------

def test_fp01_missing_exchange_and_receive_timestamp(isolated_dbs):
    """FP01: Missing exchange and receive timestamp must fail closed, not default to now_ms."""
    session = isolated_dbs["session"]
    t_eval = 1700000000000

    quote_no_ts = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "last": 2650.25,
        "is_valid": True,
        "quote_source": "WS",
        # Intentionally missing timestamp_ms, exchange_ts_ms, server_time, observed_at
    }

    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.connection_epoch = 1

    # In V17.6.4.3b, resolve_watcher_observation_provenance or evaluate_cycle must not synthesize q_ts = t_eval
    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote_no_ts,
        validated_quote=normalize_execution_quote(quote_no_ts, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )

    assert prov.exchange_ts_ms is None or prov.reason_code == "QUOTE_TIMESTAMP_UNVERIFIED", \
        f"Vulnerability: timestamp synthesized: {prov.exchange_ts_ms}"

    qual = resolve_observation_evidence_quality(
        symbol="XAUUSDT",
        feed=prov.to_snapshot_dict(),
        t_now=t_eval,
    )
    assert qual.quality != "GOOD", f"Missing timestamp was authorized as GOOD: {qual}"


def test_fp02_missing_quote_source(isolated_dbs):
    """FP02: Missing quote source must not be defaulted to WS."""
    t_eval = 1700000000000
    quote_no_src = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "timestamp_ms": t_eval - 100,
        "exchange_ts_ms": t_eval - 100,
        "received_at_ms": t_eval - 80,
        # quote_source is absent
    }
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.feed_source = "UNKNOWN"

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote_no_src,
        validated_quote=normalize_execution_quote(quote_no_src, now_ms=t_eval),
        feed_adapter_state=None,  # No trusted adapter override
        evaluated_at_ms=t_eval,
    )
    assert prov.quote_source in ("UNKNOWN", "UNVERIFIED"), \
        f"Vulnerability: missing source defaulted to {prov.quote_source}"


def test_fp03_missing_transport_state():
    """FP03: Missing transport state must not be defaulted to CONNECTED."""
    t_eval = 1700000000000
    quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t_eval - 100,
        "received_at_ms": t_eval - 80,
        "quote_source": "WS",
    }
    bitget_ws_service.transport_state = None  # Missing

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote,
        validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    assert prov.transport_state in ("UNKNOWN", "TRANSPORT_UNKNOWN"), \
        f"Vulnerability: missing transport defaulted to {prov.transport_state}"


def test_fp04_ws_disconnected_fails_closed(isolated_dbs):
    """FP04: WS DISCONNECTED must never be coerced to CONNECTED."""
    session = isolated_dbs["session"]
    t_eval = 1700000000000

    quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t_eval - 100,
        "received_at_ms": t_eval - 80,
        "timestamp_ms": t_eval - 100,
        "is_valid": True,
        "quote_source": "WS",
    }
    bitget_ws_service.transport_state = "DISCONNECTED"

    res = scalp_watcher_orchestrator.evaluate_cycle(
        db=session,
        symbol="XAUUSDT",
        now_ms=t_eval,
        quote=quote,
        execution_scope="CG01",
    )
    observation_interval_service.flush_active_intervals(session)

    # Must NOT record a GOOD interval
    good_count = session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.data_quality == "GOOD"
    ).count()
    assert good_count == 0, "Vulnerability: DISCONNECTED transport authorized GOOD interval"


def test_fp05_ws_reconnecting():
    """FP05: WS RECONNECTING must preserve RECONNECTING and not become CONNECTED."""
    t_eval = 1700000000000
    quote = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.5, "quote_source": "WS", "exchange_ts_ms": t_eval - 100}
    bitget_ws_service.transport_state = "RECONNECTING"

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote,
        validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    assert prov.transport_state == "RECONNECTING"
    qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=prov.to_snapshot_dict(), t_now=t_eval)
    assert qual.quality != "GOOD"


def test_fp06_ws_degraded():
    """FP06: WS DEGRADED transport state preserved as DEGRADED."""
    t_eval = 1700000000000
    quote = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.5, "quote_source": "WS", "exchange_ts_ms": t_eval - 100}
    bitget_ws_service.transport_state = "DEGRADED"

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote,
        validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    assert prov.transport_state == "DEGRADED"


def test_fp07_old_connection_epoch_rejected():
    """FP07: Quote from old connection epoch must not authorize GOOD on new epoch."""
    t_eval = 1700000000000
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.connection_epoch = 9  # Current socket epoch is 9

    old_quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.5,
        last=2650.25,
        exchange_ts_ms=t_eval - 50,
        received_at_ms=t_eval - 40,
        source="WS",
        connection_epoch=8,  # Old epoch from prior connection
        status="VALID",
    )

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=old_quote,
        validated_quote=normalize_execution_quote(old_quote.to_dict(), now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    assert prov.is_authoritative_for_epoch is False or prov.reason_code == "CONNECTION_EPOCH_MISMATCH"
    snap = prov.to_snapshot_dict()
    qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=snap, t_now=t_eval)
    assert qual.quality != "GOOD", f"Old epoch quote was authorized as GOOD: {qual}"


def test_fp08_wrong_symbol():
    """FP08: Wrong symbol fails closed."""
    t_eval = 1700000000000
    quote = {"symbol": "BTCUSDT", "bid": 65000.0, "ask": 65001.0, "exchange_ts_ms": t_eval - 100, "quote_source": "WS"}
    bitget_ws_service.transport_state = "CONNECTED"

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote,
        validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=prov.to_snapshot_dict(), t_now=t_eval)
    assert qual.quality == "UNVERIFIED"
    assert "SYMBOL_MISMATCH" in qual.reason_code


def test_fp09_invalid_bid():
    """FP09: Invalid Bid (<= 0 or NaN) fails closed."""
    t_eval = 1700000000000
    quote = {"symbol": "XAUUSDT", "bid": 0.0, "ask": 2650.0, "exchange_ts_ms": t_eval - 100, "quote_source": "WS"}
    bitget_ws_service.transport_state = "CONNECTED"

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote,
        validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=prov.to_snapshot_dict(), t_now=t_eval)
    assert qual.quality != "GOOD"


def test_fp10_invalid_ask():
    """FP10: Invalid Ask (< Bid) fails closed."""
    t_eval = 1700000000000
    quote = {"symbol": "XAUUSDT", "bid": 2650.5, "ask": 2650.0, "exchange_ts_ms": t_eval - 100, "quote_source": "WS"}
    bitget_ws_service.transport_state = "CONNECTED"

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote,
        validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=prov.to_snapshot_dict(), t_now=t_eval)
    assert qual.quality != "GOOD"


def test_fp11_stale_quote():
    """FP11: Stale quote (> 15s age) fails closed."""
    t_eval = 1700000000000
    quote = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.5, "exchange_ts_ms": t_eval - 25_000, "quote_source": "WS"}
    bitget_ws_service.transport_state = "CONNECTED"

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote,
        validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=prov.to_snapshot_dict(), t_now=t_eval)
    assert qual.quality in ("DEGRADED", "STALE")


def test_fp12_future_timestamp():
    """FP12: Future timestamp (> 5s ahead) fails closed."""
    t_eval = 1700000000000
    quote = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.5, "exchange_ts_ms": t_eval + 15_000, "quote_source": "WS"}
    bitget_ws_service.transport_state = "CONNECTED"

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote,
        validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=prov.to_snapshot_dict(), t_now=t_eval)
    assert qual.quality != "GOOD"


def test_fp13_contradictory_quote_timestamps():
    """FP13: Contradictory quote timestamps fail closed."""
    t_eval = 1700000000000
    # Exchange time is vastly later than receive time
    quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t_eval + 30_000,
        "received_at_ms": t_eval - 100,
        "quote_source": "WS",
    }
    bitget_ws_service.transport_state = "CONNECTED"

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote,
        validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=prov.to_snapshot_dict(), t_now=t_eval)
    assert qual.quality != "GOOD"


def test_fp14_validated_quote_lacks_source_authority():
    """FP14: ValidatedQuote alone cannot manufacture WS source or CONNECTED state."""
    t_eval = 1700000000000
    vq = ValidatedQuote(
        is_valid=True,
        bid=2650.0,
        ask=2650.5,
        last=2650.25,
        spread=0.5,
        exchange_time=t_eval - 50,
        observed_at=t_eval - 40,
        freshness_sec=0.05,
        is_stale=False,
        rejection_code=None,
        rejection_detail=None,
    )
    # feed_adapter_state is None
    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=vq,
        validated_quote=vq,
        feed_adapter_state=None,
        evaluated_at_ms=t_eval,
    )
    assert prov.quote_source in ("UNKNOWN", "UNVERIFIED")
    assert prov.transport_state in ("UNKNOWN", "TRANSPORT_UNKNOWN")


def test_fp15_rest_fallback_must_not_be_labeled_ws():
    """FP15: REST fallback must not be labeled WS, evaluating to DEGRADED."""
    t_eval = 1700000000000
    orig_transport = getattr(bitget_ws_service, "transport_state", "CONNECTED")
    orig_source = getattr(bitget_ws_service, "feed_source", "WS")
    try:
        bitget_ws_service.transport_state = "DISCONNECTED"
        bitget_ws_service.feed_source = "REST_FALLBACK"

        quote = {
            "symbol": "XAUUSDT",
            "bid": 2650.0,
            "ask": 2650.5,
            "exchange_ts_ms": t_eval - 500,
            "received_at_ms": t_eval - 400,
            "quote_source": "REST_FALLBACK",
            "freshness_state": "CONNECTED_FRESH",
        }

        from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
        prov = resolve_watcher_observation_provenance(
            raw_quote=quote,
            validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
            feed_adapter_state=bitget_ws_service,
            evaluated_at_ms=t_eval,
        )
        assert prov.quote_source == "REST_FALLBACK"
        qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=prov.to_snapshot_dict(), t_now=t_eval)
        assert qual.quality == "DEGRADED"
    finally:
        bitget_ws_service.transport_state = orig_transport
        bitget_ws_service.feed_source = orig_source


def test_fp16_cached_quote_after_disconnect(isolated_dbs):
    """FP16: Cached valid quote after disconnect cannot produce GOOD interval."""
    session = isolated_dbs["session"]
    t_eval = 1700000000000

    # Old valid quote cached in service
    bitget_ws_service.transport_state = "DISCONNECTED"
    cached_quote = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.5,
        "exchange_ts_ms": t_eval - 200,
        "received_at_ms": t_eval - 150,
        "quote_source": "WS",
        "is_valid": True,
    }

    scalp_watcher_orchestrator.evaluate_cycle(
        db=session,
        symbol="XAUUSDT",
        now_ms=t_eval,
        quote=cached_quote,
        execution_scope="CG01",
    )
    observation_interval_service.flush_active_intervals(session)

    good_count = session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.data_quality == "GOOD"
    ).count()
    assert good_count == 0


def test_fp17_process_heartbeat_without_feed_evidence(isolated_dbs):
    """FP17: Process heartbeat without feed evidence records RUNNING process but NOT GOOD coverage."""
    session = isolated_dbs["session"]
    t_eval = 1700000000000

    bitget_ws_service.transport_state = "DISCONNECTED"

    scalp_watcher_orchestrator.evaluate_cycle(
        db=session,
        symbol="XAUUSDT",
        now_ms=t_eval,
        quote=None,
        execution_scope="CG01",
    )
    observation_interval_service.flush_active_intervals(session)

    # Intervals must NOT be GOOD
    good_count = session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.data_quality == "GOOD"
    ).count()
    assert good_count == 0


def test_fp18_caller_supplied_fake_good_flags(isolated_dbs):
    """FP18: Caller supplying fake GOOD metadata without valid quote fails closed."""
    session = isolated_dbs["session"]
    t0 = 1700000000000

    engine_observability_service.record_engine_heartbeat(
        session,
        run_id="RUN_FP18",
        symbol="XAUUSDT",
        heartbeat_at_ms=t0,
        metadata={
            "coverage_quality": "GOOD",
            "is_feed_healthy": True,
            "transport_state": "CONNECTED",
            "canonical_quote": None,  # Missing real quote
        },
    )
    observation_interval_service.flush_active_intervals(session)

    good_count = session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.run_id == "RUN_FP18",
        models.PaperObservationInterval.data_quality == "GOOD",
    ).count()
    assert good_count == 0


def test_fp19_no_fabricated_last_quote_at_ms():
    """FP19: Missing exchange timestamp in quote must not be replaced with now_ms."""
    t_eval = 1700000000000
    quote = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.5, "quote_source": "WS"}

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=quote,
        validated_quote=normalize_execution_quote(quote, now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    assert prov.exchange_ts_ms != t_eval, "Vulnerability: exchange timestamp fabricated with t_eval"


def test_fp20_no_fabricated_connected():
    """FP20: DISCONNECTED transport must not become CONNECTED through normalization."""
    t_eval = 1700000000000
    bitget_ws_service.transport_state = "DISCONNECTED"

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote={"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.5},
        validated_quote=None,
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    assert prov.transport_state == "DISCONNECTED"


# -------------------------------------------------------------------------
# FP21–FP26: Positive Verification Tests (True Canonical GOOD)
# -------------------------------------------------------------------------

def test_fp21_genuine_fresh_ws_canonical_quote():
    """FP21: Genuine fresh WS canonical quote produces verified GOOD quality."""
    t_eval = 1700000000000
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.connection_epoch = 5

    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.5,
        last=2650.25,
        exchange_ts_ms=t_eval - 50,
        received_at_ms=t_eval - 40,
        source="WS",
        connection_epoch=5,
        status="VALID",
        freshness_sec=0.05,
    )

    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=cq,
        validated_quote=normalize_execution_quote(cq.to_dict(), now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    snap = prov.to_snapshot_dict()
    qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=snap, t_now=t_eval)
    assert qual.quality == "GOOD", f"Expected GOOD quality, got: {qual}"


def test_fp22_verified_current_connection_epoch():
    """FP22: Epoch matching between quote and adapter validates authority."""
    t_eval = 1700000000000
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.connection_epoch = 3

    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.5,
        last=2650.25,
        exchange_ts_ms=t_eval - 50,
        received_at_ms=t_eval - 40,
        source="WS",
        connection_epoch=3,
        status="VALID",
    )
    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=cq,
        validated_quote=normalize_execution_quote(cq.to_dict(), now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    assert prov.connection_epoch == 3
    assert prov.is_authoritative_for_epoch is True


def test_fp23_correct_receive_timestamp_preserved_separately():
    """FP23: exchange_ts_ms, received_at_ms, and evaluated_at_ms remain 3 distinct values."""
    t_eval = 1700000000000
    t_exch = t_eval - 120
    t_recv = t_eval - 45

    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.5,
        last=2650.25,
        exchange_ts_ms=t_exch,
        received_at_ms=t_recv,
        source="WS",
        connection_epoch=1,
        status="VALID",
    )
    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=cq,
        validated_quote=normalize_execution_quote(cq.to_dict(), now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    assert prov.exchange_ts_ms == t_exch
    assert prov.received_at_ms == t_recv
    assert prov.evaluated_at_ms == t_eval
    assert t_exch != t_recv != t_eval


def test_fp24_verified_source_preserved_through_normalization():
    """FP24: CanonicalQuote source WS is preserved through normalization."""
    t_eval = 1700000000000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.5,
        last=2650.25,
        exchange_ts_ms=t_eval - 50,
        received_at_ms=t_eval - 40,
        source="WS",
        connection_epoch=1,
        status="VALID",
    )
    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=cq,
        validated_quote=normalize_execution_quote(cq.to_dict(), now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    assert prov.quote_source == "WS"


def test_fp25_reconnect_followed_by_new_current_epoch_quote():
    """FP25: Reconnect followed by new current-epoch quote restores GOOD quality."""
    t_eval = 1700000000000
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.connection_epoch = 10  # Post-reconnect epoch

    cq_new = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.5,
        last=2650.25,
        exchange_ts_ms=t_eval - 20,
        received_at_ms=t_eval - 10,
        source="WS",
        connection_epoch=10,
        status="VALID",
    )
    from services.scalp_watcher_orchestrator import resolve_watcher_observation_provenance
    prov = resolve_watcher_observation_provenance(
        raw_quote=cq_new,
        validated_quote=normalize_execution_quote(cq_new.to_dict(), now_ms=t_eval),
        feed_adapter_state=bitget_ws_service,
        evaluated_at_ms=t_eval,
    )
    snap = prov.to_snapshot_dict()
    qual = resolve_observation_evidence_quality(symbol="XAUUSDT", feed=snap, t_now=t_eval)
    assert qual.quality == "GOOD"


def test_fp26_proper_interval_good_after_authoritative_verification(isolated_dbs):
    """FP26: Proper interval GOOD after authoritative verification in evaluate_cycle."""
    session = isolated_dbs["session"]
    t0 = 1700000000000
    bitget_ws_service.transport_state = "CONNECTED"
    bitget_ws_service.connection_epoch = 1

    cq1 = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2650.0,
        ask=2650.5,
        last=2650.25,
        exchange_ts_ms=t0 - 50,
        received_at_ms=t0 - 40,
        source="WS",
        connection_epoch=1,
        status="VALID",
    )
    cq2 = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2650.1,
        ask=2650.6,
        last=2650.35,
        exchange_ts_ms=t0 + 5000 - 50,
        received_at_ms=t0 + 5000 - 40,
        source="WS",
        connection_epoch=1,
        status="VALID",
    )

    scalp_watcher_orchestrator.evaluate_cycle(
        db=session,
        symbol="XAUUSDT",
        now_ms=t0,
        quote=cq1,
        execution_scope="CG01",
    )
    scalp_watcher_orchestrator.evaluate_cycle(
        db=session,
        symbol="XAUUSDT",
        now_ms=t0 + 5000,
        quote=cq2,
        execution_scope="CG01",
    )

    observation_interval_service.flush_active_intervals(session)

    good_intervals = session.query(models.PaperObservationInterval).filter(
        models.PaperObservationInterval.data_quality == "GOOD"
    ).all()
    assert len(good_intervals) >= 1, "Expected at least 1 durable GOOD interval for authoritative quote stream"
