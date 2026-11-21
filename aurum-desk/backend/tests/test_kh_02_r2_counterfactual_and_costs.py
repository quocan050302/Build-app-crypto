"""
Dedicated KH-02.R2 unit tests: chronological 1-position 3-fill paired counterfactual replays,
causal fill-time net risk & realized tail audit, fixed-fill same-ID Decimal repricing,
and full-universe dynamic rerisked cost rerun.
"""
from __future__ import annotations

import pytest

from services.kh_02_entry_foundation_research_service import build_kh02_asof_mtf_features
from services.kh_02_r2_research_integrity_service import (
    audit_r2_fill_time_net_risk_and_realized_tail,
    build_r2_causal_unswept_level_ledger,
    evaluate_causal_fill_time_policy_single_trade,
    replay_r2_same_opportunity_paired_counterfactuals,
    reprice_r2_fixed_fills_cost_counterfactual,
    rerun_r2_full_universe_by_cost_scenario,
    validate_r2_cost_scope_honesty,
)


def _synth_multi_tf(n_m1: int = 120) -> dict:
    base_ts = 1_792_080_000_000
    def _mk(tf_ms: int, cnt: int) -> list:
        out = []
        st = base_ts - cnt * tf_ms
        for i in range(cnt):
            o_t = st + i * tf_ms
            cyc = 3.5 if (i % 8 == 3) else (-3.0 if (i % 8 == 7) else 0.0)
            px = 4000.0 + 0.10 * i + cyc
            out.append(
                {
                    "timestamp": o_t,
                    "open_time": o_t,
                    "close_time": o_t + tf_ms,
                    "open": round(px, 2),
                    "high": round(px + 1.5, 2),
                    "low": round(px - 1.4, 2),
                    "close": round(px + 0.5, 2),
                    "volume": 30.0,
                    "is_closed": True,
                }
            )
        return out

    return {"1m": _mk(60_000, n_m1), "15m": _mk(900_000, 35), "1H": _mk(3_600_000, 35)}


def test_causal_fill_time_policy_all_edge_cases_and_no_future_high_low() -> None:
    # 1. Clean accept (qty=0.35 -> 3.20*0.35 + 1.12 + 0.035 = 2.275 <= 2.50, net_rr = 2.55 >= 2.0 at tp=4020)
    ok = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3997.0, take_profit=4020.0, decision_qty=0.35
    )
    assert ok["accepted"] is True
    assert ok["fill_time_action"] == "ACCEPTED_AS_SIZED"
    assert float(ok["fill_time_planned_net_risk_usd"]) <= 2.50

    # 2. Overnight gap past SL rejected
    gap_sl = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=3995.0, selected_sl=3997.0, take_profit=4010.0, decision_qty=0.40
    )
    assert gap_sl["accepted"] is False
    assert gap_sl["fill_time_action"] == "REJECTED_FILL_TIME_GAPPED_PAST_SL"

    # 3. Gap past TP rejected
    gap_tp = evaluate_causal_fill_time_policy_single_trade(
        direction="SHORT", decision_entry=4000.0, fill_bar_open=3988.0, selected_sl=4003.0, take_profit=3990.0, decision_qty=0.40
    )
    assert gap_tp["accepted"] is False
    assert gap_tp["fill_time_action"] == "REJECTED_FILL_TIME_GAPPED_PAST_TP"

    # 4. Adverse open jump breaches $2.50 on original qty -> causally resized <= $2.50 or rejected
    jump = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=4002.0, selected_sl=3997.0, take_profit=4020.0, decision_qty=0.80
    )
    if jump["accepted"]:
        assert jump["fill_time_action"] == "RESIZED_CAUSALLY_AT_FILL_TIME"
        assert float(jump["fill_time_planned_net_risk_usd"]) <= 2.50 + 1e-6
        assert float(jump["final_qty"]) < 0.80

    # 5. Min-lot 0.01 exceeds $2.50 cap -> rejected
    min_lot = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3500.0, take_profit=5500.0, decision_qty=0.01
    )
    assert min_lot["accepted"] is False
    assert min_lot["fill_time_action"] == "REJECTED_AT_FILL_TIME_MIN_LOT_EXCEEDS_RISK_CAP"

    # 6. Future High/Low peek forbidden
    with pytest.raises(ValueError, match="FUTURE_FILL_BAR_HIGH_LOW_FORBIDDEN"):
        evaluate_causal_fill_time_policy_single_trade(
            direction="LONG", decision_entry=4000.0, fill_bar_open=4000.0, selected_sl=3997.0, take_profit=4010.0, decision_qty=0.40, forbidden_future_fields_present=True
        )


