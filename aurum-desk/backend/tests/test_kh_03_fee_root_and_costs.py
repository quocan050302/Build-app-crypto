"""
aurum-desk/backend/tests/test_kh_03_fee_root_and_costs.py

KH-03.R1 P0 Root-Fee Extraction, Authoritative Ledger Contracts, 6-Scenario Streaming
Per-Episode Reconciliation, and Old/New C4 Compatibility Tests.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import gzip
import json
from pathlib import Path
import sys
from typing import Any, Dict

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_entry_foundation_research_service import build_kh02_asof_mtf_features
from services.kh_02_r2_c4_risk_tail_evidence import reconcile_c4_full_universe_cost_fees
from services.kh_02_r2_research_integrity_service import (
    _replay_chronological_custom_cost_scenario,
    build_r2_causal_unswept_level_ledger,
    build_r2_target_repair_full_opportunity_conservation,
    reprice_r2_fixed_fills_cost_counterfactual,
    replay_r2_same_opportunity_paired_counterfactuals,
    rerun_r2_full_universe_by_cost_scenario,
)
from services.kh_03_fee_integrity_service import (
    build_kh03_six_scenario_fee_ledger_and_reconciliation,
    extract_authoritative_ledger_fees_kh03,
    validate_kh03_fee_ledger_independently,
)
from services.v17_9_2_structural_stop_intelligence import simulate_v17931_trade_execution_net


def test_kh03_extract_authoritative_ledger_fees_contracts_and_negatives() -> None:
    # Genuine simulator closed trade
    base_ts = 1791500000000
    fwd_bars = [
        {"open_time": base_ts + 60_000, "close_time": base_ts + 120_000, "open": 4000.0, "high": 4001.0, "low": 3992.0, "close": 3993.0},
        {"open_time": base_ts + 120_000, "close_time": base_ts + 180_000, "open": 3993.0, "high": 3994.0, "low": 3991.0, "close": 3992.0},
    ]
    led = simulate_v17931_trade_execution_net(
        variant="KH03_UNIT_SIM",
        episode_key="kh03_ep_0",
        setup_id="setup_0",
        model_id="KH03_TEST",
        direction="LONG",
        decision_ts=base_ts,
        checkpoint_index_1m=0,
        planned_entry=4000.0,
        planned_sl=3995.0,
        planned_tp=4015.0,
        forward_bars=fwd_bars,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
        spread_usd=0.20,
        slippage_usd=0.10,
        taker_fee_rate=0.0004,
        maker_fee_rate=0.0002,
    )
    assert led.filled is True
    ld = led.to_dict()
    assert "total_fees_usd" not in ld  # Original root bug confirmed!
    assert ld["entry_fee_usd"] > 0 and ld["exit_fee_usd"] > 0

    ex = extract_authoritative_ledger_fees_kh03(ld, filled=True, outcome_label="LOSS")
    assert ex["total_fees_usd"] == pytest.approx(ld["entry_fee_usd"] + ld["exit_fee_usd"], abs=1e-4)
    assert ex["gross_fee_net_residual_usd"] <= 0.001
    assert ex["accrued_open_entry_fee_usd"] == 0.0

    # Open-at-horizon keeps entry fee separately accrued without polluting closed realized total_fees_usd
    open_ex = extract_authoritative_ledger_fees_kh03(
        {"entry_fee_usd": 0.48, "exit_fee_usd": 0.0, "realized_gross_pnl_usd": 0.0, "realized_net_pnl_usd": 0.0},
        filled=True,
        outcome_label="OPEN_AT_HORIZON",
    )
    assert open_ex["total_fees_usd"] == 0.0
    assert open_ex["accrued_open_entry_fee_usd"] == 0.48

    # Unfilled trade has zero fees
    unf_ex = extract_authoritative_ledger_fees_kh03({"entry_fee_usd": 0.0, "exit_fee_usd": 0.0}, filled=False, outcome_label="NO_FILL")
    assert unf_ex["total_fees_usd"] == 0.0

    # Negative controls: missing exit fee, negative, NaN, Inf, double-counted conflict, gross-fee-net drift, unfilled nonzero, open nonzero exit
    with pytest.raises(ValueError, match="MISSING_MANDATORY_EXIT_FEE"):
        extract_authoritative_ledger_fees_kh03({"entry_fee_usd": 0.5, "realized_gross_pnl_usd": -1.0, "realized_net_pnl_usd": -1.5}, filled=True, outcome_label="LOSS")
    with pytest.raises(ValueError, match="NEGATIVE_FEE_FORBIDDEN"):
        extract_authoritative_ledger_fees_kh03(dict(ld, entry_fee_usd=-0.01), filled=True, outcome_label="LOSS")
    with pytest.raises(ValueError, match="NON_FINITE"):
        extract_authoritative_ledger_fees_kh03(dict(ld, exit_fee_usd=float("nan")), filled=True, outcome_label="LOSS")
    with pytest.raises(ValueError, match="NON_FINITE"):
        extract_authoritative_ledger_fees_kh03(dict(ld, entry_fee_usd=float("inf")), filled=True, outcome_label="LOSS")
    with pytest.raises(ValueError, match="LEDGER_FEE_COMPONENT_MISMATCH"):
        extract_authoritative_ledger_fees_kh03(dict(ld, total_fees_usd=round((ld["entry_fee_usd"] + ld["exit_fee_usd"]) * 2.0, 4)), filled=True, outcome_label="LOSS")
    with pytest.raises(ValueError, match="CLOSED_TRADE_GROSS_FEE_NET_INCONSISTENCY"):
        extract_authoritative_ledger_fees_kh03(dict(ld, realized_net_pnl_usd=ld["realized_gross_pnl_usd"]), filled=True, outcome_label="LOSS")
    with pytest.raises(ValueError, match="UNFILLED_TRADE_NONZERO_FEE"):
        extract_authoritative_ledger_fees_kh03({"entry_fee_usd": 0.25, "exit_fee_usd": 0.0}, filled=False, outcome_label="NO_FILL")
    with pytest.raises(ValueError, match="NON_CLOSED_TRADE_NONZERO_EXIT_FEE"):
        extract_authoritative_ledger_fees_kh03({"entry_fee_usd": 0.25, "exit_fee_usd": 0.25}, filled=True, outcome_label="OPEN_AT_HORIZON")


def test_kh03_root_fix_eliminates_zero_fee_display_and_streams_ledger(tmp_path: Path) -> None:
    from services.kh_02_r2_research_integrity_service import (
        CANONICAL_DATASET_REL_PATH,
        load_r2_canonical_dataset_readonly,
    )

    full_ds, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
    # Deterministic real-market 4,500-bar slice (contains genuine closed trades + open-at-horizon episodes!)
    m1_s = list(full_ds["1m"])[:4500]
    max_ts = int(m1_s[-1]["close_time"])
    ds = {
        tf: (m1_s if tf == "1m" else [b for b in list(bars) if int(b["open_time"]) <= max_ts])
        for tf, bars in full_ds.items()
    }
    mtf = build_kh02_asof_mtf_features(ds, warmup_m1=30)
    led_b = build_r2_causal_unswept_level_ledger(ds, warmup_m1=30, run_t10_future_mutation_proof=False)
    r2_cands = build_r2_target_repair_full_opportunity_conservation(datasets=ds, mtf_bundle=mtf, ledger_bundle=led_b, r1_candidates=None)
    r2_reps = replay_r2_same_opportunity_paired_counterfactuals(datasets=ds, r1_candidates={"arm_candidates": {}}, r2_candidates=r2_cands)
    ff = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=r2_reps["r2_replays"])

    # Direct upstream rerun MUST now have 0 zero-fee display bugs!
    fu = rerun_r2_full_universe_by_cost_scenario(datasets=ds, mtf_bundle=mtf, ledger_bundle=led_b, arms=("B2_H1", "NC_NULL_TRIGGER"))
    c4_rec = reconcile_c4_full_universe_cost_fees(fu, ff)
    assert c4_rec["status"] == "PASS"
    assert c4_rec["upstream_zero_fee_display_hits"] == 0

    # Streaming KH-03 6-scenario fee ledger + independent validation
    out_gz = tmp_path / "04_fee_per_episode_ledger.jsonl.gz"
    meta, rec_doc = build_kh03_six_scenario_fee_ledger_and_reconciliation(
        datasets=ds,
        mtf_bundle=mtf,
        ledger_bundle=led_b,
        fixed_fill_doc=ff,
        out_episode_ledger_gz_path=out_gz,
        arms=("B2_H1", "NC_NULL_TRIGGER"),
    )
    assert meta["total_episode_rows"] > 0
    assert rec_doc["status"] == "PASS"
    assert rec_doc["C4_UPSTREAM_FEE_DISPLAY_ROOT_CAUSE_FIXED"] is True
    assert rec_doc["zero_fee_display_bug_hits_after_fix"] == 0
    assert rec_doc["ACCOUNT_EFFECTIVE_FEE_VERDICT"] == "UNKNOWN_NOT_LOCALLY_VERIFIED"

    val = validate_kh03_fee_ledger_independently(out_gz, rec_doc, expected_arms=("B2_H1", "NC_NULL_TRIGGER"))
    assert val["valid"] is True and val["status"] == "PASS"

    # Negative: tamper 05 total_fees_usd to 0.0 on a closed cell -> independent validator MUST reject!
    tampered = copy.deepcopy(rec_doc)
    tampered_any = False
    for sc_d in tampered["scenarios"].values():
        for a_d in sc_d["arms"].values():
            if int(a_d["closed"]) > 0:
                a_d["total_fees_usd"] = 0.0
                tampered_any = True
                break
        if tampered_any:
            break
    assert tampered_any is True
    assert validate_kh03_fee_ledger_independently(out_gz, tampered, expected_arms=("B2_H1", "NC_NULL_TRIGGER"))["valid"] is False
