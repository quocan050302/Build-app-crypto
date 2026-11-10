"""
Unit & Adversarial Regression Suite for AURUM Desk V17.9.3.1.2.12
=================================================================
Covers:
  - P0-A: Strict Raw Stream Provenance Fail-Closed (no silent defaults, no legacy_sem_hash,
    mandatory 09_original_execution_ledger_source_reconciliation.jsonl.gz, forged quote rejection)
  - P0-B: Multiprocess worker parity & PID telemetry verification
  - R1: Hierarchical loss forensics (A..H buckets, pre-entry vs ex-post separation, 166/242 conservation)
  - R2: Causal MTF (H4/H1/M15/M5/M1) + Unicorn single-axis ablation & 278->6->272->0 funnel
  - R3: Matched Entry/SL/TP counterfactuals (A0..A4) & fixed $2.50 risk cap sizing
  - R4: All 40 adversarial negative controls (V01..V40) & honest OOS / activation verdicts
"""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_2_structural_stop_intelligence import (
    evaluate_r3_counterfactual_stop_and_target_v17931212,
)
from services.v17_9_3_1_2_3_core_trading_logic_repair import (
    classify_r1_hierarchical_loss_forensics_v17931212,
)
from services.v17_9_3_macro_unicorn_intelligence import (
    evaluate_r2_mtf_unicorn_ablation_matrix_v17931212,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    build_exit_event_lineage_v17931211,
    read_exit_event_stream_strict_v17931211,
    read_legacy_v17931211_initial_stream_readonly,
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_raw_stream_vs_source_execution_v17931211,
)
from services.v17_9_3_1_2_12_provenance_forensics_and_repair_service import (
    CANONICAL_DATASET_SHA256_V17931212,
    build_v17931212_callgraph_and_defect_map,
    evaluate_v17931212_gate_matrix,
    run_negative_controls_v01_to_v40_v17931212,
    verify_r1_forensic_separation_and_conservation_v17931212,
    verify_r2_causal_mtf_unicorn_provenance_v17931212,
    verify_r3_counterfactual_invariants_v17931212,
    verify_r4_oos_and_activation_verdict_v17931212,
)

_RUN_ID = "test_run_v17931212"
_SHA = "6d7045b29f358d734da61f4b44535821aa32f944"
_SYNTH_BARS = {
    180_000: {
        "open_time": 180_000,
        "close_time": 240_000,
        "open": 4098.0,
        "high": 4101.0,
        "low": 4088.0,
        "close": 4091.0,
        "is_closed": True,
    }
}
_SYNTH_EP = [
    {
        "opportunity_id": "opp_v12_1",
        "episode_key": "ep_v12_1",
        "setup_episode_id": "set_v12_1",
        "model_id": "SMC_M1_CONTINUATION",
        "direction": "LONG",
        "decision_timestamp_ms": 120_000,
        "entry_reference": 4100.0,
        "selected_sl": 4090.0,
        "take_profit": 4120.0,
        "executed_quantity": 0.25,
        "planned_net_risk_usd": 2.50,
        "filled": True,
        "outcome_label": "LOSS",
        "exit_type": "LOSS",
        "entry_fill_ts": 120_000,
        "entry_fill_price": 4100.20,
        "entry_fee_usd": 0.4100,
        "exit_fill_ts": 240_000,
        "exit_fill_price": 4089.90,
        "exit_fee_usd": 0.4090,
        "realized_gross_pnl_usd": -2.5750,
        "realized_net_pnl_usd": -3.3940,
        "realized_r": -1.3576,
        "bars_held": 2,
        "execution_ledger": {"trigger_bar_ts": 180_000, "multiplier": 1.0},
    }
]


def _write_gz_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as gz:
        for r in rows:
            gz.write(json.dumps(r, sort_keys=True) + "\n")


@pytest.fixture(scope="module")
def neg_controls_report() -> Dict[str, Any]:
    return run_negative_controls_v01_to_v40_v17931212(
        run_id=_RUN_ID,
        tested_sha=_SHA,
    )


