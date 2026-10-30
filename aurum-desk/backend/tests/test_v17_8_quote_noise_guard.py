"""
backend/tests/test_v17_8_quote_noise_guard.py
AURUM V17.8 Phase 4 & Phase 5 Tests:
Market Noise & Quote Protection + Entry Quality Invariants.
Verifies pre-ARM and pre-FILL distance guards against microstructure noise.
"""

import pytest
from services.entry_guard_service import (
    EntryGuardRequest,
    _check_geometry,
    _check_quote,
)
from services.quote_validator import ValidatedQuote
from services.unicorn_evidence_types import (
    UnicornEvidence,
    UnicornSetupStatus,
)


def _make_quote(
    bid: float = 4020.0,
    ask: float = 4020.2,
    is_valid: bool = True,
    is_stale: bool = False,
    rejection_code: str = None,
) -> ValidatedQuote:
    return ValidatedQuote(
        is_valid=is_valid,
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2.0,
        spread=round(ask - bid, 4),
        exchange_time=1000,
        observed_at=1000,
        freshness_sec=0.1,
        is_stale=is_stale,
        rejection_code=rejection_code,
        rejection_detail=rejection_code or "OK",
    )


def _make_req(
    *,
    symbol: str = "XAUUSDT",
    direction: str = "SHORT",
    stage: str = "ARM",
    now_ms: int = 1000,
    planned_entry: float = 4021.0,
    stop_loss: float = 4025.0,
    take_profit: float = 4000.0,
    validated_quote: ValidatedQuote = None,
    min_protective_distance: float = 0.50,
    unicorn_evidence: UnicornEvidence = None,
    execution_mode: str = "AUTO",
    origin: str = "AUTO_STRATEGY",
) -> EntryGuardRequest:
    return EntryGuardRequest(
        symbol=symbol,
        direction=direction,
        stage=stage,
        now_ms=now_ms,
        execution_mode=execution_mode,
        origin=origin,
        planned_entry=planned_entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        validated_quote=validated_quote,
        min_protective_distance=min_protective_distance,
        unicorn_evidence=unicorn_evidence,
    )


def test_short_stop_breached_by_executable_ask():
    """
    Verifies that when market Ask >= SL for SHORT, the guard fails closed
    with INVALID_GEOMETRY_STOP_BREACHED. Prevents entering a trade whose stop
    is already penetrated by the ask.
    """
    quote = _make_quote(bid=4021.5, ask=4021.75)
    req = _make_req(
        direction="SHORT",
        planned_entry=4021.0,
        stop_loss=4021.67,  # ask (4021.75) >= SL (4021.67)
        take_profit=4002.0,
        validated_quote=quote,
        min_protective_distance=0.50,
    )

    check = _check_geometry(req)
    assert check.state == "BLOCK"
    assert check.code == "INVALID_GEOMETRY_STOP_BREACHED"
    assert "Ask" in check.message_vi


def test_short_stop_too_close_to_executable_ask():
    """
    Verifies that if remaining distance from executable Ask to SL is less
    than min_protective_distance, it blocks with STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE.
    Addresses historical ~9 second stop-outs caused by entering 0.59 away from SL.
    """
    quote = _make_quote(bid=4021.0, ask=4021.20)
    req = _make_req(
        direction="SHORT",
        planned_entry=4021.08,
        stop_loss=4021.67,  # remaining = 4021.67 - 4021.20 = 0.47
        take_profit=4002.0,
        validated_quote=quote,
        min_protective_distance=0.75,  # requires at least 0.75 cushion
    )

    check = _check_geometry(req)
    assert check.state == "BLOCK"
    assert check.code == "STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE"


def test_long_stop_breached_by_executable_bid():
    """
    Verifies that when market Bid <= SL for LONG, the guard fails closed
    with INVALID_GEOMETRY_STOP_BREACHED.
    """
    quote = _make_quote(bid=4019.8, ask=4020.0)
    req = _make_req(
        direction="LONG",
        planned_entry=4020.5,
        stop_loss=4020.0,  # bid (4019.8) <= SL (4020.0)
        take_profit=4035.0,
        validated_quote=quote,
        min_protective_distance=0.50,
    )

    check = _check_geometry(req)
    assert check.state == "BLOCK"
    assert check.code == "INVALID_GEOMETRY_STOP_BREACHED"
    assert "Bid" in check.message_vi


def test_long_stop_too_close_to_executable_bid():
    """
    Verifies that if remaining distance from executable Bid to SL is less
    than min_protective_distance for LONG, it blocks with STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE.
    """
    quote = _make_quote(bid=4020.30, ask=4020.50)
    req = _make_req(
        direction="LONG",
        planned_entry=4020.5,
        stop_loss=4020.0,  # remaining = 4020.30 - 4020.0 = 0.30
        take_profit=4035.0,
        validated_quote=quote,
        min_protective_distance=0.60,  # requires 0.60
    )

    check = _check_geometry(req)
    assert check.state == "BLOCK"
    assert check.code == "STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE"


def test_unicorn_invalidation_guard_blocks_when_distal_breached():
    """
    Verifies that if Unicorn evidence is invalidated, EntryGuard blocks
    execution with UNICORN_INVALIDATED.
    """
    invalid_unicorn = UnicornEvidence(
        unicorn_id="uni_inv_01",
        symbol="XAUUSDT",
        direction="SHORT",
        setup_episode_id="ep_01",
        validity_status=UnicornSetupStatus.UNICORN_INVALIDATED.value,
        invalidation_price=4025.0,
        known_at_ms=1000,
    )

    req = _make_req(
        direction="SHORT",
        planned_entry=4021.0,
        stop_loss=4025.5,
        take_profit=4000.0,
        unicorn_evidence=invalid_unicorn,
    )

    check = _check_geometry(req)
    assert check.state == "BLOCK"
    assert check.code == "UNICORN_INVALIDATED"


def test_safe_executable_quote_distance_passes_geometry():
    """
    Verifies that when quote distance is well beyond min_protective_distance,
    and SL is unbreached, geometry check PASSES.
    """
    quote = _make_quote(bid=4020.80, ask=4021.00)
    req = _make_req(
        direction="SHORT",
        planned_entry=4021.0,
        stop_loss=4024.50,  # remaining = 4024.50 - 4021.00 = 3.50
        take_profit=4002.0,
        validated_quote=quote,
        min_protective_distance=0.80,
    )

    check = _check_geometry(req)
    assert check.state == "PASS"
    assert check.code == "geometry"


def test_stale_or_invalid_quote_fails_closed():
    """
    Verifies that stale or missing quote data blocks entry at the quote check stage.
    """
    # 1. Stale quote
    stale_quote = _make_quote(bid=4020.0, ask=4020.2, is_valid=False, is_stale=True, rejection_code="QUOTE_STALE")
    req_stale = _make_req(
        direction="SHORT",
        planned_entry=4021.0,
        stop_loss=4024.0,
        take_profit=4002.0,
        validated_quote=stale_quote,
    )
    c_stale = _check_quote(req_stale)
    assert c_stale.state == "BLOCK"
    assert c_stale.code == "QUOTE_STALE"

    # 2. Missing quote
    req_none = _make_req(
        direction="SHORT",
        planned_entry=4021.0,
        stop_loss=4024.0,
        take_profit=4002.0,
        validated_quote=None,
    )
    c_none = _check_quote(req_none)
    assert c_none.state == "UNKNOWN"
    assert c_none.code == "QUOTE_UNAVAILABLE"
