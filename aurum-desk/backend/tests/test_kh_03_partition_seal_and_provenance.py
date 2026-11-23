"""
aurum-desk/backend/tests/test_kh_03_partition_seal_and_provenance.py

KH-03.R1 Data Governance, Source Contracts, L1 BBO Validation, DEV/Purged-Val/Fresh-OOS
Partition Non-Contamination, DST Ambiguity, and C4 Trusted Capture / Escrow Readiness Tests.
"""
from __future__ import annotations

from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import (
    CANONICAL_DATASET_SHA256_KH02,
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
)
from services.kh_03_data_governance_service import (
    audit_kh03_collector_and_escrow_readiness,
    build_kh03_partition_manifest_and_noncontamination,
    validate_kh03_noncontamination_entry,
    validate_kh03_source_contracts,
)


def test_kh03_source_contracts_and_l1_bbo_validators() -> None:
    b0 = {"open_time": 1791500000000, "close_time": 1791500060000, "open": 4000.0, "high": 4002.0, "low": 3999.0, "close": 4001.0, "volume": 12.5, "is_closed": True}
    b1 = {"open_time": 1791500060000, "close_time": 1791500120000, "open": 4001.0, "high": 4003.0, "low": 4000.0, "close": 4002.0, "volume": 8.0, "is_closed": True}

    ok = validate_kh03_source_contracts(
        bars_by_tf={"1m": [b0, b1]},
        source_meta={"exchange": "bitget", "symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "response_code": "00000"},
        l1_quotes=[{"symbol": "XAUUSDT", "bid": 4001.10, "ask": 4001.30, "event_utc_ms": 1791500120000, "receive_utc_ms": 1791500120050}],
        response_utc_ms=1791500120100,
    )
    assert ok["valid"] is True and ok["data_quality_state"] == "VERIFIED_COMPLETE"

    # Contract substitution (XAUUSD spot) -> rejected
    bad_sym = validate_kh03_source_contracts(
        bars_by_tf={"1m": [b0]},
        source_meta={"exchange": "bitget", "symbol": "XAUUSD", "product_type": "SPOT"},
    )
    assert bad_sym["valid"] is False and bad_sym["data_quality_state"] == "SCHEMA_OR_CONTRACT_DRIFT"

    # Unclosed bar & close_time > response_utc_ms -> rejected
    assert validate_kh03_source_contracts(bars_by_tf={"1m": [b0]}, response_utc_ms=b0["close_time"] - 1)["valid"] is False
    assert validate_kh03_source_contracts(bars_by_tf={"1m": [dict(b0, is_closed=False)]})["valid"] is False

    # Conflicting duplicate bar, reverse chronology unproven, incomplete paging -> rejected
    assert validate_kh03_source_contracts(bars_by_tf={"1m": [b0, dict(b0, close=4001.5)]})["valid"] is False
    assert validate_kh03_source_contracts(bars_by_tf={"1m": [b0]}, paging_meta={"raw_order": "DESC", "reverse_chronology_normalized_with_proof": False})["valid"] is False
    assert validate_kh03_source_contracts(bars_by_tf={"1m": [b0]}, paging_meta={"has_more": True, "pagination_complete": False})["valid"] is False

    # L1 BBO failures: crossed book, stale quote, tick misaligned, synthetic passed as observed
    assert validate_kh03_source_contracts(bars_by_tf={"1m": [b0]}, l1_quotes=[{"symbol": "XAUUSDT", "bid": 4002.0, "ask": 4001.0, "event_utc_ms": 1791500060000, "receive_utc_ms": 1791500060010}])["valid"] is False
    assert validate_kh03_source_contracts(bars_by_tf={"1m": [b0]}, l1_quotes=[{"symbol": "XAUUSDT", "bid": 4001.0, "ask": 4001.2, "event_utc_ms": 1791500000000, "receive_utc_ms": 1791500060000}])["valid"] is False
    assert validate_kh03_source_contracts(bars_by_tf={"1m": [b0]}, l1_quotes=[{"symbol": "XAUUSDT", "bid": 4001.003, "ask": 4001.20, "event_utc_ms": 1791500060000, "receive_utc_ms": 1791500060010}])["valid"] is False
    assert validate_kh03_source_contracts(bars_by_tf={"1m": [b0]}, l1_quotes=[{"symbol": "XAUUSDT", "bid": 4001.0, "ask": 4001.2, "event_utc_ms": 1791500060000, "receive_utc_ms": 1791500060010, "is_synthetic": True}])["valid"] is False


def test_kh03_partition_noncontamination_and_collector_escrow_readiness(tmp_path: Path) -> None:
    # Reused SHAs, embargo-1ms, and peeked SHAs CANNOT become fresh OOS
    assert validate_kh03_noncontamination_entry(
        dataset_sha256=CANONICAL_DATASET_SHA256_KH02,
        target_role="FUTURE_FRESH_SEALED_UNTOUCHED",
        start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000,
        end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 120_000,
    )["admitted"] is False
    assert validate_kh03_noncontamination_entry(
        dataset_sha256=KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
        target_role="FUTURE_FRESH_SEALED_UNTOUCHED",
        start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000,
        end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 120_000,
    )["admitted"] is False
    peek = validate_kh03_noncontamination_entry(
        dataset_sha256="1" * 64,
        target_role="FUTURE_FRESH_SEALED_UNTOUCHED",
        start_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 60_000,
        end_utc_ms=R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS + 120_000,
        peeked_shas=("1" * 64,),
    )
    assert peek["admitted"] is False and peek["assigned_classification"] == "CONTAMINATED_PEEKED_NOT_FRESH"

    # Full partition manifest using compact synthetic bars
    bars = [{"open_time": 1791500000000 + i * 60_000, "close_time": 1791500060000 + i * 60_000, "open": 4000.0, "high": 4001.0, "low": 3999.0, "close": 4000.5} for i in range(400)]
    p_doc = build_kh03_partition_manifest_and_noncontamination(repo_root=REPO_ROOT, datasets={"1m": bars}, dataset_sha256=CANONICAL_DATASET_SHA256_KH02)
    assert p_doc["status"] == "PASS"
    assert p_doc["OOS_EVALUATIONS"] == 0
    assert p_doc["embargo_minutes"] >= 240
    assert p_doc["noncontamination_controls"]["all_passed"] is True
    assert p_doc["kh04_prospective_oos_power_requirements"]["fixed_30_trades_proves_edge"] is False

    # Collector & escrow offline readiness: default network OFF, real OOS count == 0
    f08, f09 = audit_kh03_collector_and_escrow_readiness(tmp_path)
    assert f08["status"] == "PASS"
    assert f08["network_capture_enabled"] is False
    assert f08["live_network_capture_executed"] is False
    assert f08["LIVE_NETWORK_VALIDATION"] == "NOT_RUN_BY_DESIGN"
    assert f09["status"] == "PASS"
    assert f09["real_oos_escrow_final_record_count"] == 0
    assert f09["qa_fixture_refused_by_real_oos_escrow"] is True
    assert f09["unanchored_worm_verified"] is False