def test_paired_replay_rejects_future_known_at_and_enforces_no_overlap() -> None:
    ds = _synth_multi_tf(100)
    b20 = ds["1m"][20]
    b21 = ds["1m"][21]
    c_good_1 = {
        "opportunity_id": "op_20",
        "setup_episode_id": "ep_20",
        "direction": "LONG",
        "checkpoint_index_1m": 20,
        "decision_timestamp_ms": b20["close_time"],
        "known_at_ms": b20["close_time"],
        "entry_reference": b20["close"],
        "selected_sl": round(b20["close"] - 2.0, 2),
        "take_profit": round(b20["close"] + 6.0, 2),
        "hypothetical_quantity": 0.50,
        "planned_net_risk_usd": 1.60,
        "net_rr": 2.40,
        "ny_date": "2026-10-15",
    }
    c_good_2 = dict(c_good_1, opportunity_id="op_21", checkpoint_index_1m=21, decision_timestamp_ms=b21["close_time"], known_at_ms=b21["close_time"])

    rep = replay_r2_same_opportunity_paired_counterfactuals(
        datasets=ds,
        r1_candidates={"arm_candidates": {"B2_H1": [c_good_1]}},
        r2_candidates={"arm_candidates": {"B2_H1": [c_good_1, c_good_2]}},
        arms=("B2_H1",),
    )
    assert rep["status"] == "PASS"
    assert rep["total_overlap_violations"] == 0

    # Fill-time audit runs cleanly on replayed episodes
    ft_aud = audit_r2_fill_time_net_risk_and_realized_tail(datasets=ds, r2_replays=rep["r2_replays"], arms=("B2_H1",))
    assert ft_aud["status"] == "PASS"
    assert ft_aud["decision_time_cap_violations"] == 0
    assert ft_aud["post_fill_policy_cap_violations"] == 0

    # Future known_at_ms MUST fail closed
    c_future = dict(c_good_1, known_at_ms=b20["close_time"] + 1)
    with pytest.raises(ValueError, match="CAUSALITY_VIOLATION_IN_REPLAY_INPUT"):
        replay_r2_same_opportunity_paired_counterfactuals(
            datasets=ds,
            r1_candidates={"arm_candidates": {"B2_H1": []}},
            r2_candidates={"arm_candidates": {"B2_H1": [c_future]}},
            arms=("B2_H1",),
        )


def test_fixed_fill_decimal_exactness_and_full_universe_rerisk_scope_separation() -> None:
    fake_replays = {
        "B2_H1": {
            "episodes_by_mode": {
                "NEXT_BAR_OPEN_BID_ASK": [
                    {
                        "opportunity_id": "t1",
                        "filled": True,
                        "outcome_label": "WIN",
                        "realized_gross_pnl_usd": 6.0000,
                        "total_fees_usd": 0.4000,
                        "realized_net_pnl_usd": 5.6000,
                        "executed_quantity": 0.50,
                        "planned_net_risk_usd": 2.40,
                    },
                    {
                        "opportunity_id": "t2",
                        "filled": True,
                        "outcome_label": "LOSS",
                        "realized_gross_pnl_usd": -2.0000,
                        "total_fees_usd": 0.4000,
                        "realized_net_pnl_usd": -2.4000,
                        "executed_quantity": 0.50,
                        "planned_net_risk_usd": 2.40,
                    },
                ]
            }
        }
    }
    ff = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=fake_replays, arms=("B2_H1",))
    assert ff["status"] == "PASS"
    assert ff["zero_dropped_ids_verified"] is True
    a1 = ff["arms"]["B2_H1"]
    assert a1["denominator_closed_trades"] == 2
    assert a1["gross_pnl_1x_usd"] == 4.0
    assert a1["1x_base_0p0004"]["fees_usd"] == 0.8
    assert a1["1x_base_0p0004"]["net_pnl_usd"] == 3.2
    # At 0.0006 (1.5x fee): fees = 1.2, net = 4.0 - 1.2 = 2.8
    assert a1["fee_only_0p0006"]["fees_usd"] == 1.2
    assert a1["fee_only_0p0006"]["net_pnl_usd"] == 2.8
    # At 2x all-in: gross(4.0) - extra_ss(0.40*1.0=0.40) - 2*fees(1.60) = 2.0
    assert a1["all_in_2x_fixed_fill"]["net_pnl_usd"] == 2.0
    # At 3x all-in: gross(4.0) - 2*extra_ss(0.80) - 3*fees(2.40) = 0.8
    assert a1["all_in_3x_fixed_fill"]["net_pnl_usd"] == 0.8

    ok_ff, errs_ff = validate_r2_cost_scope_honesty(ff)
    assert ok_ff is True and errs_ff == []

    # Full-universe dynamic rerisk runs cleanly on synthetic dataset
    ds = _synth_multi_tf(120)
    mtf = build_kh02_asof_mtf_features(ds, warmup_m1=30)
    led = build_r2_causal_unswept_level_ledger(ds, warmup_m1=30, run_t10_future_mutation_proof=False)
    fu = rerun_r2_full_universe_by_cost_scenario(
        datasets=ds, mtf_bundle=mtf, ledger_bundle=led, arms=("B2_H1", "NC_NULL_TRIGGER")
    )
    assert fu["status"] == "PASS"
    assert fu["population_mode"] == "FULL_RERISKED_DYNAMIC_POPULATION"
    assert fu["starts_from_full_raw_opportunity_stream"] is True
    assert fu["conservation_verified_all_scenarios"] is True
    assert fu["total_overlap_violations"] == 0
    assert set(fu["scenarios"].keys()) == {
        "1X_BASE_0p0004",
        "FEE_ONLY_0p0006",
        "2X_ALL_COST_0p0004",
        "2X_ALL_COST_0p0006",
        "3X_ALL_COST_0p0004",
        "3X_ALL_COST_0p0006",
    }
    ok_fu, errs_fu = validate_r2_cost_scope_honesty(fu)
    assert ok_fu is True and errs_fu == []
