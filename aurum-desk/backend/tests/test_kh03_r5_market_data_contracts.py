"""
aurum-desk/backend/tests/test_kh03_r5_market_data_contracts.py

Unit & adversarial tests for KH-03.R5 market-data foundation contracts (R5-F06..F18, F44, F46, F48):
  - Source capability inventory (no invented endpoints/continuous BBO/live contract claims)
  - Instrument metadata reparsing, gzip bomb rejection, identity/tick precision mismatch
  - OHLCV strict Decimal invariants, exact vs conflicting duplicate handling, future/partial bars, gap ledger
  - BBO crossed/stale/negative-latency/OHLCV-inferred rejection, snapshot vs continuous, duplicate timestamp coverage guard
  - Funding predicted vs settled, units/bounds, missing settlement ledger without zero-fill
  - Owner fee confirmation redaction/secret rejection, public generic rejection, maker rebate bps
  - DST-aware NY trading days coverage & execution fidelity register.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_03_r5_market_data_foundation_service import (
    build_r5_execution_fidelity_register,
    build_r5_market_data_coverage_report,
    build_r5_source_capability_inventory,
    validate_r5_bbo_stream,
    validate_r5_funding_history,
    validate_r5_instrument_metadata,
    validate_r5_ohlcv_stream,
    validate_r5_owner_fee_confirmation,
)


def test_r5_source_capability_inventory_honest_unknowns_f46() -> None:
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    assert inv["status"] == "PASS"
    assert inv["external_network_queried"] is False
    miss = inv["missing_or_blocked_capabilities"]
    assert miss["continuous_l1_bbo_1s_stream"]["status"] == "BLOCKED_CONTINUOUS_BBO"
    assert miss["public_funding_rate_history"]["status"] == "UNSUPPORTED_OR_UNVERIFIED_ENDPOINT"
    assert miss["live_contract_identity_xauusdt"]["status"] == "BLOCKED_CONTRACT_IDENTITY_UNVERIFIED_LIVE"
    assert miss["account_effective_fee_tier"]["status"] == "UNKNOWN_NOT_LOCALLY_VERIFIED"


def test_r5_instrument_metadata_identity_precision_and_gzip_bomb_f06_f07() -> None:
    good_raw = json.dumps({"code": "00000", "data": [{"symbol": "XAUUSDT", "productType": "USDT-FUTURES", "symbolType": "perpetual", "quoteCoin": "USDT", "pricePlace": "2", "priceEndStep": "1", "sizeMultiplier": "0.001", "minTradeNum": "0.001", "minTradeUSDT": "5", "effectiveTimestampMs": 1794000000000}]}).encode("utf-8")
    exp = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "settlement_currency": "USDT", "expected_tick_size": "0.01"}
    ok = validate_r5_instrument_metadata(good_raw, exp, provenance_class="QA_FIXTURE")
    assert ok["valid"] is True and ok["evidence_class"] == "QA_FIXTURE"

    # RED F07: Wrong symbol / wrong tick precision -> quarantined!
    bad_sym = json.dumps({"code": "00000", "data": [{"symbol": "BTCUSDT", "productType": "USDT-FUTURES", "quoteCoin": "USDT", "pricePlace": "2", "sizeMultiplier": "0.001", "minTradeNum": "0.001"}]}).encode("utf-8")
    r_sym = validate_r5_instrument_metadata(bad_sym, exp)
    assert r_sym["valid"] is False and r_sym["quarantined"] is True

    bad_tick = json.dumps({"code": "00000", "data": [{"symbol": "XAUUSDT", "productType": "USDT-FUTURES", "quoteCoin": "USDT", "pricePlace": "1", "priceEndStep": "1", "sizeMultiplier": "0.001", "minTradeNum": "0.001"}]}).encode("utf-8")
    r_tk = validate_r5_instrument_metadata(bad_tick, exp)
    assert r_tk["valid"] is False and any("TICK_SIZE_PRECISION_MISMATCH" in v for v in r_tk["violations"])

    # RED F06: Gzip decompression bomb (> max decompressed bytes) -> bounded reject!
    bomb = gzip.compress(b"A" * (12 * 1024 * 1024))
    r_bomb = validate_r5_instrument_metadata(bomb, exp)
    assert r_bomb["valid"] is False and r_bomb["status"] == "REJECTED_MALFORMED_OR_OVERSIZE_PAYLOAD"


def test_r5_ohlcv_invariants_duplicates_future_and_gaps_f08_f09_f10_f11_f44() -> None:
    contract = {"symbol": "XAUUSDT", "tick_size": "0.01"}
    rng = {"start_ms": 1794000000000, "end_ms": 1794000240000, "now_utc_ms": 1794000300000}

    # GREEN with exact duplicate dedup + internal gap (NO forward fill!)
    b1 = {"open_time": 1794000000000, "close_time": 1794000060000, "open": "4100.00", "high": "4101.00", "low": "4099.50", "close": "4100.50", "volume": "10.0", "is_closed": True}
    b3 = {"open_time": 1794000120000, "close_time": 1794000180000, "open": "4100.50", "high": "4102.00", "low": "4100.00", "close": "4101.50", "volume": "12.0", "is_closed": True}
    ok = validate_r5_ohlcv_stream([{"rows": [b1, b1, b3], "symbol": "XAUUSDT", "timeframe": "1m"}], contract, "1m", rng)
    assert ok["valid"] is True
    assert ok["exact_duplicate_rows_deduplicated"] == 1
    assert ok["normalized_unique_bars_count"] == 2
    assert ok["gap_intervals_count"] == 1 and ok["missing_bars_internal_gaps"] == 1
    assert ok["forward_fill_applied"] is False

    # RED F08: OHLC impossibility (high < low) & negative volume
    bad_ohlc = dict(b1, high="4098.00", low="4101.00")
    assert validate_r5_ohlcv_stream([{"rows": [bad_ohlc]}], contract, "1m", rng)["valid"] is False

    # RED F09: Conflicting duplicate at same timestamp
    b1_conflict = dict(b1, close="4100.99")
    r_conf = validate_r5_ohlcv_stream([{"rows": [b1, b1_conflict]}], contract, "1m", rng)
    assert r_conf["valid"] is False and r_conf["conflicting_duplicate_rows"] == 1

    # RED F10 / F44: Future bar & seconds-instead-of-ms timestamp unit rejected
    fut = dict(b1, open_time=1794000300000, close_time=1794000360000)
    assert validate_r5_ohlcv_stream([{"rows": [fut]}], contract, "1m", rng)["valid"] is False
    sec_bar = dict(b1, open_time=1794000000, close_time=1794000060)
    assert validate_r5_ohlcv_stream([{"rows": [sec_bar]}], contract, "1m", rng)["valid"] is False


def test_r5_bbo_crossed_stale_snapshot_ohlcv_infer_and_dup_coverage_f12_f13_f14_f15_f48() -> None:
    contract = {"symbol": "XAUUSDT", "tick_size": "0.01"}
    cp = {"stale_threshold_ms": 15_000, "expected_cadence_ms": 1_000, "window_start_ms": 1794000000000, "window_end_ms": 1794000009000}

    # RED F12: Crossed BBO & negative latency
    r_cross = validate_r5_bbo_stream([{"bid": "4101.00", "ask": "4100.50", "source_timestamp_ms": 1794000000000, "local_receive_timestamp_ms": 1794000000020}], contract, cp)
    assert r_cross["valid"] is False and r_cross["crossed_quotes_rejected"] == 1
    r_neg = validate_r5_bbo_stream([{"bid": "4100.00", "ask": "4100.20", "source_timestamp_ms": 1794000005000, "local_receive_timestamp_ms": 1794000004000}], contract, cp)
    assert r_neg["valid"] is False and r_neg["negative_latency_rejected"] == 1

    # RED F15: OHLCV-inferred BBO rejected!
    r_inf = validate_r5_bbo_stream([{"bid": "4100.00", "ask": "4100.20", "source_timestamp_ms": 1794000000000, "local_receive_timestamp_ms": 1794000000010, "inferred_from_ohlcv": True}], contract, cp)
    assert r_inf["valid"] is False and any("OHLCV_INFERRED_BBO_FORBIDDEN" in v for v in r_inf["violations"])

    # RED F13: Single snapshot CANNOT claim continuous coverage!
    r_snap = validate_r5_bbo_stream([{"bid": "4100.00", "ask": "4100.20", "source_timestamp_ms": 1794000000000, "local_receive_timestamp_ms": 1794000000020}], contract, cp, stream_mode="SINGLE_OBSERVED_SNAPSHOT")
    assert r_snap["valid"] is True and r_snap["bbo_coverage_classification"] == "SINGLE_OBSERVED_SNAPSHOT" and r_snap["meets_continuous_1s_observed_target"] is False

    # RED F48 / F14: 20 duplicate quotes at identical timestamp MUST NOT inflate coverage past 1/10 = 0.10!
    dups = [{"bid": "4100.00", "ask": "4100.20", "source_timestamp_ms": 1794000000000, "local_receive_timestamp_ms": 1794000000020} for _ in range(20)]
    r_dup_cov = validate_r5_bbo_stream(dups, contract, cp, stream_mode="SAMPLED_POLL_QUOTES")
    assert r_dup_cov["valid_unique_timestamp_quotes_count"] == 1
    assert r_dup_cov["grid_coverage_ratio"] == pytest.approx(0.10, abs=1e-4)
    assert r_dup_cov["meets_continuous_1s_observed_target"] is False


def test_r5_funding_and_owner_fee_redaction_f16_f17_f18() -> None:
    contract = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES"}
    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)

    # RED F16: Predicted funding masquerading as settled & missing settlement NOT zero-filled!
    r_pred = validate_r5_funding_history([{"rows": [{"symbol": "XAUUSDT", "settlement_timestamp_ms": 1794000000000, "funding_rate": "0.0001", "rate_kind": "PREDICTED_ESTIMATE", "units": "DECIMAL_FRACTION"}]}], contract, {"interval_ms": 28800_000})
    assert r_pred["valid"] is False and r_pred["predicted_records_rejected"] == 1
    r_gap = validate_r5_funding_history([{"rows": [
        {"symbol": "XAUUSDT", "settlement_timestamp_ms": 1794000000000, "funding_rate": "0.0001", "rate_kind": "ACTUAL_SETTLED", "units": "DECIMAL_FRACTION"},
        {"symbol": "XAUUSDT", "settlement_timestamp_ms": 1794057600000, "funding_rate": "0.0001", "rate_kind": "ACTUAL_SETTLED", "units": "DECIMAL_FRACTION"},
    ]}], contract, {"interval_ms": 28800_000})
    assert r_gap["valid"] is False and r_gap["funding_readiness"] == "PARTIAL" and r_gap["missing_settlements_count"] == 1 and r_gap["zero_fill_applied"] is False

    # F17 & F18: Default None -> UNKNOWN; public generic rejected; secret rejected; redacted QA rebate accepted as QA_FIXTURE only!
    assert validate_r5_owner_fee_confirmation(None, contract, now)["account_fee_readiness"] == "UNKNOWN_NOT_LOCALLY_VERIFIED"
    pub_doc = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "source_type": "PUBLIC_GENERIC_SCHEDULE", "maker_fee_rate": "0.0002", "taker_fee_rate": "0.0004", "effective_date_utc": "2026-11-20T00:00:00Z", "source_evidence_sha256": "a" * 64, "tier_or_region_scope": "VIP0", "redacted_no_secrets_confirmed": True}
    assert validate_r5_owner_fee_confirmation(pub_doc, contract, now)["valid"] is False
    sec_doc = {**pub_doc, "source_type": "OWNER_REDACTED_CONFIRMATION", "api_key": "bg_secret_12345"}
    assert validate_r5_owner_fee_confirmation(sec_doc, contract, now)["valid"] is False
    qa_doc = {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "source_type": "OWNER_REDACTED_CONFIRMATION", "maker_fee_rate": "-0.00005", "taker_fee_rate": "0.00035", "effective_date_utc": "2026-11-20T00:00:00Z", "source_evidence_sha256": "b" * 64, "tier_or_region_scope": "QA_TIER", "redacted_no_secrets_confirmed": True, "is_qa_fixture": True}
    ok_qa = validate_r5_owner_fee_confirmation(qa_doc, contract, now)
    assert ok_qa["valid"] is True and ok_qa["maker_is_rebate"] is True and ok_qa["evidence_class"] == "QA_FIXTURE" and ok_qa["account_fee_readiness"] == "UNKNOWN_NOT_LOCALLY_VERIFIED"

    # Coverage & execution fidelity register honest INCOMPLETE when QA only
    cov = build_r5_market_data_coverage_report({"ohlcv": {"evidence_class": "QA_FIXTURE", "distinct_ny_days_count": 2}})
    assert cov["observed_verified_ohlcv_ny_days"] == 0 and cov["dev_coverage_classification"] == "INSUFFICIENT_COVERAGE"
    reg = build_r5_execution_fidelity_register({"evidence_class": "MISSING"}, {"evidence_class": "MISSING"}, {"evidence_class": "MISSING"}, {"account_fee_readiness": "UNKNOWN_NOT_LOCALLY_VERIFIED", "evidence_class": "MISSING"}, cov)
    assert reg["execution_fidelity_status"] == "INCOMPLETE"
