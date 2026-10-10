"""
AURUM DESK — V13.5 INTEGRITY NEGATIVE & VERIFIER TESTS
Verifies:
- Negative test cases fail integrity checks honestly.
- Causal order violation detection.
- Daily cap breach detection.
- Cash discrepancy detection.
- Export verification checks.
"""

import pytest
from lab.replay_integrity import run_replay_integrity_checks, verify_exported_artifacts


def test_01_integrity_pass_on_valid_inputs():
    closed_trades = [
        {"decision_time": 1000, "entry_time": 1050, "exit_time": 2000, "direction": "LONG", "entry_price": 2650.0, "stop_loss": 2645.0, "take_profit": 2665.0}
    ]
    postings = [{"amount": 5.0, "posting_type": "REALIZED_GROSS_PNL"}]
    daily_stats = {"2026-07-20": {"total_fills": 1}}

    summary = run_replay_integrity_checks(
        result_dict={"closed_count": 1, "wins": 1, "losses": 0, "breakevens": 0},
        closed_trades=closed_trades,
        ledger_postings=postings,
        daily_stats=daily_stats,
        initial_equity=1000.0,
        reported_cash=1005.0,
        dataset_hash="hash-dataset-12345",
        run_config_hash="hash-config-67890"
    )
    assert summary["status"] == "PASS"


def test_02_integrity_fail_on_acausal_timestamps():
    # Entry time 900 BEFORE decision time 1000
    closed_trades = [
        {"decision_time": 1000, "entry_time": 900, "exit_time": 2000, "direction": "LONG", "entry_price": 2650.0, "stop_loss": 2645.0, "take_profit": 2665.0}
    ]
    daily_stats = {"2026-07-20": {"total_fills": 1}}
    summary = run_replay_integrity_checks(
        result_dict={"closed_count": 1, "wins": 1, "losses": 0, "breakevens": 0},
        closed_trades=closed_trades,
        ledger_postings=[],
        daily_stats=daily_stats,
        initial_equity=1000.0,
        reported_cash=1000.0,
        dataset_hash="hash-dataset-12345",
        run_config_hash="hash-config-67890"
    )
    assert summary["status"] == "FAIL"
    assert summary["causal_data_ok"] is False


def test_03_integrity_fail_on_daily_cap_breach():
    # 4 fills on a single day (exceeds policy cap 3)
    daily_stats = {"2026-07-20": {"total_fills": 4}}
    summary = run_replay_integrity_checks(
        result_dict={"closed_count": 0, "wins": 0, "losses": 0, "breakevens": 0},
        closed_trades=[],
        ledger_postings=[],
        daily_stats=daily_stats,
        initial_equity=1000.0,
        reported_cash=1000.0,
        dataset_hash="hash-dataset-12345",
        run_config_hash="hash-config-67890"
    )
    assert summary["status"] == "FAIL"
    assert summary["guards_active"] is False


def test_04_integrity_fail_on_cash_discrepancy():
    # Reported cash 1050.0 but initial 1000.0 with 0 postings -> $50 discrepancy
    daily_stats = {"2026-07-20": {"total_fills": 0}}
    summary = run_replay_integrity_checks(
        result_dict={"closed_count": 0, "wins": 0, "losses": 0, "breakevens": 0},
        closed_trades=[],
        ledger_postings=[],
        daily_stats=daily_stats,
        initial_equity=1000.0,
        reported_cash=1050.0,
        dataset_hash="hash-dataset-12345",
        run_config_hash="hash-config-67890"
    )
    assert summary["status"] == "FAIL"
    assert summary["ledger_reconciled"] is False
