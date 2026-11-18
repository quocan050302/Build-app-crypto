"""
backend/tests/test_kh_01a_r1_forensics_truth_closure.py

Comprehensive in-process unit, causal-anti-lookahead, macro/DST boundary, per-field
provenance, and anti-fake-pass gate tests for KH-01A.R1 — FORENSICS TRUTH CLOSURE.
(Pure in-process; included in controlled w=1/w=2/w=4/w=1_repeat determinism benchmark.)
"""
from __future__ import annotations

import copy
from datetime import datetime
import gzip
import inspect
import json
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo
import pytest

from services.kh_01a_foundation_service import (
    CANONICAL_DATASET_SHA256_KH01A,
    MANDATORY_BRANCH_KH01A,
    SCHEMA_VERSION_KH01A_R1,
    _make_feature_cell,
    build_kh_01a_causal_loss_and_win_control_forensics,
    classify_pre_entry_state_strictly_asof,
    evaluate_kh_01a_14_gates,
    validate_and_classify_macro_state_kh01a_r1,
)
from services.kh_01a_r1_forensics_closure_service import (
    build_dynamic_validated_gate_receipt_r1,
    scan_all_macro_window_call_sites_ast,
    validate_kh_01a_r1_forensics_bundle_strict,
)
from services.shared_strategy_evaluator import SharedStrategyEvaluator
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    evaluate_shared_delayed_entry_adapter_v17931213,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import (
    EvidenceReceiptV17931219,
    build_loss_episode_attribution_from_raw_v17931219,
    validate_r1_forensics_summary_and_macro_strict_v17931219,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    build_close_ts_index_v179312,
)
from services.v17_9_3_macro_unicorn_intelligence import (
    resolve_causal_macro_time_window,
    validate_macro_schedule_config,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "aurum-desk"
V130_RUN = APP_ROOT / "docs/artifacts/v17_9_3_1_2_10/20261107T150245Z_b772a03bb7cc"
NY_TZ = ZoneInfo("America/New_York")


def _ny_epoch_ms(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0, ms: int = 0) -> int:
    dt = datetime(y, m, d, hh, mm, ss, ms * 1000, tzinfo=NY_TZ)
    return int(round(dt.timestamp() * 1000.0))


def test_r1_ast_all_macro_call_sites_valid_in_repo() -> None:
    sig = inspect.signature(resolve_causal_macro_time_window)
    params = list(sig.parameters.keys())
    assert params[0] == "decision_timestamp_ms"
    assert params[1] == "symbol"
    assert "schedule_config" in params

    ast_report = scan_all_macro_window_call_sites_ast(REPO_ROOT, include_tests=True)
    assert ast_report["invalid_production_call_sites_count"] == 0
    assert ast_report["production_call_sites_count"] >= 9


def test_r1_macro_reversed_args_red_and_keyword_caller_green() -> None:
    """Reproduce exact RED reversed call vs GREEN keyword call and verify real nested forensic caller."""
    datasets, prov = load_canonical_xauusdt_datasets()
    close_ts = build_close_ts_index_v179312(datasets)

    # Pick one real M1 bar inside an NY ICT macro window and one outside on a weekday
    in_win_bar = None
    out_win_bar = None
    for b in datasets["1m"][200:8000]:
        c_ms = int(b["close_time"])
        st = resolve_causal_macro_time_window(decision_timestamp_ms=c_ms, symbol="XAUUSDT")
        if st.eligible_for_macro_gate and in_win_bar is None:
            in_win_bar = b
        elif not st.eligible_for_macro_gate and not st.is_weekend_or_closed and out_win_bar is None:
            out_win_bar = b
        if in_win_bar is not None and out_win_bar is not None:
            break

    assert in_win_bar is not None and out_win_bar is not None
    ts_in = int(in_win_bar["close_time"])
    ts_out = int(out_win_bar["close_time"])

    # RED reproduction: reversed positional arguments ("XAUUSDT", ts_in)
    red_state = resolve_causal_macro_time_window("XAUUSDT", ts_in)  # type: ignore[arg-type]
    assert red_state.is_valid_timestamp is False
    assert red_state.session_group == "INVALID"
    assert red_state.eligible_for_macro_gate is False
    red_val = validate_and_classify_macro_state_kh01a_r1(
        red_state, expected_decision_timestamp_ms=ts_in, expected_symbol="XAUUSDT"
    )
    assert red_val["macro_state_category"] == "UNKNOWN_OR_INVALID_MACRO_STATE"
    assert red_val["in_ict_macro_window_value"] is None
    assert red_val["cell_state"] == "INVALID"

    # GREEN real forensic caller (unmocked resolve_causal_macro_time_window!)
    px_in = float(in_win_bar["close"])
    px_out = float(out_win_bar["close"])
    ep_in = {
        "opportunity_id": "R1_TEST_IN_MACRO",
        "episode_key": "EP_IN",
        "model_id": "SMC_M1_CONTINUATION",
        "direction": "LONG",
        "decision_timestamp_ms": ts_in,
        "entry_reference": px_in,
        "entry_fill_price": round(px_in + 0.10, 2),
        "selected_sl": round(px_in - 4.0, 2),
        "take_profit": round(px_in + 10.0, 2),
        "outcome_label": "LOSS",
        "realized_net_pnl_usd": -2.45,
        "realized_r": -0.98,
    }
    ep_out = {
        "opportunity_id": "R1_TEST_OUT_MACRO",
        "episode_key": "EP_OUT",
        "model_id": "SMC_M1_CONTINUATION",
        "direction": "SHORT",
        "decision_timestamp_ms": ts_out,
        "entry_reference": px_out,
        "entry_fill_price": round(px_out - 0.10, 2),
        "selected_sl": round(px_out + 4.0, 2),
        "take_profit": round(px_out - 10.0, 2),
        "outcome_label": "WIN",
        "realized_net_pnl_usd": 5.05,
        "realized_r": 2.02,
    }
    res = build_kh_01a_causal_loss_and_win_control_forensics(
        si0_all_primary_episodes=[ep_in, ep_out],
        si8_loss_rows=[],
        si9_loss_rows=[],
        datasets=datasets,
        close_ts_index=close_ts,
        dataset_sha256=prov["dataset_sha256"],
    )
    r_in = res["loss_casebook_157"][0]
    r_out = res["win_casebook_9"][0]

    assert r_in["pre_entry_features"]["ny_session_group"]["value"] != "INVALID"
    assert r_in["pre_entry_features"]["in_ict_macro_window"]["value"] is True
    assert r_in["pre_entry_features"]["in_ict_macro_window"]["proof_state"] == "PROVEN_TRUE"
    assert r_in["macro_window_state"]["macro_state_category"] == "ELIGIBLE_IN_VERIFIED_WINDOW"
    assert "OUTSIDE_ICT_MACRO_WINDOW" not in r_in["pre_entry_known"]["active_pre_entry_risk_flags"]

    assert r_out["pre_entry_features"]["ny_session_group"]["value"] == "OUTSIDE_MACRO"
    assert r_out["pre_entry_features"]["in_ict_macro_window"]["value"] is False
    assert r_out["pre_entry_features"]["in_ict_macro_window"]["proof_state"] == "PROVEN_FALSE"
    assert r_out["macro_window_state"]["macro_state_category"] == "VALID_OUTSIDE_WINDOW"
    assert "OUTSIDE_ICT_MACRO_WINDOW" in r_out["pre_entry_known"]["active_pre_entry_risk_flags"]


def test_r1_macro_dst_boundaries_weekends_and_invalid_inputs() -> None:
    sched = validate_macro_schedule_config(None)
    assert sched.is_valid is True

    # Half-open [start, end) across all 7 configured NY macro windows
    for w in sched.windows:
        sh, sm = (int(x) for x in w.start_hhmm.split(":"))
        eh, em = (int(x) for x in w.end_hhmm.split(":"))
        t0 = _ny_epoch_ms(2026, 10, 14, sh, sm, 0, 0)
        t1 = _ny_epoch_ms(2026, 10, 14, eh, em, 0, 0)
        assert resolve_causal_macro_time_window(decision_timestamp_ms=t0 - 1, symbol="XAUUSDT").eligible_for_macro_gate is False
        assert resolve_causal_macro_time_window(decision_timestamp_ms=t0, symbol="XAUUSDT").eligible_for_macro_gate is True
        assert resolve_causal_macro_time_window(decision_timestamp_ms=t1 - 1, symbol="XAUUSDT").eligible_for_macro_gate is True
        assert resolve_causal_macro_time_window(decision_timestamp_ms=t1, symbol="XAUUSDT").eligible_for_macro_gate is False

    # US DST Spring Forward (2026-03-06 EST -> 2026-03-09 EDT) and Fall Back (2026-11-02 EST)
    st_est1 = resolve_causal_macro_time_window(decision_timestamp_ms=_ny_epoch_ms(2026, 3, 6, 9, 55), symbol="XAUUSDT")
    st_edt = resolve_causal_macro_time_window(decision_timestamp_ms=_ny_epoch_ms(2026, 3, 9, 9, 55), symbol="XAUUSDT")
    st_est2 = resolve_causal_macro_time_window(decision_timestamp_ms=_ny_epoch_ms(2026, 11, 2, 9, 55), symbol="XAUUSDT")
    assert st_est1.is_dst is False and st_est1.utc_offset_minutes == -300 and st_est1.eligible_for_macro_gate is True
    assert st_edt.is_dst is True and st_edt.utc_offset_minutes == -240 and st_edt.eligible_for_macro_gate is True
    assert st_est2.is_dst is False and st_est2.utc_offset_minutes == -300 and st_est2.eligible_for_macro_gate is True

    # Weekend -> VALID_OUTSIDE_WINDOW with WEEKEND_CLOSED (never INVALID!)
    st_sat = resolve_causal_macro_time_window(decision_timestamp_ms=_ny_epoch_ms(2026, 10, 17, 10, 0), symbol="XAUUSDT")
    v_sat = validate_and_classify_macro_state_kh01a_r1(
        st_sat, expected_decision_timestamp_ms=_ny_epoch_ms(2026, 10, 17, 10, 0), expected_symbol="XAUUSDT"
    )
    assert v_sat["macro_state_category"] == "VALID_OUTSIDE_WINDOW"
    assert v_sat["ny_session_group_value"] == "WEEKEND_CLOSED"
    assert v_sat["in_ict_macro_window_value"] is False

    # Unverified holiday calendar required -> MISSING / UNKNOWN (never proven False!)
    ts_mon = _ny_epoch_ms(2026, 10, 19, 10, 0)
    st_cal_unv = resolve_causal_macro_time_window(
        decision_timestamp_ms=ts_mon,
        symbol="XAUUSDT",
        schedule_config={"require_verified_holiday_calendar": True, "calendar_verification_status": "CALENDAR_NOT_VERIFIED"},
    )
    v_cal_unv = validate_and_classify_macro_state_kh01a_r1(st_cal_unv, expected_decision_timestamp_ms=ts_mon)
    assert v_cal_unv["macro_state_category"] == "UNKNOWN_OR_INVALID_MACRO_STATE"
    assert v_cal_unv["cell_state"] == "MISSING"
    assert v_cal_unv["in_ict_macro_window_value"] is None


def test_r1_classifier_unknown_never_proven_false_and_partial_completeness() -> None:
    sig = inspect.signature(classify_pre_entry_state_strictly_asof)
    for forbidden in ("outcome_label", "exit_type", "exit_fill_price", "realized_net_pnl_usd", "forward_bars"):
        assert forbidden not in sig.parameters

    dec_ts = 1792000020000
    feats = {
        "h4_structure_bias": _make_feature_cell(
            "h4_structure_bias", "BULLISH", state="PRESENT", proof_state="MEASURED_VALUE",
            known_at_ms=dec_ts - 3600_000, decision_ts_ms=dec_ts, raw_source_ids=["XAUUSDT:4h:1791993600000"],
        ),
        "h1_structure_bias": _make_feature_cell(
            "h1_structure_bias", None, state="MISSING", proof_state="UNKNOWN",
            known_at_ms=None, decision_ts_ms=dec_ts, raw_source_ids=[],
        ),
        "in_ict_macro_window": _make_feature_cell(
            "in_ict_macro_window", None, state="MISSING", proof_state="UNKNOWN",
            known_at_ms=None, decision_ts_ms=dec_ts, raw_source_ids=[],
        ),
        "liquidity_sweep_reclaim_confirmed": _make_feature_cell(
            "liquidity_sweep_reclaim_confirmed", None, state="MISSING", proof_state="UNKNOWN",
            known_at_ms=None, decision_ts_ms=dec_ts, raw_source_ids=[],
        ),
        "mss_bos_displacement_confirmed": _make_feature_cell(
            "mss_bos_displacement_confirmed", None, state="MISSING", proof_state="UNKNOWN",
            known_at_ms=None, decision_ts_ms=dec_ts, raw_source_ids=[],
        ),
        "unicorn_overlap_state": _make_feature_cell(
            "unicorn_overlap_state", None, state="MISSING", proof_state="UNKNOWN",
            known_at_ms=None, decision_ts_ms=dec_ts, raw_source_ids=[],
        ),
    }
    cls = classify_pre_entry_state_strictly_asof(
        decision_timestamp_ms=dec_ts,
        direction="LONG",
        pre_entry_features=feats,
        raw_snapshot_joined=True,
    )
    assert cls["evidence_completeness"] == "PARTIAL"
    assert cls["primary_pre_entry_code"] == "ALIGNED_ON_PRESENT_FIELDS_WITH_PARTIAL_UNKNOWN_EVIDENCE"
    assert cls["active_pre_entry_risk_flags"] == []
    assert "ICT_MACRO_WINDOW_UNKNOWN" in cls["unknown_or_unproven_flags"]
    assert "SWEEP_RECLAIM_UNKNOWN" in cls["unknown_or_unproven_flags"]
    assert "MSS_DISPLACEMENT_UNKNOWN" in cls["unknown_or_unproven_flags"]
    assert "TRUE_UNICORN_OVERLAP_UNKNOWN" in cls["unknown_or_unproven_flags"]


def test_r1_per_field_provenance_and_no_selected_id_inference() -> None:
    datasets, prov = load_canonical_xauusdt_datasets()
    close_ts = build_close_ts_index_v179312(datasets)

    d10_first = next(
        json.loads(ln)
        for ln in gzip.open(V130_RUN / "10_canonical_decision_inputs.jsonl.gz", "rt", encoding="utf-8")
        if ln.strip()
    )
    oid = str(d10_first["opportunity_id"])
    f07_first = next(
        json.loads(ln)
        for ln in gzip.open(V130_RUN / "07_verified_causal_features.jsonl.gz", "rt", encoding="utf-8")
        if oid in ln
    )
    ep = {
        "opportunity_id": oid,
        "episode_key": f"ep_{oid}",
        "model_id": d10_first["model_id"],
        "direction": d10_first["direction"],
        "decision_timestamp_ms": int(d10_first["decision_timestamp_ms"]),
        "entry_reference": float(d10_first["entry_reference"]),
        "entry_fill_price": float(d10_first["entry_reference"]),
        "selected_sl": float(d10_first["selected_sl"]),
        "take_profit": float(d10_first["take_profit"]),
        "outcome_label": "LOSS",
        "realized_net_pnl_usd": -2.40,
        "realized_r": -0.96,
    }
    res = build_kh_01a_causal_loss_and_win_control_forensics(
        si0_all_primary_episodes=[ep],
        si8_loss_rows=[],
        si9_loss_rows=[],
        decision_inputs_by_opp_id={oid: d10_first},
        feature_ledger_by_opp_id={oid: f07_first},
        datasets=datasets,
        close_ts_index=close_ts,
        dataset_sha256=prov["dataset_sha256"],
    )
    row = res["loss_casebook_157"][0]
    assert row["provenance_summary"]["original_snapshot_verified"] is False
    assert row["provenance_summary"]["full_original_runtime_context_status"] == "NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS"
    assert row["provenance_summary"]["original_vs_recomputed_fingerprint_status"] == "MATCH"

    # H1 bias was measured in f07 -> PERSISTED_VERIFIED_SNAPSHOT; ATR M1 was recomputed -> RECONSTRUCTED_ASOF_RAW_BARS
    assert row["pre_entry_features"]["h1_structure_bias"]["source_origin"] == "PERSISTED_VERIFIED_SNAPSHOT"
    assert row["pre_entry_features"]["atr_m1_usd"]["source_origin"] == "RECONSTRUCTED_ASOF_RAW_BARS"
    # Even though d10_first has selected_sweep_id & selected_break_id, f07 sweep_confirmed/mss_confirmed are None -> MUST stay MISSING!
    assert row["pre_entry_features"]["liquidity_sweep_reclaim_confirmed"]["state"] == "MISSING"
    assert row["pre_entry_features"]["liquidity_sweep_reclaim_confirmed"]["value"] is None
    assert row["pre_entry_features"]["mss_bos_displacement_confirmed"]["state"] == "MISSING"
    assert row["pre_entry_features"]["mss_bos_displacement_confirmed"]["value"] is None

    # Also test build_loss_episode_attribution_from_raw_v17931219 compatibility
    v19_attr = build_loss_episode_attribution_from_raw_v17931219(
        si0_loss_rows=[ep],
        si8_loss_rows=[],
        si9_loss_rows=[],
        decision_inputs_by_opp_id={oid: d10_first},
        feature_ledger_by_opp_id={oid: f07_first},
        datasets=datasets,
        close_ts_index=close_ts,
        dataset_sha256=prov["dataset_sha256"],
    )
    assert v19_attr["baseline_si0_losses_count"] == 1

    # Future bar close lookahead & placeholder bar IDs must raise ValueError
    with pytest.raises(ValueError, match="BAR_CLOSE_LOOKAHEAD_VIOLATION"):
        _make_feature_cell(
            "h4_structure_bias", "BEARISH", state="PRESENT", proof_state="RECONSTRUCTED_CAUSAL_VALUE",
            known_at_ms=1792000000000, closed_bar_close_ms=1792000060000,
            decision_ts_ms=1792000000000, raw_source_ids=["XAUUSDT:4h:1792000000000"],
        )
    with pytest.raises(ValueError, match="PLACEHOLDER_SOURCE_ID_REJECTED"):
        _make_feature_cell(
            "h4_structure_bias", "BEARISH", state="PRESENT", proof_state="RECONSTRUCTED_CAUSAL_VALUE",
            known_at_ms=1792000000000, decision_ts_ms=1792000000000, raw_source_ids=["PLACEHOLDER_BAR_1"],
        )


def test_r1_gate_rejects_invalid_session_and_legacy_schema_even_when_166_conserved(tmp_path: Path) -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    run_id = "TEST_R1_GATE"

    # Dynamic receipt never pre-labels PASS when validator fails
    t_json = tmp_path / "f06.json"
    write_json_durable_atomic(t_json, {"test": 1})
    fail_rcpt, _ = build_dynamic_validated_gate_receipt_r1(
        gate_id="G-KH01A-R1-04",
        run_dir=tmp_path,
        run_id=run_id,
        tested_sha=sha,
        target_rel_path="f06.json",
        validator_fn=lambda: {"status": "FAIL", "violations": ["INVALID_MACRO_SESSION"]},
        write_json_fn=write_json_durable_atomic,
    )
    assert fail_rcpt.status == "FAIL"
    assert not fail_rcpt.is_valid_pass(expected_run_id=run_id, expected_tested_sha=sha, run_dir=tmp_path)

    # Legacy schema & INVALID session rejected even when 166 = 9 + 157 holds!
    poisoned_06: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "ledger_conservation": {
            "closed_equals_wins_plus_losses": True,
            "si0_closed_total": 166,
            "si0_wins_count": 9,
            "si0_losses_count": 157,
            "si8_si9_unique_losses_count": 4,
            "si8_si9_shared_loss_ids_count": 2,
            "total_casebook_rows_delivered": 170,
        },
        "anti_hindsight_contract": {
            "pre_entry_classifier_receives_post_entry_fields": False,
            "ex_post_leakage_into_pre_entry_count": 0,
            "max_known_at_minus_decision_ts_ms": 0,
            "max_closed_bar_close_minus_decision_ts_ms": 0,
        },
        "ny_session_group_census_all_170": {"INVALID": 170},
        "macro_3way_census_all_170": {"UNKNOWN_OR_INVALID_MACRO_STATE": 170},
        "all_170_invalid_evidence_rows_count": 170,
        "original_vs_recomputed_fingerprint_census_all_170": {"MATCH": 166, "NOT_COMPARABLE": 4},
        "si0_157_losses_aggregates": {"by_ny_session_group": {"INVALID": 157}},
        "si0_9_wins_control_aggregates": {"by_ny_session_group": {"INVALID": 9}},
    }
    with pytest.raises(ValueError, match="INVALID_MACRO_SESSION_IN_FORENSICS_SUMMARY"):
        validate_r1_forensics_summary_and_macro_strict_v17931219(poisoned_06)

    legacy_06 = {**copy.deepcopy(poisoned_06), "schema_version": "kh_01a.foundation.v1"}
    with pytest.raises(ValueError, match="LEGACY_OR_INVALID_R1_FORENSICS_SCHEMA"):
        validate_r1_forensics_summary_and_macro_strict_v17931219(legacy_06)

    ok_rcpt = EvidenceReceiptV17931219(
        gate_id="G-KH01A-06", status="PASS", receipt_path="f06.json", content_sha256=fail_rcpt.content_sha256,
        tested_sha=sha, run_id=run_id, validator_name="v", validator_code_sha="a1b2" * 10,
        input_sha256=fail_rcpt.content_sha256, observed_status="PASS",
    )
    g14_out = evaluate_kh_01a_14_gates(
        run_dir=tmp_path,
        run_id=run_id,
        branch=MANDATORY_BRANCH_KH01A,
        tested_sha=sha,
        dataset_sha=CANONICAL_DATASET_SHA256_KH01A,
        preflight_00={"source_tree_unchanged_during_run": True},
        gap_audit_02={"prepatch_red_confirmed": True, "postpatch_mon_fri_m15_category": "PROVIDER_OR_DATA_GAP", "unverified_weekend_labels_emitted_count": 0, "missing_bar_totals_conserved": True},
        dataset_quality_03={"synthetic_fill_applied": False, "interpolation_applied": False, "forward_fill_applied": False},
        fault_suite_04={"status": "PASS", "failed_fault_injections": 0, "report_assertions_counted_as_faults": 0, "synthetic_nonzero_d4_positive_fixture_verification": {"passed": True}},
        forensics_06=poisoned_06,
        target_audit_07={"verification": {"status": "PASS"}, "summary": {"historical_267_cohort_count": 267, "control_group_count": 40}, "executable_tp_selection_modified": False},
        replay_08={"p0a_accounting_status": "PASS", "lineage_30_partitions_status": "PASS", "d4_execution_truth_status": "PASS"},
        parity_09={"before_after_evaluator_decision_fingerprint_parity": True, "before_after_trade_action_fingerprint_parity": True, "three_adapter_parity_status": "PASS", "si0_si8_si9_accounting_exact_match_vs_baseline": True},
        junit_manifest_10={"independent_bijection_verification": {"status": "PASS", "counters": {"collected": 5500, "failed": 0, "errors": 0, "mandatory_skipped": 0}}, "shard_manifest": {"nonzero_exit_shards_count": 0}},
        worker_report_11={"pytest_subset_w1_w2_w4_repeat_outcome_hash_identical": True, "replay_slice_w1_w2_w4_repeat_parity_status": "PASS", "measured_speedup_w_parallel_vs_w1": 2.5},
        resource_report_12={"max_children_observed": 3, "orphan_pids_at_cleanup_count": 0, "temp_dirs_cleaned_with_sentinel_check": True, "disk_reserve_breached": False},
        fresh_oos_13={"FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA"},
        receipts_by_gate={f"G-KH01A-{i:02d}": ok_rcpt for i in range(1, 15)},
    )
    g06 = next(g for g in g14_out["gates"] if g["gate_id"] == "G-KH01A-06")
    assert g06["status"] == "FAIL"
    assert g14_out["orthogonal_verdicts"]["FORENSICS_SEMANTIC_VERDICT"] == "FAIL"
    assert g14_out["orthogonal_verdicts"]["ENGINEERING_VERDICT"] == "FAIL"


