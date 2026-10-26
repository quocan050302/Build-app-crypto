"""
backend/tests/test_v15_9_quote_source_rejection.py
V15.9 - Execution Quote Source Rejection Authority Across Representations.

Verifies:
1. Baseline reproduction tests:
   - Stale quote dictionary (is_valid=True, is_stale=True, rejection_code="TICKER_STALE", fresh ts)
     now normalizes to is_valid=False.
   - Disconnected quote dictionary (status="DISCONNECTED", rejection_code="FEED_DISCONNECTED", fresh ts)
     now normalizes to is_valid=False.
   - CanonicalQuote object vs to_dict() vs JSON string round-trip parity.
2. Source state interpretation & conflict resolution:
   - Stale flag without code -> TICKER_STALE.
   - Disconnected status without code -> FEED_DISCONNECTED.
   - is_valid=True does NOT override is_stale=True or status="DISCONNECTED".
   - Non-empty rejection_code preserves invalidity.
   - Rejection detail preservation.
   - Parameterized status rejection matrix for CanonicalQuote and dict.
   - Unknown status rejected safely.
   - Missing status / is_valid in raw ticker normalizes to valid if numbers & timestamps are valid.
   - Malformed booleans handled safely.
   - ValidatedQuote invalid & stale conflict preservation.
3. Immutability and freshness:
   - Non-mutation of input dictionaries and objects.
   - Repeated normalization does not resurrect rejected quote or overwrite original timestamps.
   - Immediate recovery when new valid quote arrives (no sticky global block).
   - Numeric and age validations (stale, missing ts, non-finite, inverted spread, future ts) still hold.
   - Dual failure reason priority (FEED_DISCONNECTED > numeric > source rejection).
   - Young age but disconnected quote has small freshness_sec but is_valid=False.
4. Production integration tests:
   - Direct _arm_paper_candidate() rejects stale / disconnected quote dictionaries without mutating DB.
   - Direct arming round-trip parity (CanonicalQuote object vs dict).
   - Production evaluate_cycle() blocks entry for causal LONG and SHORT when quote is stale or disconnected.
   - Positive controls: causal LONG & SHORT with fresh valid quote arms order.
   - Positive control: raw ticker dict without status/is_valid arms order.
   - Positive control: CanonicalQuote(status="VALID").to_dict() arms order.
   - Multi-cycle recovery: rejected cycle followed by valid cycle arms order cleanly.
   - Existing committed order remains untouched across rejected cycles.
"""
from __future__ import annotations

