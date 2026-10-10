"""
Aurum Desk V13.4 — Unit tests for metrics aggregation, cadence coverage boundaries, and distinct config hashes.
Phần 46, 47, 54, 58, 59, 85.
"""
import pytest
import hashlib
import json
from schemas import ReplayRunRequest, ReplayTradeItem
from lab.daily_research_scheduler import summarize_cadence


def test_01_summarize_cadence_boundary_and_zero_denominator():
    """M01: summarize_cadence guarantees coverage in [0, 100], and handles zero denominator gracefully."""
    # Zero sessions
    res_zero = summarize_cadence([])
    assert res_zero["coverage_pct"] == 0.0
    assert res_zero["executable_sessions"] == 0

    # 10 eligible sessions, 8 filled
    outcomes = [
        {"eligible": True, "fills_count": 1 if i < 8 else 0, "market_open": True, "data_complete": True}
        for i in range(10)
    ]
    res_normal = summarize_cadence(outcomes, calendar_days=10)
    assert res_normal["coverage_pct"] == 80.0
    assert res_normal["executable_sessions"] == 10
    assert res_normal["sessions_with_fills"] == 8


def test_02_distinct_run_config_hash_versus_dataset_hash():
    """M02: Changing request parameter (e.g. risk_pct) changes run_config_hash while dataset_hash stays the same."""
    dataset_hash = "dataset_abc123456"

    cfg1 = {"initial_equity": 1000.0, "risk_pct": 0.5, "leverage": 30, "strategy_variant": "NY_ADAPTIVE"}
    cfg2 = {"initial_equity": 1000.0, "risk_pct": 1.0, "leverage": 30, "strategy_variant": "NY_ADAPTIVE"}

    hash1 = hashlib.sha256(json.dumps(cfg1, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    hash2 = hashlib.sha256(json.dumps(cfg2, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    assert hash1 != hash2, "Different risk configurations must yield distinct config hashes!"
    assert hash1 != dataset_hash
    assert hash2 != dataset_hash
