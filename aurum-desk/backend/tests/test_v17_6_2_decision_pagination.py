"""
backend/tests/test_v17_6_2_decision_pagination.py
AURUM V17.6.2 — Decision Pagination & Symbol Provenance (FIX-09).

Verifies:
- PG01: Valid cursor pagination traverses events correctly.
- PG02: Corrupted base64 cursor raises HTTP 422.
- PG03: Non-positive / invalid timestamp raises HTTP 422.
- PG04: Run_id mismatch between request and cursor raises HTTP 422.
- PG05: Symbol provenance filtering correctly segregates symbols.
"""
import base64
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.engine_observability_service import (
    decode_observation_cursor,
    engine_observability_service,
)


@pytest.fixture
def decision_db(tmp_path):
    db_path = tmp_path / "dec_test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    models.Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_pg01_valid_cursor_pagination(decision_db):
    session = decision_db
    for i in range(15):
        engine_observability_service.record_decision_event(
            session,
            opportunity_id=f"opp-{i}",
            model_id="M1_SCALP",
            direction="BUY",
            stage="BLOCKED",
            symbol="XAUUSDT",
            run_id="run-pg-01",
            occurred_at_ms=1700000000000 + i * 1000,
        )
    session.commit()

    # Page 1 (limit 10)
    p1 = engine_observability_service.list_recent_decisions(
        session,
        run_id="run-pg-01",
        symbol="XAUUSDT",
        limit=10,
        cursor=None,
    )
    assert len(p1["items"]) == 10
    assert p1["has_more"] is True
    assert p1["next_cursor"] is not None

    # Page 2
    p2 = engine_observability_service.list_recent_decisions(
        session,
        run_id="run-pg-01",
        symbol="XAUUSDT",
        limit=10,
        cursor=p1["next_cursor"],
    )
    assert len(p2["items"]) == 5
    assert p2["has_more"] is False


def test_pg02_corrupted_base64_raises_422():
    with pytest.raises(HTTPException) as exc_info:
        decode_observation_cursor("not_valid_base64!!!")
    assert exc_info.value.status_code == 422


def test_pg03_non_positive_timestamp_raises_422():
    bad_payload = base64.b64encode(b"-100_123").decode("utf-8")
    with pytest.raises(HTTPException) as exc_info:
        decode_observation_cursor(bad_payload)
    assert exc_info.value.status_code == 422


def test_pg04_run_id_mismatch_raises_422():
    payload = base64.b64encode(b"1700000000000_123_RUN_A_XAUUSDT").decode("utf-8")
    with pytest.raises(HTTPException) as exc_info:
        decode_observation_cursor(payload, expected_run_id="RUN_B")
    assert exc_info.value.status_code == 422
    assert "Cursor run_id mismatch" in exc_info.value.detail


def test_pg05_symbol_filtering(decision_db):
    session = decision_db
    # Record XAUUSDT event
    engine_observability_service.record_decision_event(
        session,
        opportunity_id="opp-xau",
        model_id="M1_SCALP",
        direction="BUY",
        stage="QUALIFIED",
        symbol="XAUUSDT",
        occurred_at_ms=1700000000000,
    )
    # Record BTCUSDT event
    engine_observability_service.record_decision_event(
        session,
        opportunity_id="opp-btc",
        model_id="M1_SCALP",
        direction="SELL",
        stage="QUALIFIED",
        symbol="BTCUSDT",
        occurred_at_ms=1700000001000,
    )
    session.commit()

    res_xau = engine_observability_service.list_recent_decisions(session, symbol="XAUUSDT")
    symbols_found = [it["opportunity_id"] for it in res_xau["items"]]
    assert "opp-xau" in symbols_found
    assert "opp-btc" not in symbols_found