def test_r1_evaluator_and_3_adapter_parity() -> None:
    from tools.run_v17_9_3_macro_unicorn_backtest import (
        V1793_VARIANT_POLICIES,
        build_v1793_variant_market_context,
    )

    datasets, prov = load_canonical_xauusdt_datasets()
    close_ts = build_close_ts_index_v179312(datasets)
    pol_a1 = dict(V1793_VARIANT_POLICIES["A1_V1792_SL_TP_BASELINE"])
    pol_a1["mtf_structure_observe_only"] = False

    d10_rows = []
    for ln in gzip.open(V130_RUN / "10_canonical_decision_inputs.jsonl.gz", "rt", encoding="utf-8"):
        if ln.strip():
            r = json.loads(ln)
            if not r.get("discovered_by_si8"):
                d10_rows.append(r)
            if len(d10_rows) >= 15:
                break

    for rec in d10_rows:
        ts = int(rec["decision_timestamp_ms"])
        i1 = len([x for x in close_ts["1m"] if x <= ts])
        i5 = len([x for x in close_ts["5m"] if x <= ts])
        i15 = len([x for x in close_ts["15m"] if x <= ts])
        ih1 = len([x for x in close_ts["1H"] if x <= ts])
        ih4 = len([x for x in close_ts["4H"] if x <= ts])
        id1 = len([x for x in close_ts["1D"] if x <= ts])
        w1 = datasets["1m"][max(0, i1 - 120) : i1]
        lc = float(w1[-1]["close"])
        ctx = build_v1793_variant_market_context(
            {
                "symbol": "XAUUSDT",
                "decision_timestamp_ms": ts,
                "m1_candles": w1,
                "m5_candles": datasets["5m"][max(0, i5 - 96) : i5],
                "m15_candles": datasets["15m"][max(0, i15 - 96) : i15],
                "h1_candles": datasets["1H"][max(0, ih1 - 96) : ih1],
                "h4_candles": datasets["4H"][max(0, ih4 - 64) : ih4],
                "d1_candles": datasets["1D"][max(0, id1 - 30) : id1],
                "bid": round(lc - 0.10, 2),
                "ask": round(lc + 0.10, 2),
            },
            pol_a1,
            decision_time=ts,
        )
        sn = SharedStrategyEvaluator.evaluate_model_direction(ctx, str(rec["model_id"]), str(rec["direction"]))
        assert sn.decision_fingerprint == rec["decision_fingerprint"]
        assert sn.trade_action_fingerprint == rec["trade_action_fingerprint"]

    # 3-adapter dry-run parity on first record
    r0 = d10_rows[0]
    ts0 = int(r0["decision_timestamp_ms"])
    i1 = len([x for x in close_ts["1m"] if x <= ts0])
    i5 = len([x for x in close_ts["5m"] if x <= ts0])
    i15 = len([x for x in close_ts["15m"] if x <= ts0])
    ih1 = len([x for x in close_ts["1H"] if x <= ts0])
    ih4 = len([x for x in close_ts["4H"] if x <= ts0])
    adapter_hashes = set()
    for mode_ctx in ("REPLAY_BACKTEST", "MARKET_SHADOW", "PRODUCTION_ORCHESTRATOR_DRY_RUN"):
        out = evaluate_shared_delayed_entry_adapter_v17931213(
            execution_context_mode=mode_ctx,
            activation_flags={"LIVE": False, "PAPER": False, "AUTO_LIVE": False, "AUTO_PAPER": False, "enabled_in_production": False},
            variant_id="D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
            opportunity_id=str(r0["opportunity_id"]),
            episode_key=f"pre_{r0['opportunity_id']}",
            model_id=str(r0["model_id"]),
            direction=str(r0["direction"]),
            decision_timestamp_ms=ts0,
            baseline_entry=float(r0["entry_reference"]),
            baseline_sl=float(r0["selected_sl"]),
            baseline_tp=float(r0["take_profit"]),
            closed_m1=datasets["1m"][max(0, i1 - 180) : i1],
            closed_m5=datasets["5m"][max(0, i5 - 60) : i5],
            closed_m15=datasets["15m"][max(0, i15 - 60) : i15],
            closed_h1=datasets["1H"][max(0, ih1 - 60) : ih1],
            closed_h4=datasets["4H"][max(0, ih4 - 60) : ih4],
            forward_bars=datasets["1m"][i1 : i1 + 180],
            pre_entry_features={"require_true_unicorn": True},
            fill_mode="NEXT_BAR_OPEN_BID_ASK",
            dataset_sha=prov["dataset_sha256"],
        )
        adapter_hashes.add(canonical_hash({k: v for k, v in out.items() if k != "adapter_execution_context_mode"}))
    assert len(adapter_hashes) == 1
