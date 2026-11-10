"""
AURUM DESK — V17.9.3.1.2.11 UNIT, CONTRACT, AND NEGATIVE-CONTROL TEST SUITE
Tests P0-A (Verification Truth) and P0-B (Exit-Event Lineage) on real production code.
All synthetic inputs in this file are explicitly labeled TEST FIXTURES.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path
import pytest

from domain_calculator import CostAssumptions
from lab.replay_contracts import ReplayMarketEvent, ReplayPosition
from lab.replay_execution import (
    compute_closed_trade_accounting,
    evaluate_position_exit,
    submit_replay_order,
    try_fill_pending_order,
)
from services.shared_exit_evaluator import evaluate_order_exit
from services.v17_9_3_1_2_10_canonical_reconciliation_and_provenance import (
    analyze_15_distinct_recovered_trades_v17931210,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    CANONICAL_DATASET_SHA256_V17931211,
    CANONICAL_FILL_MODES_V17931211,
    CANONICAL_POLICIES_V17931211,
    EVIDENCE_ORIGIN_CLASSES_V17931211,
    EXIT_CAUSE_NORMALIZATION_MAP_V17931211,
    TRUTH_CLOSURE_REQUIRED_ARTIFACTS_V17931211,
    VERSION_V17931211,
    build_canonical_report_metrics_v17931211,
    build_exit_event_lineage_v17931211,
    build_function_callgraph_and_contracts_v17931211,
    evaluate_truth_closure_gates_v17931211,
    evaluate_v17931211_acceptance_gates,
    group_raw_exit_events_by_partition_v17931211,
    normalize_collected_test_identity_v17931211,
    read_exit_event_stream_strict_v17931211,
    recompute_realized_pnl_from_exit_events_v17931211,
    reconstruct_lineage_from_raw_stream_v17931211,
    render_truth_closure_readme_and_handover_v17931211,
    run_truth_closure_negative_controls_v01_to_v22_v17931211,
    run_verification_truth_negative_controls_v17931211,
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_execution_not_synthetic_v17931211,
    verify_exit_event_deduplication_v17931211,
    verify_exit_event_parentage_v17931211,
    verify_exit_fill_basis_v17931211,
    verify_exit_lineage_cross_adapter_parity_v17931211,
    verify_exit_portfolio_conservation_v17931211,
    verify_exit_state_machine_v17931211,
    verify_process_exit_and_timeout_v17931211,
    verify_raw_stream_vs_source_execution_v17931211,
    verify_strict_report_and_handover_consistency_v17931211,
)


# ==============================================================================
# 1. P0-A: VERIFICATION TRUTH & 10 NEGATIVE CONTROLS (a)..(j)
# ==============================================================================

def test_p0a_normalize_collected_test_identity_variants():
    assert (
        normalize_collected_test_identity_v17931211(
            "backend\\tests\\test_v17_5_exit_evidence.py::test_exit_evaluator_bid_ask"
        )
        == "backend/tests/test_v17_5_exit_evidence.py::test_exit_evaluator_bid_ask"
    )
    assert (
        normalize_collected_test_identity_v17931211(
            "tests/test_v16_4_cg01_replay_ledger.py::TestLedger::test_case[param-1]"
        )
        == "backend/tests/test_v16_4_cg01_replay_ledger.py::TestLedger::test_case[param-1]"
    )
    assert (
        normalize_collected_test_identity_v17931211(
            "backend.tests.test_v17_5_exit_evidence", "test_exit_evaluator_bid_ask"
        )
        == "backend/tests/test_v17_5_exit_evidence.py::test_exit_evaluator_bid_ask"
    )
    assert normalize_collected_test_identity_v17931211("") is None
    assert normalize_collected_test_identity_v17931211("not_a_test_node") is None


def test_p0a_all_10_negative_controls_rejected():
    res = run_verification_truth_negative_controls_v17931211()
    assert res["status"] == "PASS", f"Negative controls failed: {res['violations']}"
    assert res["controls_count"] == 10
    assert res["all_rejected"] is True
    expected_ids = {
        "A_FORGED_EXIT_ZERO_WITH_FAILING_JUNIT",
        "B_LOST_OR_DUPLICATED_JUNIT_TESTCASE",
        "C_TAMPERED_RAW_XML_WITH_OLD_SHA",
        "D_MISSING_SHARD_OR_ORPHANED_PID",
        "E_STALE_RUN_ID_OR_SOURCE_SHA",
        "F_COPIED_REUSED_WORKER_1_2_4_RESULTS",
        "G_TEST_TIMEOUT_MASKED_AS_SUCCESS",
        "H_SKIPPED_MANDATORY_TEST_BLOCKED",
        "I_MANIFEST_MISSING_MANDATORY_FILE_OR_FORGED_HASH",
        "J_PHASE_B_RECEIPT_WITHOUT_PHASE_C_REMOTE_READBACK",
    }
    observed_ids = {c["control_id"] for c in res["controls"]}
    assert observed_ids == expected_ids
    for c in res["controls"]:
        assert c["rejected"] is True
        assert len(c["violations_caught"]) >= 1


def test_p0a_function_callgraph_and_contracts_grounded():
    repo_root = Path(__file__).resolve().parents[2]
    cg = build_function_callgraph_and_contracts_v17931211(repo_root)
    assert cg["version"] == VERSION_V17931211
    assert cg["total_anchored_symbols"] >= 35
    symbols = {(c["file"], c["symbol"]) for c in cg["contracts"]}
    assert ("aurum-desk/backend/lab/replay_execution.py", "compute_closed_trade_accounting") in symbols
    assert ("aurum-desk/backend/services/shared_exit_evaluator.py", "evaluate_order_exit") in symbols
    assert ("aurum-desk/backend/services/v17_9_3_1_2_11_verification_truth_and_exit_lineage.py", "build_exit_event_lineage_v17931211") in symbols


# ==============================================================================
# 2. P0-B: SHARED EXIT EVALUATOR HARDENING & EDGE CASES
# ==============================================================================

def test_p0b_shared_exit_evaluator_rejects_nan_inf_bool_and_nonpositive_prices():
    order_long = {"direction": "LONG", "stop_loss": 4090.0, "take_profit": 4120.0, "entry_time": 1000}
    order_short = {"direction": "SHORT", "stop_loss": 4110.0, "take_profit": 4080.0, "entry_time": 1000}

    for invalid_px in (float("nan"), float("inf"), float("-inf"), 0.0, -4100.0, True, False, "4080"):
        d_long = evaluate_order_exit(order_long, {"bid": invalid_px, "ask": 4100.0, "evidence_id": "ev1"})
        assert d_long.triggered is False
        assert d_long.unavailable_reason == "MISSING_EXECUTABLE_SIDE"

        d_short = evaluate_order_exit(order_short, {"bid": 4100.0, "ask": invalid_px, "evidence_id": "ev1"})
        assert d_short.triggered is False
        assert d_short.unavailable_reason == "MISSING_EXECUTABLE_SIDE"

    # Invalid bar extremes (high < low or NaN)
    d_bar_bad = evaluate_order_exit(
        order_long,
        {"high": 4080.0, "low": 4100.0, "is_closed": True, "evidence_id": "ev_bad"},
    )
    assert d_bar_bad.triggered is False
    assert d_bar_bad.unavailable_reason == "INVALID_BAR_EXTREMES_OR_LEVELS"


def test_p0b_shared_exit_evaluator_rejects_partial_bar_pre_entry_and_missing_evidence_ref():
    order = {"direction": "LONG", "stop_loss": 4090.0, "take_profit": 4120.0, "entry_time": 5000}

    # 1. Partial unclosed bar -> never triggers close
    d_partial = evaluate_order_exit(
        order,
        {"high": 4125.0, "low": 4095.0, "is_closed": False, "timestamp": 6000, "evidence_id": "bar_partial"},
    )
    assert d_partial.triggered is False
    assert d_partial.unavailable_reason == "PARTIAL_UNCLOSED_BAR"

    # 2. Pre-entry quote/bar -> never triggers close
    d_pre = evaluate_order_exit(
        order,
        {"bid": 4085.0, "ask": 4085.5, "timestamp": 4000, "evidence_id": "tick_pre"},
    )
    assert d_pre.triggered is False
    assert d_pre.unavailable_reason == "PRE_ENTRY_QUOTE_OR_BAR"

    # 3. Future known_at_ms > decision_cutoff_ms -> rejected
    d_future = evaluate_order_exit(
        order,
        {"bid": 4085.0, "ask": 4085.5, "timestamp": 6000, "known_at_ms": 9000, "evidence_id": "tick_fut"},
        {"decision_cutoff_ms": 7000},
    )
    assert d_future.triggered is False
    assert d_future.unavailable_reason == "FUTURE_KNOWN_AT_VIOLATION"

    # 4. Missing evidence reference when require_evidence_reference=True -> rejected
    d_no_ev = evaluate_order_exit(
        order,
        {"bid": 4085.0, "ask": 4085.5, "timestamp": 6000},
        {"require_evidence_reference": True},
    )
    assert d_no_ev.triggered is False
    assert d_no_ev.unavailable_reason == "MISSING_EXIT_EVIDENCE_REFERENCE"


# ==============================================================================
# 3. P0-B: REPLAY EXECUTION DETERMINISM & SAME-TIMESTAMP NON-COLLISION
# ==============================================================================

def test_p0b_replay_execution_deterministic_ids_and_same_timestamp_non_collision():
    costs = CostAssumptions(taker_fee_rate=0.0004, maker_fee_rate=0.0002, slippage_usd=0.10)

    plan_a = {
        "setup_id": "setup_alpha",
        "direction": "LONG",
        "planned_entry": 4100.0,
        "planned_sl": 4090.0,
        "planned_tp": 4140.0,
        "quantity": 0.15,
        "planned_net_rr": 3.0,
    }
    plan_b = {
        "setup_id": "setup_beta",
        "direction": "SHORT",
        "planned_entry": 4100.0,
        "planned_sl": 4110.0,
        "planned_tp": 4060.0,
        "quantity": 0.15,
        "planned_net_rr": 3.0,
    }

    # Repeated order submission with identical inputs yields identical order_id
    ord_a1 = submit_replay_order(plan_a, session_id="NY-2026-10-08", decision_ms=1000)
    ord_a2 = submit_replay_order(plan_a, session_id="NY-2026-10-08", decision_ms=1000)
    ord_b1 = submit_replay_order(plan_b, session_id="NY-2026-10-08", decision_ms=1000)
    assert ord_a1.order_id == ord_a2.order_id
    assert ord_a1.order_id != ord_b1.order_id

    open_ev = ReplayMarketEvent(
        kind="OPEN",
        timestamp=2000,
        timeframe="1M",
        open_price=4100.0,
        high_price=4101.0,
        low_price=4099.0,
        close_price=4100.0,
        sequence=1,
    )
    pos_a1, post_a1, status_a1 = try_fill_pending_order(ord_a1, open_ev, costs, capital=1000.0, risk_pct=0.25)
    pos_a2, post_a2, status_a2 = try_fill_pending_order(ord_a2, open_ev, costs, capital=1000.0, risk_pct=0.25)
    pos_b1, post_b1, status_b1 = try_fill_pending_order(ord_b1, open_ev, costs, capital=1000.0, risk_pct=0.25)

    assert status_a1 == status_a2 == status_b1 == "FILLED"
    assert pos_a1.position_id == pos_a2.position_id
    assert post_a1["posting_id"] == post_a2["posting_id"]
    assert pos_a1.position_id != pos_b1.position_id

    # Two distinct positions closing at the exact same millisecond (exit_time=5000)
    acct_a = compute_closed_trade_accounting(pos_a1, exit_price=4089.9, exit_time=5000, exit_cause="STOP_LOSS", costs=costs)
    acct_b = compute_closed_trade_accounting(pos_b1, exit_price=4110.1, exit_time=5000, exit_cause="STOP_LOSS", costs=costs)

    assert acct_a["exit_event_id"] != acct_b["exit_event_id"]
    assert acct_a["postings"][0]["event_id"] != acct_b["postings"][0]["event_id"]
    assert acct_a["postings"][0]["posting_id"] != acct_b["postings"][0]["posting_id"]
    assert acct_a["postings"][1]["posting_id"] != acct_b["postings"][1]["posting_id"]

    # Second close attempt on already-closed position via evaluate_position_exit is rejected
    close_ev = ReplayMarketEvent(
        kind="CLOSE",
        timestamp=6000,
        timeframe="1M",
        open_price=4085.0,
        high_price=4088.0,
        low_price=4080.0,
        close_price=4082.0,
        sequence=2,
    )
    second_acct, second_status = evaluate_position_exit(pos_a1, close_ev, costs)
    assert second_acct is None
    assert second_status == "POSITION_ALREADY_CLOSED"


# ==============================================================================
# 4. P0-B: EXIT-EVENT LINEAGE GRAPH, VERIFIERS, AND TAMPERING CONTROLS
# ==============================================================================

def _build_fixture_episodes_and_bars():
    """TEST FIXTURE: 5 episodes covering WIN, LOSS, SAME_BAR_TIE, OPEN_AT_HORIZON, and UNFILLED."""
    bars = {
        100000: {"open_time": 100000, "close_time": 160000, "open": 4100.0, "high": 4101.0, "low": 4099.0, "close": 4100.0},
        160000: {"open_time": 160000, "close_time": 220000, "open": 4100.0, "high": 4125.0, "low": 4099.0, "close": 4121.0},
        300000: {"open_time": 300000, "close_time": 360000, "open": 4100.0, "high": 4101.0, "low": 4099.0, "close": 4100.0},
        360000: {"open_time": 360000, "close_time": 420000, "open": 4100.0, "high": 4112.0, "low": 4098.0, "close": 4111.0},
        500000: {"open_time": 500000, "close_time": 560000, "open": 4100.0, "high": 4101.0, "low": 4099.0, "close": 4100.0},
        560000: {"open_time": 560000, "close_time": 620000, "open": 4100.0, "high": 4125.0, "low": 4085.0, "close": 4095.0},
        700000: {"open_time": 700000, "close_time": 760000, "open": 4100.0, "high": 4102.0, "low": 4098.0, "close": 4101.0},
    }
    episodes = [
        # 1. Closed LONG WIN (entry 4100.20, TP 4120.00, qty 0.25)
        {
            "opportunity_id": "opp_fix_win_1",
            "episode_key": "ep_fix_win_1",
            "setup_episode_id": "set_1",
            "model_id": "SMC_M1_CONTINUATION",
            "direction": "LONG",
            "decision_timestamp_ms": 100000,
            "entry_fill_ts": 100000,
            "exit_fill_ts": 220000,
            "release_ts_ms": 220000,
            "bars_held": 2,
            "filled": True,
            "entry_reference": 4100.0,
            "entry_fill_price": 4100.20,
            "selected_sl": 4090.0,
            "take_profit": 4120.0,
            "exit_fill_price": 4120.0,
            "executed_quantity": 0.25,
            "planned_net_risk_usd": 2.50,
            "entry_fee_usd": round(4100.20 * 0.0004 * 0.25, 4),
            "exit_fee_usd": round(4120.00 * 0.0004 * 0.25, 4),
            "realized_gross_pnl_usd": round((4120.0 - 4100.20) * 0.25, 4),
            "realized_net_pnl_usd": round(
                (4120.0 - 4100.20) * 0.25
                - round(4100.20 * 0.0004 * 0.25, 4)
                - round(4120.00 * 0.0004 * 0.25, 4),
                4,
            ),
            "realized_r": round(
                (
                    (4120.0 - 4100.20) * 0.25
                    - round(4100.20 * 0.0004 * 0.25, 4)
                    - round(4120.00 * 0.0004 * 0.25, 4)
                )
                / 2.50,
                4,
            ),
            "outcome_label": "WIN",
            "exit_type": "WIN",
            "same_bar_sl_tp_tie": False,
            "execution_ledger": {"trigger_bar_ts": 160000, "multiplier": 1.0},
        },
        # 2. Closed SHORT LOSS (entry 4099.80, SL 4110.00 -> exit fill 4110.10, qty 0.20)
        {
            "opportunity_id": "opp_fix_loss_2",
            "episode_key": "ep_fix_loss_2",
            "setup_episode_id": "set_2",
            "model_id": "SMC_M1_FVG_CONTINUATION",
            "direction": "SHORT",
            "decision_timestamp_ms": 300000,
            "entry_fill_ts": 300000,
            "exit_fill_ts": 420000,
            "release_ts_ms": 420000,
            "bars_held": 2,
            "filled": True,
            "entry_reference": 4100.0,
            "entry_fill_price": 4099.80,
            "selected_sl": 4110.0,
            "take_profit": 4075.0,
            "exit_fill_price": 4110.10,
            "executed_quantity": 0.20,
            "planned_net_risk_usd": 2.40,
            "entry_fee_usd": round(4099.80 * 0.0004 * 0.20, 4),
            "exit_fee_usd": round(4110.10 * 0.0004 * 0.20, 4),
            "realized_gross_pnl_usd": round((4099.80 - 4110.10) * 0.20, 4),
            "realized_net_pnl_usd": round(
                (4099.80 - 4110.10) * 0.20
                - round(4099.80 * 0.0004 * 0.20, 4)
                - round(4110.10 * 0.0004 * 0.20, 4),
                4,
            ),
            "realized_r": round(
                (
                    (4099.80 - 4110.10) * 0.20
                    - round(4099.80 * 0.0004 * 0.20, 4)
                    - round(4110.10 * 0.0004 * 0.20, 4)
                )
                / 2.40,
                4,
            ),
            "outcome_label": "LOSS",
            "exit_type": "LOSS",
            "same_bar_sl_tp_tie": False,
            "execution_ledger": {"trigger_bar_ts": 360000, "multiplier": 1.0},
        },
        # 3. Same-bar SL+TP tie -> Conservative SL loss
        {
            "opportunity_id": "opp_fix_tie_3",
            "episode_key": "ep_fix_tie_3",
            "setup_episode_id": "set_3",
            "model_id": "SMC_M1_REVERSAL",
            "direction": "LONG",
            "decision_timestamp_ms": 500000,
            "entry_fill_ts": 500000,
            "exit_fill_ts": 620000,
            "release_ts_ms": 620000,
            "bars_held": 2,
            "filled": True,
            "entry_reference": 4100.0,
            "entry_fill_price": 4100.20,
            "selected_sl": 4090.0,
            "take_profit": 4120.0,
            "exit_fill_price": 4089.90,
            "executed_quantity": 0.20,
            "planned_net_risk_usd": 2.40,
            "entry_fee_usd": round(4100.20 * 0.0004 * 0.20, 4),
            "exit_fee_usd": round(4089.90 * 0.0004 * 0.20, 4),
            "realized_gross_pnl_usd": round((4089.90 - 4100.20) * 0.20, 4),
            "realized_net_pnl_usd": round(
                (4089.90 - 4100.20) * 0.20
                - round(4100.20 * 0.0004 * 0.20, 4)
                - round(4089.90 * 0.0004 * 0.20, 4),
                4,
            ),
            "realized_r": round(
                (
                    (4089.90 - 4100.20) * 0.20
                    - round(4100.20 * 0.0004 * 0.20, 4)
                    - round(4089.90 * 0.0004 * 0.20, 4)
                )
                / 2.40,
                4,
            ),
            "outcome_label": "LOSS",
            "exit_type": "LOSS_TIE_CONSERVATIVE",
            "same_bar_sl_tp_tie": True,
            "execution_ledger": {"trigger_bar_ts": 560000, "multiplier": 1.0, "same_bar_sl_tp_tie": True},
        },
        # 4. OPEN_AT_HORIZON censored episode (0 realized PnL)
        {
            "opportunity_id": "opp_fix_open_4",
            "episode_key": "ep_fix_open_4",
            "setup_episode_id": "set_4",
            "model_id": "SMC_M1_CONTINUATION",
            "direction": "LONG",
            "decision_timestamp_ms": 700000,
            "entry_fill_ts": 700000,
            "exit_fill_ts": None,
            "release_ts_ms": 760000,
            "bars_held": 1,
            "filled": True,
            "entry_reference": 4100.0,
            "entry_fill_price": 4100.20,
            "selected_sl": 4090.0,
            "take_profit": 4125.0,
            "exit_fill_price": None,
            "executed_quantity": 0.20,
            "planned_net_risk_usd": 2.40,
            "entry_fee_usd": round(4100.20 * 0.0004 * 0.20, 4),
            "exit_fee_usd": 0.0,
            "realized_gross_pnl_usd": 0.0,
            "realized_net_pnl_usd": 0.0,
            "realized_r": 0.0,
            "outcome_label": "OPEN_AT_HORIZON",
            "exit_type": "OPEN_AT_HORIZON",
            "execution_ledger": {
                "trigger_bar_ts": 700000,
                "open_at_horizon_mtm_separate": 0.12,
                "unresolved_reason": "MARK_TO_MARKET_CENSORED_AT_HORIZON",
            },
        },
        # 5. UNFILLED episode (entry gap)
        {
            "opportunity_id": "opp_fix_unfilled_5",
            "episode_key": "ep_fix_unfilled_5",
            "setup_episode_id": "set_5",
            "model_id": "SMC_M1_CONTINUATION",
            "direction": "SHORT",
            "decision_timestamp_ms": 800000,
            "entry_fill_ts": None,
            "exit_fill_ts": None,
            "release_ts_ms": 800000,
            "bars_held": 0,
            "filled": False,
            "entry_reference": 4100.0,
            "entry_fill_price": None,
            "selected_sl": 4110.0,
            "take_profit": 4075.0,
            "exit_fill_price": None,
            "executed_quantity": 0.0,
            "planned_net_risk_usd": 2.40,
            "entry_fee_usd": 0.0,
            "exit_fee_usd": 0.0,
            "realized_gross_pnl_usd": 0.0,
            "realized_net_pnl_usd": 0.0,
            "realized_r": 0.0,
            "outcome_label": "NO_FILL_ENTRY_GAP",
            "exit_type": "NO_FILL_ENTRY_GAP",
            "no_fill_reason": "ENTRY_BAR_GAP_EXCEEDS_THRESHOLD",
            "execution_ledger": {"filled": False, "no_fill_reason": "ENTRY_BAR_GAP_EXCEEDS_THRESHOLD"},
        },
    ]
    return episodes, bars


def test_p0b_exit_lineage_positive_and_corruption_negative_controls():
    episodes, bars = _build_fixture_episodes_and_bars()
    lineage = build_exit_event_lineage_v17931211(
        episodes,
        bars_1m_by_open_ts=bars,
        run_id="test_run_v11",
        tested_executable_sha="a" * 40,
    )

    p_res = verify_exit_event_parentage_v17931211(lineage)
    s_res = verify_exit_state_machine_v17931211(lineage)
    b_res = verify_exit_fill_basis_v17931211(lineage, bars_1m_by_open_ts=bars)
    d_res = verify_exit_event_deduplication_v17931211(lineage)
    r_res = recompute_realized_pnl_from_exit_events_v17931211(lineage)
    c_res = verify_exit_portfolio_conservation_v17931211(lineage)

    assert p_res["status"] == "PASS", p_res["violations"]
    assert s_res["status"] == "PASS", s_res["violations"]
    assert b_res["status"] == "PASS", b_res["violations"]
    assert d_res["status"] == "PASS", d_res["violations"]
    assert r_res["status"] == "PASS", r_res["violations"]
    assert c_res["status"] == "PASS", c_res["violations"]
    assert c_res["total_scheduled_episodes"] == 5
    assert c_res["closed_trades_count"] == 3
    assert c_res["open_at_horizon_count"] == 1
    assert c_res["unfilled_no_entry_count"] == 1

    # Negative Control 1: Missing parent event
    bad_parent = copy.deepcopy(lineage)
    bad_parent["all_events"][5]["parent_event_ids"] = ["evt_nonexistent_parent"]
    assert verify_exit_event_parentage_v17931211(bad_parent)["status"] == "FAIL"

    # Negative Control 2: Closed trade missing EXIT_TRIGGER
    bad_state = copy.deepcopy(lineage)
    bad_state["episode_chains"][0]["events"] = [
        e for e in bad_state["episode_chains"][0]["events"] if e["event_kind"] != "EXIT_TRIGGER"
    ]
    assert verify_exit_state_machine_v17931211(bad_state)["status"] == "FAIL"

    # Negative Control 3: Non-zero realized PnL on OPEN_AT_HORIZON episode
    bad_open = copy.deepcopy(lineage)
    bad_open["episode_chains"][3]["events"][-1]["payload"]["realized_net_pnl_usd"] = 5.0
    assert verify_exit_state_machine_v17931211(bad_open)["status"] == "FAIL"

    # Negative Control 4: Tampered exit fill price basis
    bad_price = copy.deepcopy(lineage)
    bad_price["closed_trade_identities"][0]["exit_fill_price"] = 4150.0
    assert verify_exit_fill_basis_v17931211(bad_price, bars_1m_by_open_ts=bars)["status"] == "FAIL"

    # Negative Control 5: Duplicate posting ID / double close
    bad_dedupe = copy.deepcopy(lineage)
    bad_dedupe["all_postings"].append(copy.deepcopy(bad_dedupe["all_postings"][0]))
    assert verify_exit_event_deduplication_v17931211(bad_dedupe)["status"] == "FAIL"

    # Negative Control 6: Tampered net PnL vs raw fills
    bad_pnl = copy.deepcopy(lineage)
    for ev in bad_pnl["episode_chains"][0]["events"]:
        if ev["event_kind"] == "POSITION_CLOSED":
            ev["payload"]["realized_net_pnl_usd"] += 1.25
    assert recompute_realized_pnl_from_exit_events_v17931211(bad_pnl)["status"] == "FAIL"


def test_p0b_cross_adapter_exit_parity_and_15_recovered_trades_field_binding():
    parity = verify_exit_lineage_cross_adapter_parity_v17931211()
    assert parity["status"] == "PASS", parity["violations"]
    assert parity["scenarios_tested"] == 5

    # Verify analyze_15_distinct_recovered_trades_v17931210 binds exit_fill_ts -> exit_timestamp_ms (not 0!)
    episodes, _ = _build_fixture_episodes_and_bars()
    ep_loss = copy.deepcopy(episodes[1])
    ep_loss["v1793127_final_feature_proof"] = {
        "htf_alignment_state": "ALIGNED",
        "proof_hash": "a" * 64,
        "source_snapshot_hash": "b" * 64,
        "causal_snapshot": {"features": []},
    }
    # Patch verify_feature_snapshot_v1793127 via valid minimal structure or inspect case fields
    from unittest.mock import patch
    with patch(
        "services.v17_9_3_1_2_10_canonical_reconciliation_and_provenance.verify_feature_snapshot_v1793127",
        return_value=True,
    ):
        res_15 = analyze_15_distinct_recovered_trades_v17931210(
            {
                "SI1_H1_H2": {"primary_episodes": [ep_loss]},
                "SI2_H3": {"primary_episodes": []},
                "SI3_H4": {"primary_episodes": [ep_loss]},
            },
            {},
        )
    assert res_15["unique_closed_trades_count"] == 1
    case = res_15["cases"][0]
    assert case["exit_timestamp_ms"] == 420000
    assert case["trigger_bar_ts"] == 360000
    assert case["fill_price"] == 4099.80
    assert case["exit_price"] == 4110.10
    assert case["quantity"] == 0.20


def test_p0b_late_reconciliation_idempotency_and_out_of_order_protection():
    """E09: Late or out-of-order quotes for an already closed order never reopen or double-close."""
    import json
    from types import SimpleNamespace
    from services.market_reconciliation_service import (
        CoverageAssessment,
        CoverageStatus,
        collect_terminal_candidates,
        resolve_reconciliation_window,
    )

    closed_order = SimpleNamespace(
        id="ord_closed_1",
        instrument="XAUUSDT",
        direction="LONG",
        entry_price=4100.0,
        stop_loss=4090.0,
        take_profit=4120.0,
        opened_at=1000,
        closed_at=5000,
        state="closed",
        exit_cause="SL_HIT",
        exit_price=4090.0,
        frozen_exit_context=json.dumps({
            "direction": "LONG",
            "stop_loss": 4090.0,
            "take_profit": 4120.0,
            "entry_price": 4100.0,
            "quantity": 0.25,
            "price_basis": "BID_ASK",
        }),
    )
    cov = CoverageAssessment(
        interval_start_ms=1000,
        interval_end_ms=10000,
        source="BITGET",
        epoch=1,
        price_basis="BID_ASK",
        coverage_status=CoverageStatus.COMPLETE,
        reason_code="VERIFIED_CONTINUOUS_PROOF",
    )

    # 1. Later exit candidate after closed_at=5000 -> action == NONE (idempotent ignore)
    cands_later = collect_terminal_candidates(
        closed_order,
        [{"evidence_id": "ev_late_tp", "source_timestamp": 6000, "bid": 4121.0, "ask": 4121.2, "source": "BITGET"}],
    )
    dec_later = resolve_reconciliation_window(closed_order, cands_later, cov)
    assert dec_later.action == "NONE"
    assert dec_later.reason_code == "ALREADY_CLOSED_NO_CONFLICT"

    # 2. Earlier conflicting exit candidate (t=3000 < closed_at=5000, TP_HIT vs SL_HIT) -> REVIEW_REQUIRED, never CLOSE/reopen
    cands_conflict = collect_terminal_candidates(
        closed_order,
        [{"evidence_id": "ev_earlier_tp", "source_timestamp": 3000, "bid": 4121.0, "ask": 4121.2, "source": "BITGET"}],
    )
    dec_conflict = resolve_reconciliation_window(closed_order, cands_conflict, cov)
    assert dec_conflict.action == "REVIEW_REQUIRED"
    assert dec_conflict.reason_code == "EARLIER_CONFLICTING_TERMINAL_ARRIVED_POST_CLOSE"


@pytest.mark.parametrize(
    "control_id",
    [
        "A_FORGED_EXIT_ZERO_WITH_FAILING_JUNIT",
        "B_LOST_OR_DUPLICATED_JUNIT_TESTCASE",
        "C_TAMPERED_RAW_XML_WITH_OLD_SHA",
        "D_MISSING_SHARD_OR_ORPHANED_PID",
        "E_STALE_RUN_ID_OR_SOURCE_SHA",
        "F_COPIED_REUSED_WORKER_1_2_4_RESULTS",
        "G_TEST_TIMEOUT_MASKED_AS_SUCCESS",
        "H_SKIPPED_MANDATORY_TEST_BLOCKED",
        "I_MANIFEST_MISSING_MANDATORY_FILE_OR_FORGED_HASH",
        "J_PHASE_B_RECEIPT_WITHOUT_PHASE_C_REMOTE_READBACK",
    ],
)
def test_p0a_parameterized_verification_truth_negative_control(control_id: str):
    res = run_verification_truth_negative_controls_v17931211()
    by_id = {c["control_id"]: c for c in res["controls"]}
    assert control_id in by_id
    assert by_id[control_id]["rejected"] is True
    assert len(by_id[control_id]["violations_caught"]) > 0


@pytest.mark.parametrize(
    "corruption_kind",
    [
        "CORRUPT_E01_BROKEN_PARENT_EVENT_ID",
        "CORRUPT_E02_MISSING_EXIT_TRIGGER_FOR_CLOSED_TRADE",
        "CORRUPT_E03_OPEN_HORIZON_NONZERO_REALIZED_PNL",
        "CORRUPT_E04_EXIT_FILL_PRICE_BASIS_DRIFT",
        "CORRUPT_E05_DUPLICATE_POSTING_ID",
        "CORRUPT_E06_DOUBLE_POSITION_CLOSE",
        "CORRUPT_E07_REALIZED_NET_PNL_ARITHMETIC_DRIFT",
        "CORRUPT_E08_FUTURE_EXIT_MARKET_EVENT_KNOWN_AT",
    ],
)
def test_p0b_parameterized_exit_lineage_negative_control(corruption_kind: str):
    episodes, bars = _build_fixture_episodes_and_bars()
    lineage = build_exit_event_lineage_v17931211(
        episodes,
        bars_1m_by_open_ts=bars,
        run_id="test_param_v11",
        tested_executable_sha="a" * 40,
    )
    bad = copy.deepcopy(lineage)

    if corruption_kind == "CORRUPT_E01_BROKEN_PARENT_EVENT_ID":
        bad["all_events"][4]["parent_event_ids"] = ["evt_missing_parent_999"]
        assert verify_exit_event_parentage_v17931211(bad)["status"] == "FAIL"
    elif corruption_kind == "CORRUPT_E02_MISSING_EXIT_TRIGGER_FOR_CLOSED_TRADE":
        bad["episode_chains"][0]["events"] = [
            e for e in bad["episode_chains"][0]["events"] if e["event_kind"] != "EXIT_TRIGGER"
        ]
        assert verify_exit_state_machine_v17931211(bad)["status"] == "FAIL"
    elif corruption_kind == "CORRUPT_E03_OPEN_HORIZON_NONZERO_REALIZED_PNL":
        bad["episode_chains"][3]["events"][-1]["payload"]["realized_net_pnl_usd"] = 1.0
        assert verify_exit_state_machine_v17931211(bad)["status"] == "FAIL"
    elif corruption_kind == "CORRUPT_E04_EXIT_FILL_PRICE_BASIS_DRIFT":
        bad["closed_trade_identities"][0]["exit_fill_price"] = 4999.99
        assert verify_exit_fill_basis_v17931211(bad, bars_1m_by_open_ts=bars)["status"] == "FAIL"
    elif corruption_kind == "CORRUPT_E05_DUPLICATE_POSTING_ID":
        bad["all_postings"].append(copy.deepcopy(bad["all_postings"][0]))
        assert verify_exit_event_deduplication_v17931211(bad)["status"] == "FAIL"
    elif corruption_kind == "CORRUPT_E06_DOUBLE_POSITION_CLOSE":
        pos_closed = [e for e in bad["episode_chains"][0]["events"] if e["event_kind"] == "POSITION_CLOSED"][0]
        dup_close = copy.deepcopy(pos_closed)
        dup_close["event_id"] = dup_close["event_id"] + "_dup"
        bad["episode_chains"][0]["events"].append(dup_close)
        bad["all_events"].append(dup_close)
        assert verify_exit_event_deduplication_v17931211(bad)["status"] == "FAIL"
    elif corruption_kind == "CORRUPT_E07_REALIZED_NET_PNL_ARITHMETIC_DRIFT":
        for ev in bad["episode_chains"][0]["events"]:
            if ev["event_kind"] == "POSITION_CLOSED":
                ev["payload"]["realized_net_pnl_usd"] += 2.50
        assert recompute_realized_pnl_from_exit_events_v17931211(bad)["status"] == "FAIL"
    elif corruption_kind == "CORRUPT_E08_FUTURE_EXIT_MARKET_EVENT_KNOWN_AT":
        for ev in bad["all_events"]:
            if ev["event_kind"] == "EXIT_MARKET_OBSERVATION":
                ev["observed_timestamp_ms"] = ev["known_at_timestamp_ms"] + 60000
        assert verify_exit_event_parentage_v17931211(bad)["status"] == "FAIL"


# ==============================================================================
# 5. TRUTH CLOSURE: RAW STREAM RECONSTRUCTION ACROSS ALL 30 PARTITIONS
# ==============================================================================

def test_truth_closure_raw_stream_reconstruction_and_30_partition_verification(tmp_path: Path):
    import gzip
    import json

    episodes, bars = _build_fixture_episodes_and_bars()
    all_raw_events = []
    source_exec_records = []

    for pol in CANONICAL_POLICIES_V17931211:
        for fm in CANONICAL_FILL_MODES_V17931211:
            lin = build_exit_event_lineage_v17931211(
                episodes,
                bars_1m_by_open_ts=bars,
                run_id="test_tc_30",
                tested_executable_sha="a" * 40,
                policy_id=pol,
                fill_mode=fm,
            )
            all_raw_events.extend(lin["all_events"])
            for idx, ep in enumerate(episodes):
                is_closed = idx < 3
                source_exec_records.append({
                    "policy_id": pol,
                    "fill_mode": fm,
                    "episode_order_idx": idx,
                    "opportunity_id": ep["opportunity_id"],
                    "episode_key": ep["episode_key"],
                    "direction": ep["direction"],
                    "decision_timestamp_ms": ep["decision_timestamp_ms"],
                    "entry_fill_ts": ep["entry_fill_ts"],
                    "exit_fill_ts": ep["exit_fill_ts"],
                    "trigger_bar_ts": (ep.get("execution_ledger") or {}).get("trigger_bar_ts"),
                    "filled": ep["filled"],
                    "is_closed": is_closed,
                    "terminal_state": "CLOSED" if is_closed else ("OPEN_AT_HORIZON" if idx == 3 else "UNFILLED"),
                    "outcome_label": ep["outcome_label"],
                    "exit_type": ep["exit_type"],
                    "entry_fill_price": ep["entry_fill_price"],
                    "exit_fill_price": ep["exit_fill_price"],
                    "selected_sl": ep["selected_sl"],
                    "take_profit": ep["take_profit"],
                    "executed_quantity": ep["executed_quantity"],
                    "planned_net_risk_usd": ep["planned_net_risk_usd"],
                    "entry_fee_usd": ep["entry_fee_usd"] if is_closed else 0.0,
                    "exit_fee_usd": ep["exit_fee_usd"] if is_closed else 0.0,
                    "realized_gross_pnl_usd": ep["realized_gross_pnl_usd"] if is_closed else 0.0,
                    "realized_net_pnl_usd": ep["realized_net_pnl_usd"] if is_closed else 0.0,
                    "realized_r": ep["realized_r"] if is_closed else 0.0,
                    "same_bar_sl_tp_tie": bool(ep.get("same_bar_sl_tp_tie", False)),
                })

    stream_path = tmp_path / "09_exit_event_lineage_ledger.jsonl.gz"
    with gzip.open(stream_path, "wt", encoding="utf-8") as gz:
        for ev in all_raw_events:
            gz.write(json.dumps(ev, sort_keys=True) + "\n")

    read_res = read_exit_event_stream_strict_v17931211(
        stream_path,
        expected_run_id="test_tc_30",
        expected_tested_sha="a" * 40,
    )
    assert read_res["status"] == "PASS", read_res["violations"]
    assert read_res["events_count"] == len(all_raw_events)

    group_res = group_raw_exit_events_by_partition_v17931211(read_res["events"])
    assert group_res["status"] == "PASS", group_res["violations"]
    assert group_res["total_partitions"] == 30

    reconstructed_partitions = {pol: {} for pol in CANONICAL_POLICIES_V17931211}
    for pol in CANONICAL_POLICIES_V17931211:
        for fm in CANONICAL_FILL_MODES_V17931211:
            reconstructed_partitions[pol][fm] = reconstruct_lineage_from_raw_stream_v17931211(
                group_res["partitions"][pol][fm],
                policy_id=pol,
                fill_mode=fm,
                run_id="test_tc_30",
                tested_sha="a" * 40,
            )

    src_ver = verify_raw_stream_vs_source_execution_v17931211(
        reconstructed_partitions,
        source_exec_records,
        bars_1m_by_open_ts=bars,
    )
    assert src_ver["status"] == "PASS", src_ver["violations"]
    assert src_ver["reconstructed_episodes_verified"] == 150

    # Tamper one event's evidence_origin to UNKNOWN -> must FAIL read_exit_event_stream_strict_v17931211
    bad_stream_path = tmp_path / "bad_09.jsonl.gz"
    bad_events = copy.deepcopy(read_res["events"])
    bad_events[0]["evidence_origin"] = "UNKNOWN"
    with gzip.open(bad_stream_path, "wt", encoding="utf-8") as gz:
        for ev in bad_events:
            gz.write(json.dumps(ev, sort_keys=True) + "\n")
    bad_read = read_exit_event_stream_strict_v17931211(
        bad_stream_path,
        expected_run_id="test_tc_30",
        expected_tested_sha="a" * 40,
    )
    assert bad_read["status"] == "FAIL"
    assert any("UNKNOWN_EVIDENCE_ORIGIN" in v for v in bad_read["violations"])


def test_truth_closure_process_truth_4_distinct_pids_and_ordered_digest_parity():
    from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

    ep_hashes = ["h_ep_0", "h_ep_1", "h_ep_2", "h_ep_3"]
    ord_sha = canonical_hash({"ordered_episode_hashes": ep_hashes, "total_realized_net_pnl_usd": -345.8971})
    mem_sha = canonical_hash({"sorted_episode_hashes": sorted(ep_hashes), "total_realized_net_pnl_usd": -345.8971})

    base_col = {"collect_pid": 1001, "collected_node_ids": ["backend/tests/test_a.py::test_1"]}
    base_shard = {
        "shards": [
            {
                "shard_id": "s0",
                "pid": 2002,
                "junit_xml_path": "s0.xml",
                "xml_sha256": "a" * 64,
            }
        ]
    }
    parity_good = {
        "independent_runs": [
            {
                "attempt_id": label,
                "worker_count": w_cnt,
                "workers_started": w_cnt,
                "workers_with_actual_nonempty_work": w_cnt,
                "workers_completed": w_cnt,
                "worker_pids": [5000 + idx * 10 + k for k in range(w_cnt)],
                "started_at_ms": 100 + idx * 100,
                "completed_at_ms": 180 + idx * 100,
                "canonical_semantic_sha256": ord_sha,
                "ordered_canonical_semantic_sha256": ord_sha,
                "membership_semantic_sha256": mem_sha,
                "ordered_episode_hashes": list(ep_hashes),
                "total_realized_net_pnl_usd": -345.8971,
                "raw_execution_log_sha256": f"{idx + 1:064x}",
            }
            for idx, (label, w_cnt) in enumerate([("w1", 1), ("w2", 2), ("w4", 4), ("w1_rep", 1)])
        ]
    }

    res_ok = verify_execution_not_synthetic_v17931211(
        collection_manifest=base_col,
        shard_manifest=base_shard,
        parity_manifest=parity_good,
    )
    assert res_ok["status"] == "PASS", res_ok["violations"]

    # Reject w=4 with only 3 distinct PIDs
    bad_3pids = copy.deepcopy(parity_good)
    bad_3pids["independent_runs"][2]["worker_pids"] = [5020, 5021, 5022]
    assert (
        verify_execution_not_synthetic_v17931211(
            collection_manifest=base_col,
            shard_manifest=base_shard,
            parity_manifest=bad_3pids,
        )["status"]
        == "FAIL"
    )

    # Reject permuted episode order in w=2
    bad_order = copy.deepcopy(parity_good)
    bad_order["independent_runs"][1]["ordered_episode_hashes"] = ["h_ep_1", "h_ep_0", "h_ep_2", "h_ep_3"]
    assert (
        verify_execution_not_synthetic_v17931211(
            collection_manifest=base_col,
            shard_manifest=base_shard,
            parity_manifest=bad_order,
        )["status"]
        == "FAIL"
    )


@pytest.fixture(scope="module")
def _cached_v01_to_v22_report():
    return run_truth_closure_negative_controls_v01_to_v22_v17931211()


@pytest.mark.parametrize(
    "control_id",
    [f"V{i:02d}" for i in range(1, 23)],
)
def test_truth_closure_all_22_negative_controls_v01_to_v22(control_id: str, _cached_v01_to_v22_report):
    res = _cached_v01_to_v22_report
    assert res["status"] == "PASS", res["violations"]
    assert res["controls_count"] == 22
    assert res["all_rejected"] is True
    assert res["temp_dirs_cleaned"] is True
    by_id = {c["short_id"]: c for c in res["controls"]}
    assert control_id in by_id
    ctrl = by_id[control_id]
    assert ctrl["rejected"] is True
    assert ctrl["detected_by_verifier"] is True
    assert len(ctrl["violations_caught"]) >= 1
