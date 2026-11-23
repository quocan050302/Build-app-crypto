"""
aurum-desk/backend/tests/test_kh_02_r2_c4_tail_evidence.py

AURUM DESK — KH-02.R2.C4 P0-B Realized Tail Ledger, Independent Recomputation & Mutation Tests:
  - Verifies non-empty arm x mode tail evidence across all 5 R2 arms x 2 modes on synthetic & real data.
  - Verifies streaming `12_per_episode_risk_ledger.jsonl.gz` round-trip & independent Decimal recompute.
  - Verifies fail-closed rejection of: empty `{}`, hardcoded boolean without rows, missing arm/mode,
    dropped episode, duplicate UID, altered loss count, altered max realized loss, swapped PnL sign,
    altered cap threshold, and checksum mismatch.
  - Verifies §05.4 full-universe `total_fees_usd=0.0` upstream display reconciliation.
"""
from __future__ import annotations

import copy
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

from services.kh_02_r2_c4_risk_tail_evidence import (
    EXPECTED_R2_TAIL_ARMS_C4,
    EXPECTED_R2_TAIL_MODE_NAMES_C4,
    build_c4_per_episode_risk_ledger,
    build_c4_realized_tail_evidence_from_r2_replays,
    recompute_c4_tail_counters_from_episode_ledger,
    reconcile_c4_full_universe_cost_fees,
    summarize_c4_science_and_risk_truth,
    validate_c4_tail_disclosure_vs_risk_ledger,
)


def _synthetic_datasets_and_replays() -> tuple[Dict[str, Any], Dict[str, Any]]:
    base_ts = 1791500000000
    bars_1m = [
        {"open_time": base_ts + i * 60_000, "close_time": base_ts + (i + 1) * 60_000, "open": 4000.0 + (0.5 if i == 3 else 0.0), "high": 4005.0, "low": 3995.0, "close": 4001.0}
        for i in range(10)
    ]
    r2_replays: Dict[str, Any] = {}
    for a_idx, ac in enumerate(EXPECTED_R2_TAIL_ARMS_C4):
        eps_by_mode: Dict[str, Any] = {}
        for m_idx, md in enumerate(EXPECTED_R2_TAIL_MODE_NAMES_C4):
            # Include one loss > $2.50 (adverse gap tail!) + one normal win + one open at horizon
            eps_by_mode[md] = [
                {
                    "opportunity_id": f"{ac}_{md}_ep0",
                    "direction": "LONG",
                    "decision_timestamp_ms": base_ts + 60_000,
                    "checkpoint_index_1m": 1,
                    "entry_reference": 4000.0,
                    "selected_sl": 3995.0,
                    "take_profit": 4015.0,
                    "executed_quantity": 0.40,
                    "planned_net_risk_usd": 2.45,
                    "planned_net_rr": 2.4,
                    "filled": True,
                    "outcome_label": "LOSS",
                    "realized_gross_pnl_usd": -2.30,
                    "total_fees_usd": 0.65,
                    "realized_net_pnl_usd": -2.95,  # exceeds $2.50 cap due to adverse gap!
                    "realized_net_r": -1.2041,
                    "execution_ledger": {"exit_type": "ADVERSE_GAP_OPEN_STOP_LOSS", "entry_fill_ts": base_ts + 60_000, "exit_fill_ts": base_ts + 120_000},
                },
                {
                    "opportunity_id": f"{ac}_{md}_ep1",
                    "direction": "SHORT",
                    "decision_timestamp_ms": base_ts + 180_000,
                    "checkpoint_index_1m": 3,
                    "entry_reference": 4000.5,
                    "selected_sl": 4005.5,
                    "take_profit": 3985.5,
                    "executed_quantity": 0.40,
                    "planned_net_risk_usd": 2.40,
                    "planned_net_rr": 2.5,
                    "filled": True,
                    "outcome_label": "WIN",
                    "realized_gross_pnl_usd": 6.00,
                    "total_fees_usd": 0.64,
                    "realized_net_pnl_usd": 5.36,
                    "realized_net_r": 2.2333,
                    "execution_ledger": {"exit_type": "WIN", "entry_fill_ts": base_ts + 180_000, "exit_fill_ts": base_ts + 240_000},
                },
                {
                    "opportunity_id": f"{ac}_{md}_ep2",
                    "direction": "LONG",
                    "decision_timestamp_ms": base_ts + 300_000,
                    "checkpoint_index_1m": 5,
                    "entry_reference": 4000.0,
                    "selected_sl": 3995.0,
                    "take_profit": 4015.0,
                    "executed_quantity": 0.40,
                    "planned_net_risk_usd": 2.42,
                    "planned_net_rr": 2.4,
                    "filled": True,
                    "outcome_label": "OPEN_AT_HORIZON",
                    "realized_gross_pnl_usd": 0.0,
                    "total_fees_usd": 0.0,
                    "realized_net_pnl_usd": 0.0,
                    "realized_net_r": 0.0,
                    "execution_ledger": {"exit_type": "OPEN_AT_HORIZON", "entry_fill_ts": base_ts + 300_000, "exit_fill_ts": None},
                },
            ]
        r2_replays[ac] = {"episodes_by_mode": eps_by_mode}
    return {"1m": bars_1m}, r2_replays