import copy
import json
import math
import time
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import models
from services.clock import FakeClock
from services.liquidity_scenario_service import (
    HypothesisWriteOutcome,
    LifecycleCycleAuthority,
    LifecycleStageResult,
)
from services.quote_validator import (
    CanonicalQuote,
    QuoteSourceState,
    QuoteValidator,
    ValidatedQuote,
    apply_quote_source_rejection,
    extract_execution_quote_source_state,
    normalize_execution_quote,
    parse_optional_bool,
)
from services.scalp_m1_model_service import ScalpCandidate
from services.scalp_m1_types import (
    M1ScalpConfig,
    ScalpMode,
    WatchedZone,
    ZoneState,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from tests.test_v15_1_execution_lifecycle import (
    _make_causal_long_m1_candles,
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def isolated_db() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def base_cfg() -> M1ScalpConfig:
    return M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )


def _make_dummy_scalp_candidate(zone_id: str = "z_v15_9_test", direction: str = "LONG") -> ScalpCandidate:
    calc = type("CalcStub", (), {
        "is_valid": True,
        "can_execute": True,
        "meets_min_rr": True,
        "quantity": 0.1,
        "gross_rr": 3.0,
        "net_rr": 2.8,
        "net_reward_usdt": 50.0,
        "net_risk_usdt": 2.5,
        "leverage": 30.0,
        "margin_mode": "ISOLATED",
        "effective_risk_pct": 0.25,
        "estimated_liquidation": 2550.0 if direction == "LONG" else 2750.0,
        "est_fill_fee_usdt": 0.1,
        "est_close_fee_usdt": 0.1,
        "est_slippage_usdt": 0.1,
        "total_friction_usdt": 0.3,
        "cash_buffer_usdt": 10.0,
        "margin_used_usdt": 10.0,
    })()
    p_entry = 2620.0 if direction == "LONG" else 2650.0
    sl = 2610.0 if direction == "LONG" else 2660.0
    tp = 2650.0 if direction == "LONG" else 2620.0
    return ScalpCandidate(
        is_eligible=True,
        model_id="SMC_M1_REVERSAL",
        direction=direction,
        candidate_key=f"cand_v15_9_{zone_id}",
        symbol="XAUUSDT",
        planned_entry=p_entry,
        stop_loss=sl,
        take_profit=tp,
        net_rr=2.8,
        gross_rr=3.0,
        calculation=calc,
        zone_id=zone_id,
    )


def _make_positive_authority(zone_id: str = "z_v15_9_test") -> LifecycleCycleAuthority:
    auth = LifecycleCycleAuthority()
    auth.zone_outcomes[zone_id] = HypothesisWriteOutcome(
        hypothesis_id=f"hyp_{zone_id}",
        storage_key=f"hlc:XAUUSDT:{zone_id}",
        zone_id=zone_id,
        operation="UPSERT_HYPOTHESIS",
        result=LifecycleStageResult.CREATED,
        is_success=True,
    )
    return auth


# =====================================================================
# 1. BASELINE REPRODUCTION TESTS (002, 003, 127, 128, 129)
# =====================================================================

def test_v15_9_baseline_stale_dict_rejection():
    """
    SECTION 002, 127:
    Dictionary with is_stale=True, rejection_code='TICKER_STALE', is_valid=True
    and fresh timestamp must normalize to is_valid=False and is_stale=True.
    """
    now_ms = 1_000_000
    stale_dict = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "is_valid": True,
        "is_stale": True,
        "rejection_code": "TICKER_STALE",
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    norm = normalize_execution_quote(stale_dict, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.is_stale is True
    assert norm.rejection_code == "TICKER_STALE"


def test_v15_9_baseline_disconnected_dict_rejection():
    """
    SECTION 002, 128:
    Dictionary with status='DISCONNECTED', rejection_code='FEED_DISCONNECTED',
    and fresh timestamp must normalize to is_valid=False.
    """
    now_ms = 1_000_000
    disconnected_dict = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "DISCONNECTED",
        "rejection_code": "FEED_DISCONNECTED",
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    norm = normalize_execution_quote(disconnected_dict, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "FEED_DISCONNECTED"


def test_v15_9_baseline_roundtrip_parity_disconnected():
    """
    SECTION 003, 085, 129:
    CanonicalQuote with status='DISCONNECTED' when passed as object vs to_dict()
    must yield identical rejection outcomes: both is_valid=False and FEED_DISCONNECTED.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="DISCONNECTED",
        rejection_code="FEED_DISCONNECTED",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        freshness_sec=0.1,
    )

    norm_obj = normalize_execution_quote(cq, now_ms=now_ms)
    norm_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms)

    assert norm_obj.is_valid is False
    assert norm_dict.is_valid is False
    assert norm_obj.is_valid == norm_dict.is_valid
    assert norm_obj.is_stale == norm_dict.is_stale
    assert norm_obj.rejection_code == norm_dict.rejection_code == "FEED_DISCONNECTED"


# =====================================================================
# 2. SOURCE STATE INTERPRETATION & CONFLICT RESOLUTION (130 - 148)
# =====================================================================

def test_v15_9_stale_flag_without_code():
    """
    SECTION 130:
    Fresh timestamps and is_stale=True, but missing rejection_code.
    Must normalize to is_valid=False, is_stale=True, rejection_code='TICKER_STALE'.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "is_stale": True,
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.is_stale is True
    assert norm.rejection_code == "TICKER_STALE"


def test_v15_9_disconnected_status_without_code():
    """
    SECTION 131:
    Fresh timestamps and status='DISCONNECTED', but missing rejection_code.
    Must normalize to is_valid=False, rejection_code='FEED_DISCONNECTED'.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "DISCONNECTED",
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "FEED_DISCONNECTED"


def test_v15_9_is_valid_false_preserved():
    """
    SECTION 132:
    is_valid=False in dict must be preserved with its rejection_code.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "is_valid": False,
        "rejection_code": "CUSTOM_SOURCE_ERROR",
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "CUSTOM_SOURCE_ERROR"


def test_v15_9_is_valid_true_and_stale_true_conflict():
    """
    SECTION 133:
    Conflicting flags: is_valid=True and is_stale=True.
    Rule: is_valid=True cannot override is_stale=True -> is_valid=False, is_stale=True.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "is_valid": True,
        "is_stale": True,
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.is_stale is True


def test_v15_9_valid_status_and_stale_true_conflict():
    """
    SECTION 134:
    Conflicting flags: status='VALID' but is_stale=True.
    Must normalize to is_valid=False, is_stale=True.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "VALID",
        "is_stale": True,
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.is_stale is True


def test_v15_9_disconnected_and_is_valid_true_conflict():
    """
    SECTION 135:
    Conflicting flags: status='DISCONNECTED', is_valid=True, fresh timestamps.
    Source rejection has authority to block -> is_valid=False, FEED_DISCONNECTED.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "DISCONNECTED",
        "is_valid": True,
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "FEED_DISCONNECTED"


def test_v15_9_rejection_code_non_empty_preserves_rejection():
    """
    SECTION 136:
    Non-empty rejection_code present in dict keeps quote invalid even without explicit is_valid=False.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "rejection_code": "CIRCUIT_BREAKER_ACTIVE",
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "CIRCUIT_BREAKER_ACTIVE"


def test_v15_9_rejection_detail_preservation():
    """
    SECTION 137:
    rejection_detail provided in input must be preserved in normalized ValidatedQuote.
    """
    now_ms = 1_000_000
    detail_msg = "WebSocket heartbeat dropped after 3 attempts"
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "DISCONNECTED",
        "rejection_detail": detail_msg,
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_detail == detail_msg


@pytest.mark.parametrize("rej_status,expected_code", [
    ("DISCONNECTED", "FEED_DISCONNECTED"),
    ("STALE", "TICKER_STALE"),
    ("MALFORMED", "MALFORMED_QUOTE"),
    ("INVERTED", "INVERTED_SPREAD"),
    ("TIME_UNAVAILABLE", "TIME_UNAVAILABLE"),
    ("FUTURE_TIMESTAMP", "FUTURE_TIMESTAMP"),
])
def test_v15_9_status_rejection_matrix(rej_status: str, expected_code: str):
    """
    SECTION 138, 147:
    Parameterize all rejected statuses in CanonicalQuote contract.
    Both object and to_dict() must be invalid with equivalent rejection code.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status=rej_status,
        rejection_code=expected_code,
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        freshness_sec=0.1,
    )
    norm_obj = normalize_execution_quote(cq, now_ms=now_ms)
    norm_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms)

    assert norm_obj.is_valid is False
    assert norm_dict.is_valid is False
    assert norm_obj.rejection_code == expected_code
    assert norm_dict.rejection_code == expected_code


def test_v15_9_unknown_status_rejected():
    """
    SECTION 139:
    Status outside contract enum (e.g. 'UNKNOWN_STATE_99') is not silently treated as valid.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "UNKNOWN_STATE_99",
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False


def test_v15_9_missing_status_raw_ticker_valid():
    """
    SECTION 140:
    Raw ticker without status and without source rejection fields normalizes to valid.
    Demonstrates patch does not require new status fields for old raw sources.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is True
    assert norm.is_stale is False
    assert norm.rejection_code is None


def test_v15_9_missing_is_valid_raw_ticker_valid():
    """
    SECTION 141:
    Raw ticker without is_valid field normalizes to valid if numbers and timestamps are valid.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "timestamp": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is True


@pytest.mark.parametrize("val,expected_bool", [
    (True, True),
    (False, False),
    ("true", True),
    ("TRUE", True),
    ("false", False),
    ("FALSE", False),
    ("1", True),
    ("0", False),
    (1, True),
    (0, False),
    (None, None),
    ("invalid_string", None),
    ([], None),
    ({}, None),
])
def test_v15_9_parse_optional_bool(val: Any, expected_bool: Optional[bool]):
    """
    SECTION 045, 046, 142:
    parse_optional_bool parses real booleans and strings without bool('false') == True pitfall.
    """
    assert parse_optional_bool(val) == expected_bool


def test_v15_9_boolean_malformed_rejection():
    """
    SECTION 142:
    Malformed boolean field (e.g. is_valid='not_a_bool') rejects as MALFORMED_QUOTE.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "is_valid": "banana",
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "MALFORMED_QUOTE"


def test_v15_9_validated_quote_invalid_preserved():
    """
    SECTION 143:
    ValidatedQuote with is_valid=False preserves rejection when re-normalized.
    """
    now_ms = 1_000_000
    vq = ValidatedQuote(
        is_valid=False,
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        spread=0.5,
        exchange_time=now_ms,
        observed_at=now_ms,
        freshness_sec=0.1,
        is_stale=False,
        rejection_code="EARLIER_REJECTION",
        rejection_detail="Early gate failure",
    )
    norm = normalize_execution_quote(vq, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "EARLIER_REJECTION"


def test_v15_9_validated_quote_stale_conflict():
    """
    SECTION 144:
    ValidatedQuote with is_valid=True but is_stale=True is rejected.
    Object path is not weaker than dictionary path.
    """
    now_ms = 1_000_000
    vq = ValidatedQuote(
        is_valid=True,
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        spread=0.5,
        exchange_time=now_ms,
        observed_at=now_ms,
        freshness_sec=0.1,
        is_stale=True,
        rejection_code=None,
        rejection_detail=None,
    )
    norm = normalize_execution_quote(vq, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.is_stale is True


def test_v15_9_canonical_quote_valid_accepted():
    """
    SECTION 145:
    CanonicalQuote with status='VALID', valid prices and fresh timestamps is accepted.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        freshness_sec=0.1,
    )
    norm_obj = normalize_execution_quote(cq, now_ms=now_ms)
    norm_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms)

    assert norm_obj.is_valid is True
    assert norm_dict.is_valid is True
    assert norm_obj.is_stale is False
    assert norm_dict.is_stale is False


def test_v15_9_canonical_quote_disconnected_object_rejected():
    """
    SECTION 146:
    CanonicalQuote object with status='DISCONNECTED' is rejected.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="DISCONNECTED",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )
    norm = normalize_execution_quote(cq, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "FEED_DISCONNECTED"


def test_v15_9_json_roundtrip_preservation():
    """
    SECTION 148:
    CanonicalQuote.to_dict() -> json.dumps() -> json.loads() -> normalize_execution_quote().
    Assert source rejection is fully preserved through JSON serialization.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="DISCONNECTED",
        rejection_code="FEED_DISCONNECTED",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )
    json_str = json.dumps(cq.to_dict())
    deserialized = json.loads(json_str)

    norm = normalize_execution_quote(deserialized, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "FEED_DISCONNECTED"


# =====================================================================
# 3. IMMUTABILITY, TIMESTAMPS & RECOVERY (149 - 160)
# =====================================================================

def test_v15_9_non_mutation_input_dict():
    """
    SECTION 066, 149:
    normalize_execution_quote() does not mutate input dictionary.
    """
    now_ms = 1_000_000
    orig = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "DISCONNECTED",
        "rejection_code": "FEED_DISCONNECTED",
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    copy_d = copy.deepcopy(orig)
    normalize_execution_quote(orig, now_ms=now_ms)
    assert orig == copy_d


def test_v15_9_non_mutation_input_object():
    """
    SECTION 150:
    normalize_execution_quote() does not mutate fields of input CanonicalQuote or ValidatedQuote.
    """
    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="DISCONNECTED",
        rejection_code="FEED_DISCONNECTED",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )
    status_before = cq.status
    code_before = cq.rejection_code
    normalize_execution_quote(cq, now_ms=now_ms)
    assert cq.status == status_before
    assert cq.rejection_code == code_before