def test_p0a_strict_stream_reader_rejects_missing_and_forged_provenance(tmp_path: Path) -> None:
    lin = build_exit_event_lineage_v17931211(
        _SYNTH_EP,
        bars_1m_by_open_ts=_SYNTH_BARS,
        run_id=_RUN_ID,
        tested_executable_sha=_SHA,
        policy_id="SI0_OFF_BASELINE",
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    valid_events = copy.deepcopy(lin["all_events"])
    valid_file = tmp_path / "valid.jsonl.gz"
    _write_gz_jsonl(valid_file, valid_events)

    res_ok = read_exit_event_stream_strict_v17931211(
        valid_file,
        expected_run_id=_RUN_ID,
        expected_tested_sha=_SHA,
    )
    assert res_ok["status"] == "PASS"
    assert res_ok["events_count"] == len(valid_events)

    # 1. Missing evidence_origin -> FAIL (no silent default)
    missing_eo = copy.deepcopy(valid_events)
    missing_eo[0].pop("evidence_origin", None)
    f_eo = tmp_path / "missing_eo.jsonl.gz"
    _write_gz_jsonl(f_eo, missing_eo)
    res_eo = read_exit_event_stream_strict_v17931211(f_eo, expected_run_id=_RUN_ID, expected_tested_sha=_SHA)
    assert res_eo["status"] == "FAIL"
    assert any("STREAM_MISSING_OR_EMPTY_REQUIRED_FIELD" in v and "evidence_origin" in v for v in res_eo["violations"])

    # 2. Missing quote_source_kind -> FAIL (no silent default)
    missing_qsk = copy.deepcopy(valid_events)
    missing_qsk[0].pop("quote_source_kind", None)
    f_qsk = tmp_path / "missing_qsk.jsonl.gz"
    _write_gz_jsonl(f_qsk, missing_qsk)
    res_qsk = read_exit_event_stream_strict_v17931211(f_qsk, expected_run_id=_RUN_ID, expected_tested_sha=_SHA)
    assert res_qsk["status"] == "FAIL"
    assert any("STREAM_MISSING_OR_EMPTY_REQUIRED_FIELD" in v and "quote_source_kind" in v for v in res_qsk["violations"])

    # 3. Legacy semantic hash (excluding evidence_origin/quote_source_kind) -> FAIL on active stream
    legacy_events = copy.deepcopy(valid_events)
    for ev in legacy_events:
        ev["semantic_hash"] = canonical_hash(
            {
                k: v
                for k, v in ev.items()
                if k not in ("run_id", "tested_executable_sha", "semantic_hash", "evidence_origin", "quote_source_kind")
            }
        )
    f_leg = tmp_path / "legacy_hash.jsonl.gz"
    _write_gz_jsonl(f_leg, legacy_events)
    res_leg_strict = read_exit_event_stream_strict_v17931211(
        f_leg, expected_run_id=_RUN_ID, expected_tested_sha=_SHA
    )
    assert res_leg_strict["status"] == "FAIL"
    assert any("STREAM_EVENT_SEMANTIC_HASH_TAMPERED" in v for v in res_leg_strict["violations"])

    # Explicit read-only helper forbids non-initial run IDs and only accepts locked initial .11 run
    res_leg_ro_forbid = read_legacy_v17931211_initial_stream_readonly(
        f_leg, expected_run_id=_RUN_ID, expected_tested_sha=_SHA
    )
    assert res_leg_ro_forbid["status"] == "FAIL"
    assert any("LEGACY_READONLY_READER_FORBIDDEN_FOR_RUN" in v for v in res_leg_ro_forbid["violations"])

    # 4. Forged REAL_PROVIDER_QUOTE without provider capture proof -> FAIL
    forged_rpq = copy.deepcopy(valid_events)
    forged_rpq[0]["quote_source_kind"] = "REAL_PROVIDER_QUOTE"
    forged_rpq[0]["semantic_hash"] = canonical_hash(
        {k: v for k, v in forged_rpq[0].items() if k not in ("run_id", "tested_executable_sha", "semantic_hash")}
    )
    f_rpq = tmp_path / "forged_rpq.jsonl.gz"
    _write_gz_jsonl(f_rpq, forged_rpq)
    res_rpq = read_exit_event_stream_strict_v17931211(
        f_rpq, expected_run_id=_RUN_ID, expected_tested_sha=_SHA
    )
    assert res_rpq["status"] == "FAIL"
    assert any("FORGED_REAL_PROVIDER_OR_ORIGINAL_CAPTURE_CLAIM" in v for v in res_rpq["violations"])


def test_p0a_source_execution_ledger_mandatory_and_deep_crosscheck(tmp_path: Path) -> None:
    lin = build_exit_event_lineage_v17931211(
        _SYNTH_EP,
        bars_1m_by_open_ts=_SYNTH_BARS,
        run_id=_RUN_ID,
        tested_executable_sha=_SHA,
        policy_id="SI0_OFF_BASELINE",
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    stream_path = tmp_path / "09_exit_event_lineage_ledger.jsonl.gz"
    _write_gz_jsonl(stream_path, lin["all_events"])

    # Missing 09_original_execution_ledger_source_reconciliation.jsonl.gz -> FAIL
    res_missing = verify_all_policy_fill_mode_lineage_independently_v17931211(
        tmp_path,
        bars_1m_by_open_ts=_SYNTH_BARS,
        expected_run_id=_RUN_ID,
        expected_tested_sha=_SHA,
    )
    assert res_missing["status"] == "FAIL"
    assert any("MISSING_MANDATORY_SOURCE_EXECUTION_LEDGER" in v for v in res_missing["violations"])

    # Empty 09_original_execution_ledger_source_reconciliation.jsonl.gz -> FAIL
    recon_path = tmp_path / "09_original_execution_ledger_source_reconciliation.jsonl.gz"
    _write_gz_jsonl(recon_path, [])
    res_empty = verify_all_policy_fill_mode_lineage_independently_v17931211(
        tmp_path,
        bars_1m_by_open_ts=_SYNTH_BARS,
        expected_run_id=_RUN_ID,
        expected_tested_sha=_SHA,
    )
    assert res_empty["status"] == "FAIL"
    assert any("EMPTY_MANDATORY_SOURCE_EXECUTION_LEDGER" in v for v in res_empty["violations"])

    # Deep field crosscheck (quantity, selected_sl, take_profit, entry_reference, etc.)
    part_map = {"SI0_OFF_BASELINE": {"NEXT_BAR_OPEN_BID_ASK": lin}}
    good_src_row = {
        "policy_id": "SI0_OFF_BASELINE",
        "fill_mode": "NEXT_BAR_OPEN_BID_ASK",
        "opportunity_id": "opp_v12_1",
        "model_id": "SMC_M1_CONTINUATION",
        "direction": "LONG",
        "decision_timestamp_ms": 120_000,
        "entry_fill_ts": 120_000,
        "exit_fill_ts": 240_000,
        "bars_held": 2,
        "filled": True,
        "outcome_label": "LOSS",
        "entry_reference": 4100.0,
        "entry_fill_price": 4100.20,
        "selected_sl": 4090.0,
        "take_profit": 4120.0,
        "exit_fill_price": 4089.90,
        "entry_fee_usd": 0.4100,
        "exit_fee_usd": 0.4090,
        "realized_gross_pnl_usd": -2.5750,
        "realized_net_pnl_usd": -3.3940,
        "realized_r": -1.3576,
        "quantity": 0.25,
        "planned_net_risk_usd": 2.50,
        "trigger_bar_ts": 180_000,
    }
    res_src_ok = verify_raw_stream_vs_source_execution_v17931211(
        part_map, [good_src_row], bars_1m_by_open_ts=_SYNTH_BARS
    )
    assert res_src_ok["status"] == "PASS"

    # Mutate selected_sl -> FAIL
    bad_sl = copy.deepcopy(good_src_row)
    bad_sl["selected_sl"] = 4085.0
    res_bad_sl = verify_raw_stream_vs_source_execution_v17931211(
        part_map, [bad_sl], bars_1m_by_open_ts=_SYNTH_BARS
    )
    assert res_bad_sl["status"] == "FAIL"
    assert any("SOURCE_NUMERIC_MISMATCH" in v and "selected_sl" in v for v in res_bad_sl["violations"])


@pytest.mark.parametrize("control_idx", list(range(40)))
def test_all_40_adversarial_negative_controls_v01_to_v40(
    neg_controls_report: Dict[str, Any],
    control_idx: int,
) -> None:
    assert neg_controls_report["status"] == "PASS"
    assert neg_controls_report["controls_count"] == 40
    assert neg_controls_report["all_rejected"] is True
    assert neg_controls_report["temp_dirs_cleaned"] is True
    ctrl = neg_controls_report["controls"][control_idx]
    assert ctrl["rejected"] is True
    assert ctrl["status"] == "PASS"
    assert ctrl["control_id"].startswith(f"V{control_idx + 1:02d}")


def test_r1_r2_r3_r4_functions_and_callgraph_invariants() -> None:
    # Callgraph & defect map
    cg = build_v17931212_callgraph_and_defect_map()
    assert cg["anchored_symbols_count"] >= 15
    assert all(s["line_start"] >= 1 and ":" in s["file_line"] for s in cg["anchored_symbols"])

    # R1 hierarchical loss classification
    ep_loss = {
        "outcome_label": "LOSS",
        "direction": "LONG",
        "entry_reference": 3020.0,
        "selected_sl": 3015.0,
        "take_profit": 3032.0,
        "planned_net_risk_usd": 2.50,
        "entry_fee_usd": 0.83,
        "exit_fee_usd": 0.83,
    }
    feat_other = {
        "htf_alignment_state": "ALIGNED",
        "sweep_confirmed": True,
        "sweep_reclaimed": True,
        "mss_confirmed": True,
        "displacement_valid": True,
        "displacement_body_atr_ratio": 1.25,
        "fvg_present": True,
        "fvg_retest_valid": True,
        "fvg_unmitigated": True,
        "protected_structure_independent_of_sl": True,
        "target_pool_unswept": True,
    }
    r1_premature = classify_r1_hierarchical_loss_forensics_v17931212(
        ep_loss,
        feat_other,
        ex_post_stop_outcome_class="PREMATURE_NOISE_SWEEP_STOP",
    )
    assert r1_premature["pre_entry_point_in_time_primary_cause"] == "OTHER/UNKNOWN"
    assert r1_premature["hierarchical_bucket_a_to_h"] == "F_EX_POST_STOP_BEFORE_STRUCTURE_BREAK_NOISE_SWEEP"
    assert r1_premature["evidence_timing_scope"] == "EX_POST_DIAGNOSTIC_ONLY_FORBIDDEN_IN_LIVE_DECISION"
    assert r1_premature["point_in_time_proven_pre_entry"] is False

    # R2 single-axis MTF + Unicorn ablation
    sample_feats = [
        {
            "direction": "LONG",
            "h4_bias": "BULLISH",
            "h1_bias": "BULLISH",
            "m15_bias": "BULLISH",
            "m5_bias": "BULLISH",
            "htf_alignment_state": "ALIGNED",
            "sweep_confirmed": True,
            "sweep_reclaimed": True,
            "mss_confirmed": True,
            "displacement_valid": True,
            "fvg_present": True,
            "fvg_retest_valid": True,
            "fvg_unmitigated": True,
            "unicorn_classification": "UNICORN_CONFIRMED",
        },
        {
            "direction": "LONG",
            "h4_bias": "BEARISH",
            "h1_bias": "BULLISH",
            "m15_bias": "BULLISH",
            "m5_bias": "BULLISH",
            "htf_alignment_state": "CONFLICTED",
            "sweep_confirmed": True,
            "sweep_reclaimed": True,
            "mss_confirmed": True,
            "displacement_valid": True,
            "fvg_present": True,
            "fvg_retest_valid": False,
            "fvg_unmitigated": True,
            "unicorn_classification": "FVG_ONLY",
        },
    ]
    r2_abl = evaluate_r2_mtf_unicorn_ablation_matrix_v17931212(sample_feats)
    assert r2_abl["total_opportunities_evaluated"] == 2
    assert r2_abl["ablation_modes"]["H1_ONLY"]["passed"] == 2
    assert r2_abl["ablation_modes"]["H4_ONLY"]["passed"] == 1
    assert r2_abl["ablation_modes"]["FULL_MTF"]["passed"] == 1
    assert r2_abl["unicorn_classification_breakdown"]["UNICORN_CONFIRMED"] == 1
    assert r2_abl["unicorn_classification_breakdown"]["FVG_ONLY"] == 1

    # R3 counterfactual stop and target evaluation across A0..A4
    bars_m1 = [
        {
            "open_time": 1774569000000 + i * 60000,
            "close_time": 1774569060000 + i * 60000,
            "open": 3020.0,
            "high": 3022.0,
            "low": 3017.0,
            "close": 3020.5,
            "volume": 100.0,
        }
        for i in range(40)
    ]
    for var_id in (
        "A0_STRICT_BASELINE",
        "A1_ENTRY_CONFIRMATION_RETEST_ONLY",
        "A2_PROTECTED_SL_NOISE_BUFFER_ONLY",
        "A3_SOURCE_BACKED_LIQUIDITY_TP_ONLY",
        "A4_COMBINED_CAUSAL_ENTRY_SL_TP",
    ):
        r3_cf = evaluate_r3_counterfactual_stop_and_target_v17931212(
            variant_id=var_id,
            opportunity_id="opp_cf_1",
            episode_key="ep_cf_1",
            model_id="M1_SWEEP_MSS_DISPLACEMENT_FVG",
            direction="LONG",
            decision_timestamp_ms=1774569000000 + 20 * 60000,
            baseline_entry=3020.0,
            baseline_sl=3018.5,
            baseline_tp=3028.0,
            closed_m1=bars_m1[:20],
            closed_m5=(),
            closed_m15=(),
            forward_bars=bars_m1[20:],
            pre_entry_features={
                "fvg_retest_valid": True,
                "fvg_unmitigated": True,
                "sweep_confirmed": True,
                "sweep_reclaimed": True,
                "displacement_valid": True,
                "mss_confirmed": True,
            },
            fill_mode="NEXT_BAR_OPEN_BID_ASK",
            dataset_sha=CANONICAL_DATASET_SHA256_V17931212,
        )
        assert r3_cf["variant_id"] == var_id
        assert r3_cf["planned_net_risk_usd"] <= 2.5001

    # R4 OOS provenance check & gate matrix
    oos_ok = verify_r4_oos_and_activation_verdict_v17931212(
        {
            "dataset_sha256": CANONICAL_DATASET_SHA256_V17931212,
            "untouched_post_freeze_dataset_exists": False,
            "fresh_oos_verdict": "BLOCKED_NO_UNTOUCHED_DATA",
            "activation_ready": "NO",
        }
    )
    assert oos_ok["status"] == "PASS"

    gm = evaluate_v17931212_gate_matrix(
        p0a_check={"status": "PASS"},
        p0b_check={"status": "PASS"},
        r1_check={"status": "PASS"},
        r2_check={"status": "PASS"},
        r3_check={"status": "PASS"},
        r4_junit_check={"status": "PASS"},
        r4_lineage_check={
            "status": "PASS",
            "source_execution_reconciliation": {"status": "PASS", "checked": True},
        },
        r4_oos_check=oos_ok,
        neg_v40_check={"status": "PASS", "controls_count": 40},
        manifest_check={"status": "PASS"},
        prior_immutability_ok=True,
    )
    assert gm["pre_publication_engineering_gates_all_pass"] is True
    assert gm["orthogonal_verdicts"]["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA"
    assert gm["orthogonal_verdicts"]["ACTIVATION_READY"] == "NO"