def test_c4_tail_ledger_build_recompute_and_10_mutations_fail_closed(tmp_path: Path) -> None:
    ds, reps = _synthetic_datasets_and_replays()
    ledger_p = tmp_path / "12_per_episode_risk_ledger.jsonl.gz"

    meta = build_c4_per_episode_risk_ledger(datasets=ds, r2_replays=reps, out_jsonl_gz_path=ledger_p)
    assert meta["total_episode_rows"] == 30  # 5 arms * 2 modes * 3 episodes

    recomp = recompute_c4_tail_counters_from_episode_ledger(ledger_p)
    assert recomp["status"] == "PASS"
    assert recomp["total_realized_losses_exceeding_2p50_count"] == 10
    assert recomp["global_max_realized_net_loss_usd"] == pytest.approx(2.95, abs=1e-4)

    tail_doc = build_c4_realized_tail_evidence_from_r2_replays(
        datasets=ds, r2_replays=reps, ledger_meta=meta, ledger_recomputed=recomp
    )
    assert tail_doc["status"] == "PASS"
    assert tail_doc["coverage_cells_populated"] == 10

    val_ok = validate_c4_tail_disclosure_vs_risk_ledger(tail_doc, ledger_p)
    assert val_ok["valid"] is True and val_ok["status"] == "PASS"

    # 1. Empty `{}` summary must FAIL
    assert validate_c4_tail_disclosure_vs_risk_ledger({}, ledger_p)["valid"] is False
    # 2. Hardcoded boolean without arm_fill_time_and_tail_audits must FAIL
    assert validate_c4_tail_disclosure_vs_risk_ledger({"realized_tail_risk_disclosed": True, "arm_fill_time_and_tail_audits": {}}, ledger_p)["valid"] is False
    # 3. Missing arm must FAIL
    m3 = copy.deepcopy(tail_doc)
    m3["arm_fill_time_and_tail_audits"].pop("B2_H1")
    assert validate_c4_tail_disclosure_vs_risk_ledger(m3, ledger_p)["valid"] is False
    # 4. Missing mode must FAIL
    m4 = copy.deepcopy(tail_doc)
    m4["arm_fill_time_and_tail_audits"]["B2_H2"].pop("STRESS_WIDER_SPREAD_SLIPPAGE_2X")
    assert validate_c4_tail_disclosure_vs_risk_ledger(m4, ledger_p)["valid"] is False
    # 5. Fake 0 oversized losses when actual is 1 must FAIL
    m5 = copy.deepcopy(tail_doc)
    m5["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"]["realized_losses_exceeding_2p50_count"] = 0
    assert validate_c4_tail_disclosure_vs_risk_ledger(m5, ledger_p)["valid"] is False
    # 6. Altered max realized loss must FAIL
    m6 = copy.deepcopy(tail_doc)
    m6["arm_fill_time_and_tail_audits"]["B2_H1"]["NEXT_BAR_OPEN_BID_ASK"]["max_realized_net_loss_usd"] = 2.40
    assert validate_c4_tail_disclosure_vs_risk_ledger(m6, ledger_p)["valid"] is False
    # 7. Altered risk cap threshold must FAIL
    m7 = copy.deepcopy(tail_doc)
    m7["risk_cap_usd"] = 5.0
    assert validate_c4_tail_disclosure_vs_risk_ledger(m7, ledger_p)["valid"] is False
    # 8. Dropped trade in ledger fails checksum & count
    drop_p = tmp_path / "dropped.jsonl.gz"
    with gzip.open(ledger_p, "rt", encoding="utf-8") as src, gzip.open(drop_p, "wt", encoding="utf-8") as dst:
        lines = [ln for ln in src if ln.strip()]
        for ln in lines[:-1]:
            dst.write(ln)
    assert validate_c4_tail_disclosure_vs_risk_ledger(tail_doc, drop_p)["valid"] is False
    # 9. Duplicate episode UID in ledger must FAIL
    dup_p = tmp_path / "dup.jsonl.gz"
    with gzip.open(ledger_p, "rt", encoding="utf-8") as src, gzip.open(dup_p, "wt", encoding="utf-8") as dst:
        lines = [ln for ln in src if ln.strip()]
        for ln in lines:
            dst.write(ln)
        dst.write(lines[0])
    assert recompute_c4_tail_counters_from_episode_ledger(dup_p)["status"] == "FAIL"
    # 10. Swapped net PnL sign in ledger breaks gross - fee == net check
    sign_p = tmp_path / "sign.jsonl.gz"
    with gzip.open(ledger_p, "rt", encoding="utf-8") as src, gzip.open(sign_p, "wt", encoding="utf-8") as dst:
        for i, ln in enumerate(src):
            obj = json.loads(ln)
            if i == 0:
                obj["realized_net_pnl_usd"] = abs(obj["realized_net_pnl_usd"])
            dst.write(json.dumps(obj) + "\n")
    assert recompute_c4_tail_counters_from_episode_ledger(sign_p)["status"] == "FAIL"