def test_v15_9_normalize_repeated_invalid():
    """
    SECTION 151:
    Normalizing the same rejected quote repeatedly at increasing now_ms
    remains rejected every time, and original exchange_time is preserved.
    """
    base_ts = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "DISCONNECTED",
        "server_time": base_ts,
        "observed_at": base_ts,
    }

    for dt in (0, 1000, 5000, 15000):
        norm = normalize_execution_quote(d, now_ms=base_ts + dt)
        assert norm.is_valid is False
        assert norm.rejection_code == "FEED_DISCONNECTED"
        assert norm.exchange_time == base_ts


def test_v15_9_input_recovery_no_sticky_rejection():
    """
    SECTION 089, 090, 152:
    A rejected quote does not permanently poison the normalizer.
    Subsequent call with fresh valid quote succeeds immediately.
    """
    now_ms = 1_000_000
    bad_quote = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "DISCONNECTED",
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm_bad = normalize_execution_quote(bad_quote, now_ms=now_ms)
    assert norm_bad.is_valid is False

    good_quote = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "VALID",
        "server_time": now_ms + 100,
        "observed_at": now_ms + 100,
    }
    norm_good = normalize_execution_quote(good_quote, now_ms=now_ms + 100)
    assert norm_good.is_valid is True
    assert norm_good.is_stale is False


