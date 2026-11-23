"""
aurum-desk/backend/tests/test_kh03_r4_economic_accounting.py

Unit & integration tests for KH-03.R4 exact Decimal accounting bridge, open vs closed fee
separation, non-executable zero-fee descriptive counterfactual vs full dynamic 6-cost replay,
conservative break-even bounds, and strict non-finite / malformed numeric parser fuzzing.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import math
from pathlib import Path
import sys
from typing import Any, Dict, List

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_research_integrity_service import CANONICAL_DATASET_SHA256_KH02_R2
from services.kh_03_r4_economic_diagnostic_service import (
    SEALED_KH03_R4_1X_ANCHORS,
    _to_dec,
    build_r4_counterfactual_cost_only_diagnostics,
    derive_r4_breakeven_feasibility_bounds,
    recompute_r4_decimal_economic_bridge,
)


def _make_synthetic_anchor_forensic_rows() -> List[Dict[str, Any]]:
    """
    Constructs a 233-row deterministic synthetic forensic ledger matching exact 1x NULL anchor totals:
      7 wins, 196 losses, 28 open_at_horizon, 2 data_gap_unresolved (233 filled, 0 unfilled),
      gross=-164.9541, closed_fees=276.6577, net=-441.6118, open28_fee=14.7151, gap2_fee=1.6080,
      expectancy_r=-0.8806 (avg over 203 closed), 139 losses > 2.50 USD, 86 losses MFE>=1.0x SL.
    """
    rows: List[Dict[str, Any]] = []
    t0 = 1791500000000
    # 7 wins: gross=+40.0000 each -> +280.0000 total; fee=1.4000 each -> 9.8000; net=+38.6000 each -> +270.2000
    # Wait: let's hit exact totals:
    # Closed totals: gross=-164.9541, fees=276.6577, net=-441.6118
    # Expectancy R sum over 203 trades: 203 * -0.8806 = -178.7618
    # Split across 7 wins + 196 losses:
    # Wins (7): gross = +11.4286 * 6 + 11.4285 = +80.0001; fees = 1.4000 * 7 = 9.8000; net = +70.2001; r = +2.8000 * 7 = +19.6000
    # Wait: losses need gross = -164.9541 - 80.0001 = -244.9542; fees = 276.6577 - 9.8000 = 266.8577; net = -511.8119
    # Wait: check 196 losses avg loss = 511.8119 / 196 = 2.6113? Let's make 139 losses = -2.5172 and 57 losses = -2.4000 -> total net losses = -349.8908 - 136.8000 = -486.6908!
    # Then wins net = -441.6118 - (-486.6908) = +45.0790!
    # Check wins gross: wins_fees = 7 * 1.4000 = 9.8000 -> wins_gross = 45.0790 + 9.8000 = 54.8790!
    # Losses fees = 276.6577 - 9.8000 = 266.8577 -> losses_gross = -486.6908 + 266.8577 = -219.8331!
    # Check sum gross = 54.8790 - 219.8331 = -164.9541! EXACT!
    # Check sum fees = 9.8000 + 266.8577 = 276.6577! EXACT!
    # Check sum net = 45.0790 - 486.6908 = -441.6118! EXACT!
    # Check R: 196 losses at -1.0070R = -197.3720R; 7 wins total R = -178.7618 - (-197.3720) = +18.6102R -> 6 wins at +2.6586R, 1 win at +2.6586R!
    win_gross_each = [Decimal("7.8399")] * 6 + [Decimal("7.8396")]
    win_r_each = [Decimal("2.6586")] * 7
    for i in range(7):
        g = win_gross_each[i]
        ef = Decimal("0.7000")
        xf = Decimal("0.7000")
        tf = ef + xf
        ne = g - tf
        rows.append({
            "episode_uid": f"SYN|W|{i}",
            "opportunity_id": f"OPP_W_{i:03d}",
            "setup_episode_id": f"EP_{i % 17:02d}",
            "decision_timestamp_ms": t0 + i * 200 * 60_000,
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "ny_date": f"2026-10-{10 + (i % 17):02d}",
            "stop_dist_usd": 2.38,
            "tp_dist_usd": 24.05,
            "gross_rr": 10.105,
            "adverse_open_gap_usd": 0.0,
            "planned_net_risk_usd": 2.48,
            "filled": True,
            "outcome_label": "WIN",
            "exit_type": "WIN",
            "hold_minutes": 15,
            "post_hoc_mfe_20bar_in_sl_units": 10.105,
            "realized_gross_pnl_usd": float(g),
            "entry_fee_usd": float(ef),
            "exit_fee_usd": float(xf),
            "total_fees_usd": float(tf),
            "accrued_open_entry_fee_usd": 0.0,
            "fee_share_of_planned_risk": 0.5645,
            "realized_net_pnl_usd": float(ne),
            "gross_fee_net_residual_usd": 0.0,
            "realized_net_r": float(win_r_each[i]),
        })

    # 196 losses: 139 with abs(net) > 2.50 (138 at -2.5172, 1 at -2.5574), 57 at -2.4000, adjust last one slightly to match exact sum -486.6908
    loss_nets = [Decimal("-2.5172")] * 138 + [Decimal("-2.5574")] + [Decimal("-2.4000")] * 56
    last_net = Decimal("-486.6908") - sum(loss_nets, Decimal("0"))
    loss_nets.append(last_net)
    assert len(loss_nets) == 196 and abs(last_net) <= Decimal("2.50")

    # Distribute 266.8577 closed fees across 196 losses
    base_ef = Decimal("0.6808")
    base_xf = Decimal("0.6807")
    loss_fees = [base_ef + base_xf] * 195
    last_tf = Decimal("266.8577") - sum(loss_fees, Decimal("0"))
    loss_fees.append(last_tf)

    for i in range(196):
        ne = loss_nets[i]
        tf = loss_fees[i]
        ef = (tf / Decimal("2")).quantize(Decimal("0.0001"))
        xf = tf - ef
        g = ne + tf
        idx = 7 + i
        rows.append({
            "episode_uid": f"SYN|L|{i}",
            "opportunity_id": f"OPP_L_{i:03d}",
            "setup_episode_id": f"EP_{idx % 40:02d}",
            "decision_timestamp_ms": t0 + idx * 200 * 60_000,
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "ny_date": f"2026-10-{10 + (i % 17):02d}",
            "stop_dist_usd": 2.38,
            "tp_dist_usd": 24.05,
            "gross_rr": 8.9172,
            "adverse_open_gap_usd": 0.0,
            "planned_net_risk_usd": 2.48,
            "filled": True,
            "outcome_label": "LOSS",
            "exit_type": "LOSS",
            "hold_minutes": 15,
            "post_hoc_mfe_20bar_in_sl_units": 1.25 if i < 86 else 0.40,
            "realized_gross_pnl_usd": float(g),
            "entry_fee_usd": float(ef),
            "exit_fee_usd": float(xf),
            "total_fees_usd": float(tf),
            "accrued_open_entry_fee_usd": 0.0,
            "fee_share_of_planned_risk": 0.5611 if i < 127 else 0.35,
            "realized_net_pnl_usd": float(ne),
            "gross_fee_net_residual_usd": 0.0,
            "realized_net_r": -1.0070,
        })

    # 28 OPEN_AT_HORIZON: total open accrued fee = 14.7151
    open_fees = [Decimal("0.5255")] * 27 + [Decimal("14.7151") - Decimal("0.5255") * 27]
    for i in range(28):
        idx = 203 + i
        rows.append({
            "episode_uid": f"SYN|O|{i}",
            "opportunity_id": f"OPP_O_{i:03d}",
            "setup_episode_id": f"EP_O_{i:02d}",
            "decision_timestamp_ms": t0 + idx * 200 * 60_000,
            "direction": "LONG",
            "ny_date": f"2026-10-{10 + (i % 17):02d}",
            "stop_dist_usd": 2.38,
            "tp_dist_usd": 24.05,
            "gross_rr": 8.9172,
            "adverse_open_gap_usd": 0.0,
            "planned_net_risk_usd": 2.48,
            "filled": True,
            "outcome_label": "OPEN_AT_HORIZON",
            "exit_type": "OPEN_AT_HORIZON",
            "hold_minutes": 180,
            "post_hoc_mfe_20bar_in_sl_units": 0.50,
            "realized_gross_pnl_usd": 0.0,
            "entry_fee_usd": 0.0,
            "exit_fee_usd": 0.0,
            "total_fees_usd": 0.0,
            "accrued_open_entry_fee_usd": float(open_fees[i]),
            "fee_share_of_planned_risk": None,
            "realized_net_pnl_usd": 0.0,
            "gross_fee_net_residual_usd": 0.0,
            "realized_net_r": 0.0,
        })

    # 2 DATA_GAP_UNRESOLVED: total gap accrued fee = 1.6080
    for i in range(2):
        idx = 231 + i
        rows.append({
            "episode_uid": f"SYN|G|{i}",
            "opportunity_id": f"OPP_G_{i:03d}",
            "setup_episode_id": f"EP_G_{i:02d}",
            "decision_timestamp_ms": t0 + idx * 200 * 60_000,
            "direction": "SHORT",
            "ny_date": "2026-10-20",
            "stop_dist_usd": 2.38,
            "tp_dist_usd": 24.05,
            "gross_rr": 8.9172,
            "adverse_open_gap_usd": 0.0,
            "planned_net_risk_usd": 2.48,
            "filled": True,
            "outcome_label": "DATA_GAP_UNRESOLVED",
            "exit_type": "DATA_GAP_UNRESOLVED",
            "hold_minutes": None,
            "post_hoc_mfe_20bar_in_sl_units": 0.0,
            "realized_gross_pnl_usd": 0.0,
            "entry_fee_usd": 0.0,
            "exit_fee_usd": 0.0,
            "total_fees_usd": 0.0,
            "accrued_open_entry_fee_usd": 0.8040,
            "fee_share_of_planned_risk": None,
            "realized_net_pnl_usd": 0.0,
            "gross_fee_net_residual_usd": 0.0,
            "realized_net_r": 0.0,
        })

    return rows


def test_r4_decimal_bridge_reconciles_exact_and_separates_open_fees() -> None:
    rows = _make_synthetic_anchor_forensic_rows()
    out = recompute_r4_decimal_economic_bridge(
        rows, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2, ledger_meta={"ledger_rel_path": "logs/forensic_closed_open_gap_ledger_1x.jsonl.gz"}
    )
    assert out["status"] == "PASS", out["violations"]
    b = out["closed_trades_decimal_bridge_usd"]
    o = out["open_and_gap_fee_separation_usd"]
    assert b["gross_pnl_closed_usd"] == pytest.approx(-164.9541, abs=1e-4)
    assert b["total_closed_fees_usd"] == pytest.approx(276.6577, abs=1e-4)
    assert b["net_pnl_closed_usd"] == pytest.approx(-441.6118, abs=1e-4)
    assert b["aggregate_gross_minus_fees_minus_net_residual_usd"] <= 0.001
    assert o["open_at_horizon_28_accrued_entry_fees_usd"] == pytest.approx(14.7151, abs=1e-4)
    assert o["data_gap_unresolved_2_accrued_entry_fees_usd"] == pytest.approx(1.6080, abs=1e-4)
    assert o["all_nonclosed_filled_30_accrued_entry_fees_usd"] == pytest.approx(16.3231, abs=1e-4)
    assert o["open_fees_excluded_from_closed_net_pnl"] is True
    assert o["entry_fee_double_counted"] is False
    assert out["gross_is_strictly_negative_before_fees"] is True
    assert out["planned_risk_cap_tail_check"]["planned_cap_is_physical_guarantee"] is False
    assert out["planned_risk_cap_tail_check"]["realized_losses_exceeding_2p50_count"] == 139


def test_r4_decimal_bridge_detects_residual_double_count_and_open_leakage() -> None:
    rows = _make_synthetic_anchor_forensic_rows()

    # RED 1: Residual breach (>0.001)
    r_res = copy.deepcopy(rows)
    r_res[0]["realized_net_pnl_usd"] = round(float(r_res[0]["realized_net_pnl_usd"]) + 0.05, 4)
    out1 = recompute_r4_decimal_economic_bridge(r_res, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2, ledger_meta={})
    assert out1["status"] == "FAIL"
    assert any("GROSS_FEE_NET_RESIDUAL_BREACH" in v for v in out1["violations"])

    # RED 2: Entry fee double-counted (entry_fee + exit_fee != total_fees)
    r_dup_fee = copy.deepcopy(rows)
    r_dup_fee[0]["entry_fee_usd"] = round(float(r_dup_fee[0]["entry_fee_usd"]) * 2.0, 4)
    out2 = recompute_r4_decimal_economic_bridge(r_dup_fee, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2, ledger_meta={})
    assert out2["status"] == "FAIL"
    assert any("FEE_SPLIT_RESIDUAL_BREACH" in v for v in out2["violations"])

    # RED 3: Open trade fee leaked into closed total_fees_usd
    r_open_leak = copy.deepcopy(rows)
    r_open_leak[205]["total_fees_usd"] = 0.5255
    out3 = recompute_r4_decimal_economic_bridge(r_open_leak, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2, ledger_meta={})
    assert out3["status"] == "FAIL"
    assert any("OPEN_TRADE_LEAKED_INTO_CLOSED_FEES" in v for v in out3["violations"])


@pytest.mark.parametrize("bad_val", [None, True, False, float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "abc", ""])
def test_r4_decimal_parser_fuzz_rejects_nonfinite_and_invalid_types(bad_val: Any) -> None:
    with pytest.raises(ValueError):
        _to_dec(bad_val, field_name="fuzz_field")


def test_r4_counterfactual_zero_fee_is_negative_and_non_executable() -> None:
    rows = _make_synthetic_anchor_forensic_rows()
    dyn6 = {
        "1X_BASE_0p0004": {"arms": {"NC_NULL_TRIGGER": {"closed": 203, "gross_pnl_usd": -164.9541, "total_fees_usd": 276.6577, "net_pnl_usd": -441.6118, "overlap_violations": 0}}},
        "2X_ALL_COST_0p0004": {"arms": {"NC_NULL_TRIGGER": {"closed": 111, "gross_pnl_usd": -64.1289, "total_fees_usd": 189.6224, "net_pnl_usd": -253.7513, "overlap_violations": 0}}},
        "2X_ALL_COST_0p0006": {"arms": {"NC_NULL_TRIGGER": {"closed": 46, "gross_pnl_usd": -26.1278, "total_fees_usd": 87.738, "net_pnl_usd": -113.8658, "overlap_violations": 0}}},
        "3X_ALL_COST_0p0004": {"arms": {"NC_NULL_TRIGGER": {"closed": 45, "gross_pnl_usd": -27.3175, "total_fees_usd": 84.9594, "net_pnl_usd": -112.2769, "overlap_violations": 0}}},
        "3X_ALL_COST_0p0006": {"arms": {"NC_NULL_TRIGGER": {"closed": 14, "gross_pnl_usd": -7.2788, "total_fees_usd": 26.7959, "net_pnl_usd": -34.0747, "overlap_violations": 0}}},
        "FEE_ONLY_0p0006": {"arms": {"NC_NULL_TRIGGER": {"closed": 138, "gross_pnl_usd": -82.0567, "total_fees_usd": 217.547, "net_pnl_usd": -299.6037, "overlap_violations": 0}}},
    }
    cf = build_r4_counterfactual_cost_only_diagnostics(
        rows, fixed_fills_from_r3={"status": "PASS"}, dyn6_scenarios=dyn6, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2
    )
    assert cf["status"] == "PASS"
    assert cf["is_executable_strategy"] is False
    assert cf["is_proven_edge"] is False
    assert cf["population_mode"] == "FIXED_FILLS_DESCRIPTIVE_REPRICING"
    assert cf["counterfactual_zero_fee_label"] == "COUNTERFACTUAL_ZERO_FEE_DESCRIPTIVE"
    assert cf["zero_fee_counterfactual_net_pnl_usd"] == pytest.approx(-164.9541, abs=1e-4)
    assert cf["fee_reduction_alone_cannot_cure_baseline_losses"] is True
    assert cf["dynamic_vs_fixed_fill_populations_separated"] is True
    assert cf["dynamic_populations_differ_across_cost_regimes"] is True

    # Also test breakeven feasibility bounds on 6 dynamic scenarios
    f08 = derive_r4_breakeven_feasibility_bounds(
        rows, f09_cost={"scenarios": {}}, dyn6_scenarios=dyn6, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2
    )
    assert f08["status"] == "PASS"
    assert f08["population_mode"] == "FULL_DYNAMIC_RERISK"
    assert f08["zero_fee_display_bugs_count"] == 0
    be = f08["observed_1x_breakeven_bounds_conservative"]
    assert be["observed_closed_win_rate"] < be["empirical_breakeven_win_rate_at_1x_modeled"]
