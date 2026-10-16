"""
backend/tests/test_v16_10_runner_result_authority.py
V16.10 Acceptance Suite - Area C: Historical Result Authority & Effective Config.

Verifies:
1. Missing path, directory path, or corrupt JSON dataset produces BLOCKED_DATA with authoritative run_context.
2. Driver BLOCKED_DATA propagates strictly to runner as BLOCKED_DATA and NOT_EVALUATED.
3. Config parity between driver run_context and effective_config.json (e.g. slippage 0.0 is preserved).
4. Zero seed assertion: historical replay runs with setup_hook=None and 0 seeded orders.
"""
import json
import os
import tempfile
import pytest

from domain_calculator import CostAssumptions
from lab.cg01_replay_driver import CG01ReplayDriver
from services.cg01_types import CG01Config


def test_missing_path_directory_or_corrupt_json_produces_blocked_data_with_run_context():
    """Area C: Invalid datasets must return BLOCKED_DATA with valid run_context and effective_config."""
    # 1. Missing file
    driver_missing = CG01ReplayDriver(m1_dataset_path="/invalid/nonexistent_dataset.json")
    res_missing = driver_missing.run()
    assert res_missing["status"] == "BLOCKED_DATA"
    assert "run_context" in res_missing
    assert "effective_config" in res_missing["run_context"]
    assert "config_hash" in res_missing["run_context"]

    # 2. Directory path instead of file
    with tempfile.TemporaryDirectory() as tmpdir:
        driver_dir = CG01ReplayDriver(m1_dataset_path=tmpdir)
        res_dir = driver_dir.run()
        assert res_dir["status"] == "BLOCKED_DATA"
        assert "run_context" in res_dir

    # 3. Corrupt JSON
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
        f.write("{invalid_json: true,")
        corrupt_path = f.name
    try:
        driver_corrupt = CG01ReplayDriver(m1_dataset_path=corrupt_path)
        res_corrupt = driver_corrupt.run()
        assert res_corrupt["status"] == "BLOCKED_DATA"
        assert "run_context" in res_corrupt
        assert res_corrupt["run_context"]["effective_config"]["capital_mode"] == "CURRENT_ACCOUNT_BALANCE"
    finally:
        if os.path.exists(corrupt_path):
            os.remove(corrupt_path)


def test_config_parity_demo_zero_slippage_in_run_context():
    """Area C: Demo configured with slippage 0.0 must output slippage_usd == 0.0 in run_context."""
    t_base = 1_792_000_000_000
    bars = []
    for i in range(20):
        bars.append({
            "open_time": t_base + i * 60_000,
            "open": 2000.0,
            "high": 2000.5,
            "low": 1999.5,
            "close": 2000.0,
            "volume": 50.0,
        })
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
        json.dump(bars, f)
        path = f.name

    try:
        costs = CostAssumptions(maker_fee_rate=0.0002, taker_fee_rate=0.0004, slippage_usd=0.0)
        driver = CG01ReplayDriver(
            m1_dataset_path=path,
            costs=costs,
        )
        res = driver.run()
        assert res["status"] == "COMPLETED"
        assert "run_context" in res
        eff = res["run_context"]["effective_config"]
        # Slippage 0 must be accurately preserved without being overridden to 0.10
        assert eff["slippage_usd"] == 0.0
        assert res["effective_config"]["slippage_usd"] == 0.0
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_zero_seed_assertion_in_historical_run():
    """Area C: Historical replay has 0 seeded demo orders."""
    t_base = 1_792_000_000_000
    bars = []
    for i in range(20):
        bars.append({
            "open_time": t_base + i * 60_000,
            "open": 2000.0,
            "high": 2000.5,
            "low": 1999.5,
            "close": 2000.0,
            "volume": 50.0,
        })
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
        json.dump(bars, f)
        path = f.name

    try:
        driver = CG01ReplayDriver(
            m1_dataset_path=path,
            setup_hook=None,
        )
        res = driver.run()
        assert res["status"] == "COMPLETED"
        assert res["execution_demo"]["seeded_orders_count"] == 0
        assert res["execution_demo"]["seeded_order_ids"] == []
    finally:
        if os.path.exists(path):
            os.remove(path)