def test_v15_9_age_validation_still_works():
    """
    SECTION 153:
    Quote with status='VALID' but timestamp 999s old is still rejected as TICKER_STALE.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "VALID",
        "server_time": 1_000,
        "observed_at": 1_000,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.is_stale is True
    assert norm.rejection_code == "TICKER_STALE"


def test_v15_9_missing_timestamp_still_works():
    """
    SECTION 154:
    Quote with status='VALID' but missing timestamps is rejected as TIME_UNAVAILABLE.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "VALID",
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "TIME_UNAVAILABLE"


def test_v15_9_non_finite_price_still_works():
    """
    SECTION 155:
    Quote with status='VALID' but NaN/Inf price is rejected as NON_FINITE_QUOTE.
    """
    now_ms = 1_000_000
    d = {
        "bid": float("nan"),
        "ask": 2620.5,
        "last": 2620.25,
        "status": "VALID",
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "NON_FINITE_QUOTE"


def test_v15_9_inverted_spread_still_works():
    """
    SECTION 156:
    Quote with status='VALID' but ask < bid is rejected as INVERTED_SPREAD.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2625.0,
        "ask": 2620.0,
        "last": 2622.0,
        "status": "VALID",
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "INVERTED_SPREAD"


def test_v15_9_future_timestamp_still_works():
    """
    SECTION 157:
    Quote with status='VALID' but timestamp > 5s in the future is rejected as FUTURE_TIMESTAMP.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "VALID",
        "server_time": now_ms + 10_000,
        "observed_at": now_ms + 10_000,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "FUTURE_TIMESTAMP"


def test_v15_9_dual_failure_reason_priority():
    """
    SECTION 075, 158:
    When quote is both DISCONNECTED and 999s stale: FEED_DISCONNECTED takes priority.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "DISCONNECTED",
        "rejection_code": "FEED_DISCONNECTED",
        "server_time": 1_000,  # 999s stale
        "observed_at": 1_000,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.rejection_code == "FEED_DISCONNECTED"


def test_v15_9_young_age_disconnected_not_stale():
    """
    SECTION 074, 159:
    Quote with status='DISCONNECTED' but fresh timestamp (freshness_sec < 0.1)
    is invalid without having is_stale=True fabricated.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "DISCONNECTED",
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.is_stale is False
    assert norm.freshness_sec < 1.0
    assert norm.rejection_code == "FEED_DISCONNECTED"


def test_v15_9_stale_source_with_fresh_timestamp():
    """
    SECTION 160:
    is_stale=True with fresh timestamp preserves is_valid=False and is_stale=True
    without fabricating a 9999s freshness_sec.
    """
    now_ms = 1_000_000
    d = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "is_stale": True,
        "server_time": now_ms,
        "observed_at": now_ms,
    }
    norm = normalize_execution_quote(d, now_ms=now_ms)
    assert norm.is_valid is False
    assert norm.is_stale is True
    assert norm.rejection_code == "TICKER_STALE"
    assert norm.freshness_sec < 1.0