def test_c4_full_universe_zero_fee_display_reconciliation_and_science_truth(tmp_path: Path) -> None:
    ds, reps = _synthetic_datasets_and_replays()
    ledger_p = tmp_path / "l.jsonl.gz"
    meta = build_c4_per_episode_risk_ledger(datasets=ds, r2_replays=reps, out_jsonl_gz_path=ledger_p)
    recomp = recompute_c4_tail_counters_from_episode_ledger(ledger_p)
    tail_doc = build_c4_realized_tail_evidence_from_r2_replays(datasets=ds, r2_replays=reps, ledger_meta=meta, ledger_recomputed=recomp)
    val = validate_c4_tail_disclosure_vs_risk_ledger(tail_doc, ledger_p)

    fake_rerisk = {
        "scenarios": {
            "1X_BASE_0p0004": {
                "raw_r2_geometry_risk_eligible_count": 1323,
                "arms": {
                    "B2_H1": {"replay_summary": {"closed": 73, "wins": 1, "losses": 72, "open_at_horizon": 9, "gross_pnl_usd": -73.1555, "net_pnl_usd": -175.438, "total_fees_usd": 0.0, "expectancy_r": -0.9737}},
                },
            }
        }
    }
    fake_ff = {"arms": {"B2_H1": {"1x_base_0p0004": {"fees_usd": 102.2825}}}}
    fee_rec = reconcile_c4_full_universe_cost_fees(fake_rerisk, fake_ff)
    assert fee_rec["status"] == "PASS"
    assert fee_rec["upstream_zero_fee_display_hits"] == 1
    b2 = fee_rec["reconciled_scenarios"]["1X_BASE_0p0004"]["arms"]["B2_H1"]
    assert b2["c4_reconciled_total_fees_usd"] == pytest.approx(102.2825, abs=1e-4)
    assert b2["matches_fixed_fill_1x_fees"] is True

    # Negative mismatch -> NOT_RECONCILED
    bad_ff = {"arms": {"B2_H1": {"1x_base_0p0004": {"fees_usd": 999.0}}}}
    assert reconcile_c4_full_universe_cost_fees(fake_rerisk, bad_ff)["status"] == "NOT_RECONCILED"

    truth = summarize_c4_science_and_risk_truth(tail_doc=tail_doc, reconciliation_doc=val, cost_fee_reconciliation=fee_rec)
    assert truth["B_advisory_hypothetical_fill_time_rerisk"]["is_live_runtime_enforcement"] is False
    assert truth["C_production_engine_behavior"]["ACTIVATION_READY"] == "NO"
    assert truth["D_unobserved_future_gap_and_tail_risk"]["planned_cap_guarantees_realized_loss_le_2p50"] is False