# =====================================================================
# 4. PRODUCTION FLOW & REGRESSION TESTS (161 - 186)
# =====================================================================

def test_v15_9_arm_direct_stale_dict_returns_none_zero_mutations(isolated_db: Session):
    """
    SECTION 165, 168, 169, 170:
    Direct invocation of _arm_paper_candidate() with stale dict (is_stale=True, fresh ts)
    returns None and produces zero mutations to WatchSetup, SMCScenarioSnapshot, or PaperOrder.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cand = _make_dummy_scalp_candidate("z_arm_stale_dict")
    auth = _make_positive_authority("z_arm_stale_dict")

    now_ms = 1_000_000
    stale_dict = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "is_valid": True,
        "is_stale": True,
        "rejection_code": "TICKER_STALE",
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    ws_count_before = isolated_db.query(models.WatchSetup).count()
    snap_count_before = isolated_db.query(models.SMCScenarioSnapshot).count()

    order = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=now_ms, clock=FakeClock(now_ms),
        quote=stale_dict,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=auth,
        auto_enabled=True,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
    )

    assert order is None
    assert isolated_db.query(models.PaperOrder).count() == 0
    assert isolated_db.query(models.WatchSetup).count() == ws_count_before
    assert isolated_db.query(models.SMCScenarioSnapshot).count() == snap_count_before


def test_v15_9_arm_direct_disconnected_dict_returns_none(isolated_db: Session):
    """
    SECTION 166:
    Direct invocation of _arm_paper_candidate() with disconnected dict (status='DISCONNECTED', fresh ts)
    returns None even when feed_health fixture is CONNECTED. Quote rejection has authority.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cand = _make_dummy_scalp_candidate("z_arm_disconn_dict")
    auth = _make_positive_authority("z_arm_disconn_dict")

    now_ms = 1_000_000
    disconn_dict = {
        "bid": 2620.0,
        "ask": 2620.5,
        "last": 2620.25,
        "status": "DISCONNECTED",
        "rejection_code": "FEED_DISCONNECTED",
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    order = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=now_ms, clock=FakeClock(now_ms),
        quote=disconn_dict,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=auth,
        auto_enabled=True,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
    )

    assert order is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_9_arm_direct_roundtrip_parity(isolated_db: Session):
    """
    SECTION 167:
    Pass CanonicalQuote DISCONNECTED object vs its to_dict() into _arm_paper_candidate().
    Both must return None and create zero orders.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cand = _make_dummy_scalp_candidate("z_arm_parity")
    auth = _make_positive_authority("z_arm_parity")

    now_ms = 1_000_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2620.0,
        ask=2620.5,
        last=2620.25,
        status="DISCONNECTED",
        rejection_code="FEED_DISCONNECTED",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    # 1. Object pass
    order_obj = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=now_ms, clock=FakeClock(now_ms),
        quote=cq,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=auth,
        auto_enabled=True,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
    )
    assert order_obj is None

    # 2. Dict pass
    order_dict = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=now_ms, clock=FakeClock(now_ms),
        quote=cq.to_dict(),
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=auth,
        auto_enabled=True,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
    )
    assert order_dict is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_9_evaluate_cycle_causal_long_stale_dict_blocks_entry(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 161:
    Production evaluate_cycle() with causal LONG candles blocks entry when quote
    is a dictionary with fresh timestamps but is_stale=True / TICKER_STALE.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_eval_long_stale_dict", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    stale_quote_dict = {
        "bid": 2622.0,
        "ask": 2622.5,
        "last": 2622.25,
        "is_valid": True,
        "is_stale": True,
        "rejection_code": "TICKER_STALE",
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=stale_quote_dict, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res.get("armed_order_id") is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_9_evaluate_cycle_causal_long_disconnected_dict_blocks_entry(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 162:
    Production evaluate_cycle() with causal LONG candles blocks entry when quote
    is a dictionary with fresh timestamps but status='DISCONNECTED'.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_eval_long_disconn_dict", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    disconn_quote_dict = {
        "bid": 2622.0,
        "ask": 2622.5,
        "last": 2622.25,
        "status": "DISCONNECTED",
        "rejection_code": "FEED_DISCONNECTED",
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=disconn_quote_dict, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res.get("armed_order_id") is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_9_evaluate_cycle_causal_short_stale_dict_blocks_entry(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 163:
    Production evaluate_cycle() with causal SHORT candles blocks entry when quote
    is a dictionary with fresh timestamps but is_stale=True / TICKER_STALE.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_eval_short_stale_dict", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    stale_quote_dict = {
        "bid": 2647.5,
        "ask": 2648.0,
        "last": 2647.75,
        "is_valid": True,
        "is_stale": True,
        "rejection_code": "TICKER_STALE",
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=stale_quote_dict, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res.get("armed_order_id") is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_9_evaluate_cycle_causal_short_disconnected_dict_blocks_entry(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 164:
    Production evaluate_cycle() with causal SHORT candles blocks entry when quote
    is a dictionary with fresh timestamps but status='DISCONNECTED'.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_eval_short_disconn_dict", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    disconn_quote_dict = {
        "bid": 2647.5,
        "ask": 2648.0,
        "last": 2647.75,
        "status": "DISCONNECTED",
        "rejection_code": "FEED_DISCONNECTED",
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=disconn_quote_dict, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res.get("armed_order_id") is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_9_positive_control_causal_long_fresh_quote(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 173:
    Positive control: Causal LONG candles, real writer, real calculator, fresh valid quote
    successfully arms an order in evaluate_cycle().
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_pos_long", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    fresh_quote = {
        "bid": 2622.0,
        "ask": 2622.5,
        "last": 2622.25,
        "is_valid": True,
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=fresh_quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res.get("armed_order_id") is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "LONG"
    assert order.state == "armed"
    assert order.take_profit > order.planned_entry > order.stop_loss


def test_v15_9_positive_control_causal_short_fresh_quote(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 174:
    Positive control: Causal SHORT candles, real writer, real calculator, fresh valid quote
    successfully arms an order in evaluate_cycle().
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_pos_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    fresh_quote = {
        "bid": 2647.5,
        "ask": 2648.0,
        "last": 2647.75,
        "is_valid": True,
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=fresh_quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res.get("armed_order_id") is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "SHORT"
    assert order.state == "armed"
    assert order.take_profit < order.planned_entry < order.stop_loss


def test_v15_9_positive_control_raw_ticker(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 175:
    Positive control: Raw ticker dict without status or is_valid fields
    successfully arms an order in evaluate_cycle() if prices and timestamps are fresh.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_pos_raw", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    raw_ticker_dict = {
        "bid": 2622.0,
        "ask": 2622.5,
        "last": 2622.25,
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=raw_ticker_dict, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res.get("armed_order_id") is not None


def test_v15_9_positive_control_canonical_dict(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 176:
    Positive control: CanonicalQuote(status='VALID').to_dict()
    successfully arms an order in evaluate_cycle().
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_pos_cq_dict", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    cq = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2622.0,
        ask=2622.5,
        last=2622.25,
        status="VALID",
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
    )

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=cq.to_dict(), candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res.get("armed_order_id") is not None


def test_v15_9_production_recovery_after_rejected_cycle(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 177:
    Multi-cycle production recovery:
    Cycle 1 receives DISCONNECTED quote -> entry blocked (no order).
    Cycle 2 receives fresh valid quote -> entry allowed, order armed!
    Proves no sticky rejection lock exists across cycles.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_recovery_test", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    t1 = 1_300_000
    bad_quote = {
        "bid": 2622.0,
        "ask": 2622.5,
        "last": 2622.25,
        "status": "DISCONNECTED",
        "rejection_code": "FEED_DISCONNECTED",
        "server_time": t1,
        "observed_at": t1,
    }

    # Cycle 1: Blocked
    res1 = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=t1, quote=bad_quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res1.get("armed_order_id") is None
    assert isolated_db.query(models.PaperOrder).count() == 0

    # Cycle 2: Recovered
    t2 = t1 + 5_000
    good_quote = {
        "bid": 2622.0,
        "ask": 2622.5,
        "last": 2622.25,
        "status": "VALID",
        "server_time": t2,
        "observed_at": t2,
    }
    res2 = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=t2, quote=good_quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res2["status"] == "OK"
    assert res2.get("armed_order_id") is not None
    assert isolated_db.query(models.PaperOrder).count() == 1


def test_v15_9_existing_committed_order_untouched_by_rejected_candidate(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 186:
    An existing committed PaperOrder in the database remains completely untouched
    when a new cycle runs with a rejected execution quote.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()

    # Pre-create an existing committed order
    existing_order = models.PaperOrder(
        id="ord_existing_safe_99",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=2600.0,
        stop_loss=2590.0,
        take_profit=2630.0,
        quantity=0.25,
        initial_risk_usdt=2.5,
        created_at=100_000,
    )
    isolated_db.add(existing_order)
    isolated_db.commit()
    existing_id = existing_order.id

    # Run cycle with rejected quote
    zone = WatchedZone(
        zone_id="z_existing_protect", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    stale_quote = {
        "bid": 2622.0,
        "ask": 2622.5,
        "last": 2622.25,
        "status": "DISCONNECTED",
        "server_time": now_ms,
        "observed_at": now_ms,
    }

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=stale_quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )

    assert res.get("armed_order_id") is None
    # Verify existing order has exact same properties
    isolated_db.expire_all()
    reloaded = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == existing_id).first()
    assert reloaded is not None
    assert reloaded.direction == "LONG"
    assert reloaded.planned_entry == 2600.0
    assert reloaded.stop_loss == 2590.0
    assert reloaded.take_profit == 2630.0
    assert reloaded.quantity == 0.25
    assert reloaded.state == "armed"
