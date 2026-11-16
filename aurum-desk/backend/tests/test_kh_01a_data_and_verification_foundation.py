"""
backend/tests/test_kh_01a_data_and_verification_foundation.py

Focused unit, regression, causal-anti-lookahead, and adversarial fault-injection tests
for KH-01A — DATA & VERIFICATION FOUNDATION (SPEC VERSION: KH-01A-v1.0).
"""
from __future__ import annotations

import bisect
import copy
from dataclasses import asdict
import errno
import hashlib
import inspect
from pathlib import Path
import time
from typing import Any, Dict
import pytest

from domain_calculator import CostAssumptions
from services.kh_01a_foundation_service import (
    CANONICAL_DATASET_SHA256_KH01A,
    DatasetManifestEntryKH01A,
    MANDATORY_BRANCH_KH01A,
    SPEC_VERSION_KH01A,
    build_dataset_registry_and_fresh_oos_audit_kh01a,
    build_kh_01a_callgraph_and_keep_as_is_matrix,
    build_kh_01a_causal_loss_and_win_control_forensics,
    classify_pre_entry_state_strictly_asof,
    run_kh_01a_real_fault_injections,
    validate_fresh_oos_escrow_claim_kh01a,
)
from services.shared_strategy_evaluator import (
    SharedStrategyEvaluator,
    build_market_decision_context,
)
from services.v17_9_1_decision_funnel import classify_historical_dataset_gaps
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    evaluate_shared_delayed_entry_adapter_v17931213,
)
from services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service import (
    audit_target_reachability_row_v17931214,
    compute_causal_tf_atr_v17931214,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import (
    EvidenceReceiptV17931219,
    TypedMetricValidationError,
    build_authoritative_metrics_from_evidence_v17931219,
    build_loss_episode_attribution_from_raw_v17931219,
    evaluate_v17931219_orthogonal_gate_matrix,
    parse_required_pytest_counts_v17931219,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    build_close_ts_index_v179312,
)
from tools.fresh_junit_io import (
    DurableArtifactWriteError,
    write_bytes_durable_atomic,
    write_json_durable_atomic,
    write_jsonl_gz_durable_atomic,
)
from tools.run_v17_9_3_1_2_19_verification import _SafeStreamWrapper
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "aurum-desk"


def _make_bar(open_ms: int, tf_ms: int, px: float = 4000.0, vol: float = 10.0, is_closed: bool = True) -> Dict[str, Any]:
    return {
        "timestamp": open_ms,
        "open_time": open_ms,
        "close_time": open_ms + tf_ms,
        "open": px,
        "high": px + 1.5,
        "low": px - 1.5,
        "close": px + 0.5,
        "volume": vol,
        "is_closed": is_closed,
    }


# ==============================================================================
# SECTION 1: DATA GAP TRUTH TESTS
# ==============================================================================

def test_sec1_weekday_ge24h_gap_not_weekend() -> None:
    """Mon 2026-10-12 08:15Z -> Fri 2026-10-16 01:00Z (>=24h weekday gap) must NEVER be WEEKEND_MARKET_CLOSED."""
    mon_bar_open = 1791792000000  # 2026-10-12T08:00:00Z (Monday)
    fri_bar_open = 1792111500000  # 2026-10-16T00:45:00Z (Friday, closes 01:00:00Z)
    m15_bars = [
        _make_bar(mon_bar_open, 900_000),
        _make_bar(fri_bar_open, 900_000),
    ]
    # Case A: Without cross-TF bars and without verified calendar -> UNKNOWN_UNVERIFIED (never WEEKEND!)
    out_unverified = classify_historical_dataset_gaps({"15m": m15_bars})
    g_unv = out_unverified["timeframes"]["M15"]["all_gaps"][0]
    assert g_unv["missing_bars"] == 354
    assert g_unv["category"] == "UNKNOWN_UNVERIFIED"
    assert g_unv["category"] != "WEEKEND_MARKET_CLOSED"
    assert g_unv["legacy_v1791_category_deprecated"] == "WEEKEND_MARKET_CLOSED"
    assert out_unverified["session_calendar_verification_status"] == "UNKNOWN_UNVERIFIED"

    # Case B: With active H1 bars spanning the weekday gap -> PROVIDER_OR_DATA_GAP
    h1_bars = [_make_bar(mon_bar_open + k * 3_600_000, 3_600_000, vol=25.0) for k in range(90)]
    out_proven = classify_historical_dataset_gaps({"15m": m15_bars, "1H": h1_bars})
    g_prv = out_proven["timeframes"]["M15"]["all_gaps"][0]
    assert g_prv["category"] == "PROVIDER_OR_DATA_GAP"
    assert g_prv["source_confidence"] == "HIGH"
    assert g_prv["cross_tf_contained_active_bars"]["H1"] > 0


def test_sec1_real_weekend_crossing_verified_vs_mixed_interval_split() -> None:
    """Verified weekend closure vs mixed closed+Monday-open gap splitting & contradiction detection."""
    verified_cfd_cal = {
        "verified": True,
        "calendar_version": "TEST-NY-SESSION-v1",
        "evidence_source": "VERIFIED_EXCHANGE_SCHEDULE_FIXTURE",
        "timezone": "America/New_York",
        "weekly_closure_rules": [
            {"close_weekday": 4, "close_hour": 17, "close_minute": 0, "reopen_weekday": 6, "reopen_hour": 18, "reopen_minute": 0}
        ],
        "default_unlisted_open": True,
    }
    # Pure weekend closure in EDT (Oct 2026, UTC-4): Fri 21:00Z -> Sun 22:00Z
    fri_20z = 1792180800000  # 2026-10-16T20:00:00Z (closes 21:00:00Z = Fri 17:00 EDT)
    sun_22z = 1792360800000  # 2026-10-18T22:00:00Z (= Sun 18:00 EDT)
    pure_wknd = classify_historical_dataset_gaps(
        {"1H": [_make_bar(fri_20z, 3_600_000), _make_bar(sun_22z, 3_600_000)]},
        session_calendar=verified_cfd_cal,
    )
    g_pure = pure_wknd["timeframes"]["H1"]["all_gaps"][0]
    assert g_pure["category"] == "VERIFIED_SCHEDULED_MARKET_CLOSURE"
    assert pure_wknd["session_calendar_verification_status"] == "VERIFIED"

    # Mixed interval: Fri 21:00Z -> Mon 12:00Z (includes 14h of Monday open trading!)
    mon_12z = 1792411200000  # 2026-10-19T12:00:00Z
    mixed_wknd = classify_historical_dataset_gaps(
        {"1H": [_make_bar(fri_20z, 3_600_000), _make_bar(mon_12z, 3_600_000)]},
        session_calendar=verified_cfd_cal,
    )
    g_mix = mixed_wknd["timeframes"]["H1"]["all_gaps"][0]
    assert g_mix["category"] == "MIXED_INTERVAL_GAP"
    assert len(g_mix["segments"]) == 2
    assert g_mix["segments"][0]["segment_category"] == "VERIFIED_SCHEDULED_MARKET_CLOSURE"
    assert g_mix["segments"][0]["minutes"] == 2940.0  # 49h Fri 21:00Z..Sun 22:00Z
    assert g_mix["segments"][1]["segment_category"] == "PROVIDER_OR_DATA_GAP"
    assert g_mix["segments"][1]["minutes"] == 840.0   # 14h Sun 22:00Z..Mon 12:00Z

    # Contradiction: active M15 bars observed inside declared closure
    m15_during_sat = [_make_bar(fri_20z + 7200_000 + i * 900_000, 900_000, vol=15.0) for i in range(4)]
    contradicted = classify_historical_dataset_gaps(
        {"1H": [_make_bar(fri_20z, 3_600_000), _make_bar(sun_22z, 3_600_000)], "15m": m15_during_sat},
        session_calendar=verified_cfd_cal,
    )
    assert contradicted["session_calendar_verification_status"] == "CONTRADICTED_BY_OBSERVED_BARS"
    assert contradicted["calendar_contradictions_count"] == 1


def test_sec1_verified_maintenance_and_ny_dst_transition() -> None:
    """Daily maintenance 17:00..18:00 America/New_York shifts from 21:00..22:00Z (EDT) to 22:00..23:00Z (EST) across Nov 1, 2026 DST."""
    maint_cal = {
        "verified": True,
        "calendar_version": "TEST-NY-MAINT-DST-v1",
        "evidence_source": "EXCHANGE_MAINT_RECEIPT",
        "timezone": "America/New_York",
        "daily_maintenance_rules": [
            {"weekdays": [0, 1, 2, 3], "start_hour": 17, "start_minute": 0, "duration_minutes": 60}
        ],
        "default_unlisted_open": True,
    }
    # Wed 2026-10-28 (EDT, UTC-4): 21:00Z..22:00Z is 17:00..18:00 NY
    oct28_20z = 1793217600000  # 2026-10-28T20:00:00Z (closes 21:00Z)
    oct28_22z = 1793224800000  # 2026-10-28T22:00:00Z
    res_edt = classify_historical_dataset_gaps(
        {"15m": [_make_bar(oct28_20z + 3 * 900_000, 900_000), _make_bar(oct28_22z, 900_000)]},
        session_calendar=maint_cal,
    )
    assert res_edt["timeframes"]["M15"]["all_gaps"][0]["category"] == "VERIFIED_MAINTENANCE_PAUSE"

    # Wed 2026-11-04 (EST, UTC-5): 21:00Z..22:00Z is 16:00..17:00 NY (OPEN!), while 22:00Z..23:00Z is 17:00..18:00 NY (MAINTENANCE!)
    nov04_2045z = 1793825100000  # 2026-11-04T20:45:00Z (closes 21:00Z)
    nov04_2200z = 1793829600000  # 2026-11-04T22:00:00Z (closes 22:15Z -> gap 21:00..22:00Z is OPEN)
    nov04_2145z = 1793828700000  # 2026-11-04T21:45:00Z (closes 22:00Z)
    nov04_2300z = 1793833200000  # 2026-11-04T23:00:00Z (gap 22:00..23:00Z is MAINTENANCE)

    res_est_open = classify_historical_dataset_gaps(
        {"15m": [_make_bar(nov04_2045z, 900_000), _make_bar(nov04_2200z, 900_000)]},
        session_calendar=maint_cal,
    )
    assert res_est_open["timeframes"]["M15"]["all_gaps"][0]["category"] == "PROVIDER_OR_DATA_GAP"

    res_est_maint = classify_historical_dataset_gaps(
        {"15m": [_make_bar(nov04_2145z, 900_000), _make_bar(nov04_2300z, 900_000)]},
        session_calendar=maint_cal,
    )
    assert res_est_maint["timeframes"]["M15"]["all_gaps"][0]["category"] == "VERIFIED_MAINTENANCE_PAUSE"


def test_sec1_unknown_calendar_empty_duplicates_out_of_order_and_invalid_timestamps() -> None:
    """Empty datasets, identical/conflicting duplicates, out-of-order, unclosed, and invalid timestamps fail-safe without mutation."""
    empty_out = classify_historical_dataset_gaps({})
    assert empty_out["timeframes"]["M1"]["actual_bars"] == 0

    t0 = 1792000020000  # aligned to 60_000 ms (1792000020000 % 60000 == 0)
    b0 = _make_bar(t0, 60_000, px=4000.0)
    b1 = _make_bar(t0 + 60_000, 60_000, px=4001.0)
    b1_ident_dup = _make_bar(t0 + 60_000, 60_000, px=4001.0)
    b1_confl_dup = _make_bar(t0 + 60_000, 60_000, px=4010.0)
    b3 = _make_bar(t0 + 180_000, 60_000, px=4002.0)  # skips t0+120_000 (1 missing bar)
    b_ooo = _make_bar(t0 + 120_000, 60_000, px=4001.5)  # out-of-order after b3!
    b_unclosed = _make_bar(t0 + 240_000, 60_000, px=4003.0, is_closed=False)
    bad_bars = [
        {"open_time": None, "close_time": None},
        {"open_time": True, "close_time": False},
        {"open_time": -60000, "close_time": 0},
        {"open_time": 1792000000, "close_time": 1792000060},  # seconds scale instead of ms!
        {"open_time": t0 + 300_000, "close_time": t0 + 300_000},  # zero span!
    ]
    raw_list = [b0, b1, b1_ident_dup, b1_confl_dup, b3, b_ooo, b_unclosed, *bad_bars]
    snapshot_before = copy.deepcopy(raw_list)

    rep = classify_historical_dataset_gaps({"1m": raw_list})
    assert raw_list == snapshot_before, "Input bar sequence must remain 100% immutable"
    m1_rep = rep["timeframes"]["M1"]
    assert m1_rep["invalid_timestamp_count"] == 5
    assert m1_rep["unclosed_bar_count"] == 1
    assert m1_rep["duplicate_count"] == 2
    assert m1_rep["identical_duplicate_count"] == 1
    assert m1_rep["conflicting_duplicate_count"] == 1
    assert m1_rep["out_of_order_count"] == 1
    # Because b_ooo supplied t0+120_000, sorted deduplicated closed bars cover t0, t0+60k, t0+120k, t0+180k continuously!
    assert m1_rep["valid_unique_closed_bars"] == 4
    assert m1_rep["total_missing_bars"] == 0


def test_sec1_canonical_dataset_all_6_timeframes_coherence_and_immutability() -> None:
    datasets, prov = load_canonical_xauusdt_datasets()
    assert prov["dataset_sha256"] == CANONICAL_DATASET_SHA256_KH01A
    rep = classify_historical_dataset_gaps(datasets)
    assert rep["synthetic_fill_applied"] is False
    assert rep["interpolation_applied"] is False
    assert rep["forward_fill_applied"] is False
    assert rep["session_calendar_verification_status"] == "UNKNOWN_UNVERIFIED"

    tfs = rep["timeframes"]
    assert tfs["M1"]["actual_bars"] == 22376 and tfs["M1"]["total_missing_bars"] == 6926
    assert tfs["M5"]["actual_bars"] == 31609 and tfs["M5"]["total_missing_bars"] == 923
    assert tfs["M15"]["actual_bars"] == 15090 and tfs["M15"]["total_missing_bars"] == 362
    assert tfs["H1"]["actual_bars"] == 3863 and tfs["H1"]["total_missing_bars"] == 0
    assert tfs["H4"]["actual_bars"] == 965 and tfs["H4"]["total_missing_bars"] == 0
    assert tfs["D1"]["actual_bars"] == 181 and tfs["D1"]["total_missing_bars"] == 0

    for lbl in ("M1", "M5", "M15", "H1", "H4", "D1"):
        assert tfs[lbl]["missing_bars_by_category"].get("WEEKEND_MARKET_CLOSED", 0) == 0


# ==============================================================================
# SECTION 2: VERIFICATION FAIL-CLOSED & ADVERSARIAL FAULT TESTS
# ==============================================================================

def test_sec2_metrics_and_gate_matrix_fail_closed_faults() -> None:
    with pytest.raises(TypedMetricValidationError, match="MISSING_REQUIRED_JUNIT_COUNTER:errors"):
        parse_required_pytest_counts_v17931219({"collected": 5, "executed": 5, "passed": 5, "skipped": 0, "failed": 0})

    with pytest.raises(TypedMetricValidationError, match="FLOAT_AS_INT_REJECTED"):
        parse_required_pytest_counts_v17931219({"collected": 5.0, "executed": 5, "passed": 5, "skipped": 0, "failed": 0, "errors": 0})


def test_sec2_receipt_and_junit_adversarial_faults(tmp_path: Path) -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    # Non-hex content_sha256 rejected
    bad_hex = EvidenceReceiptV17931219(
        gate_id="G03", status="PASS", receipt_path="x.json", content_sha256="z" * 64,
        tested_sha=sha, run_id="R1", validator_name="v", validator_code_sha="a" * 40,
        input_sha256="b" * 64, observed_status="PASS",
    )
    assert not bad_hex.is_valid_pass(expected_run_id="R1", expected_tested_sha=sha)

    # Short input_sha256 rejected
    short_in = EvidenceReceiptV17931219(
        gate_id="G03", status="PASS", receipt_path="x.json", content_sha256="a" * 64,
        tested_sha=sha, run_id="R1", validator_name="v", validator_code_sha="a" * 40,
        input_sha256="123", observed_status="PASS",
    )
    assert not short_in.is_valid_pass(expected_run_id="R1", expected_tested_sha=sha)

    # Missing scheduled_node_ids rejected when require_shard_schedule=True
    junit_dir = tmp_path / "junit"
    junit_dir.mkdir()
    xml_p = junit_dir / "s0.xml"
    xml_bytes = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<testsuite name="pytest" tests="1" errors="0" failures="0" skipped="0">'
        b'<testcase classname="backend.tests.test_kh_01a_x" name="test_a" time="0.01"/>'
        b"</testsuite>"
    )
    xml_sha = write_bytes_durable_atomic(xml_p, xml_bytes)
    nid = "backend/tests/test_kh_01a_x.py::test_a"
    col = {"run_id": "R1", "tested_executable_sha": sha, "collect_exit_code": 0, "collected_node_ids": [nid]}
    sh_no_sched = {
        "run_id": "R1",
        "tested_executable_sha": sha,
        "shards": [{
            "shard_name": "s0", "run_id": "R1", "tested_executable_sha": sha,
            "reused": False, "pid": 111, "exit_code": 0, "timed_out": False,
            "junit_xml_path": "junit/s0.xml", "xml_sha256": xml_sha, "xml_mtime_ns": xml_p.stat().st_mtime_ns,
        }],
    }
    res = verify_fresh_junit_bijection_v17931211(
        tmp_path, col, sh_no_sched, expected_run_id="R1", expected_tested_sha=sha, require_shard_schedule=True
    )
    assert res["status"] == "FAIL"
    assert any("SHARD_MISSING_SCHEDULED_NODE_IDS" in v for v in res["violations"])

    # Mandatory KH-01A test skipped must fail closed
    skip_xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<testsuite name="pytest" tests="1" errors="0" failures="0" skipped="1">'
        b'<testcase classname="backend.tests.test_kh_01a_x" name="test_a" time="0.01"><skipped message="skip"/></testcase>'
        b"</testsuite>"
    )
    skip_p = junit_dir / "s_skip.xml"
    skip_sha = write_bytes_durable_atomic(skip_p, skip_xml)
    sh_skip = copy.deepcopy(sh_no_sched)
    sh_skip["shards"][0].update(
        scheduled_node_ids=[nid], junit_xml_path="junit/s_skip.xml", xml_sha256=skip_sha, xml_mtime_ns=skip_p.stat().st_mtime_ns
    )
    res_skip = verify_fresh_junit_bijection_v17931211(
        tmp_path, col, sh_skip, expected_run_id="R1", expected_tested_sha=sha, require_shard_schedule=True
    )
    assert res_skip["status"] == "FAIL"
    assert any("MANDATORY_TESTS_SKIPPED" in v for v in res_skip["violations"])


def test_sec2_durable_io_enospc_and_safe_stream_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenStream:
        def write(self, data: str) -> int:
            raise OSError(errno.ENOSPC, "No space left on device")
        def flush(self) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

    sw = _SafeStreamWrapper(_BrokenStream())
    with pytest.raises(OSError) as exc_info:
        sw.write("must not swallow")
    assert exc_info.value.errno == errno.ENOSPC
    assert sw.write_errors_count == 1

    # Durable JSON/GZIP atomic readback happy path
    j_path = tmp_path / "ok.json"
    j_sha = write_json_durable_atomic(j_path, {"a": 1})
    assert len(j_sha) == 64 and j_path.is_file()

    gz_path = tmp_path / "ok.jsonl.gz"
    gz_sha = write_jsonl_gz_durable_atomic(gz_path, [{"row": 1}, {"row": 2}])
    assert len(gz_sha) == 64 and gz_path.is_file()

    # Simulated fsync ENOSPC cleans up .partial and leaves no corrupt target
    bad_target = tmp_path / "should_not_exist.json"

    def _boom_fsync(_fd: int) -> None:
        raise OSError(errno.ENOSPC, "Simulated fsync ENOSPC")

    monkeypatch.setattr("os.fsync", _boom_fsync)
    with pytest.raises(DurableArtifactWriteError, match="Simulated fsync ENOSPC"):
        write_bytes_durable_atomic(bad_target, b'{"x": 1}\n')
    assert not bad_target.exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_sec2_full_real_fault_injection_suite_kh01a() -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    suite = run_kh_01a_real_fault_injections(tested_sha=sha, run_id="PYTEST_KH01A_FAULTS")
    assert suite["status"] == "PASS"
    assert suite["report_assertions_counted_as_faults"] == 0
    assert suite["failed_fault_injections"] == 0
    assert suite["total_real_fault_injections"] >= 22
    assert suite["synthetic_nonzero_d4_positive_fixture_verification"]["passed"] is True


# ==============================================================================
# SECTION 3: CAUSAL LOSS FORENSICS TESTS
# ==============================================================================

def test_sec3_causal_loss_forensics_no_hindsight_and_unknown_honesty() -> None:
    # Structural signature anti-hindsight proof
    sig = inspect.signature(classify_pre_entry_state_strictly_asof)
    forbidden = {"outcome_label", "exit_type", "exit_fill_price", "realized_net_pnl_usd", "forward_bars", "mae", "mfe"}
    assert forbidden.isdisjoint(set(sig.parameters.keys()))

    # Bare rows without raw MarketDecisionContext -> PRE_ENTRY_UNKNOWN (never fabricated!)
    bare_attr = build_loss_episode_attribution_from_raw_v17931219(
        si0_loss_rows=[{
            "opportunity_id": "OPP_BARE_1",
            "direction": "SHORT",
            "decision_timestamp_ms": 1792000000000,
            "entry_fill_price": 4000.0,
        }],
        si8_loss_rows=[{"opportunity_id": "S8_A"}, {"opportunity_id": "SHARED_X"}],
        si9_loss_rows=[{"opportunity_id": "SHARED_X"}, {"opportunity_id": "S9_B"}],
    )
    assert bare_attr["observed_pre_entry_known_distribution"] == {"PRE_ENTRY_UNKNOWN": 1}
    assert bare_attr["missing_raw_snapshot_census_count"] == 1
    assert bare_attr["si8_si9_research_losses"]["shared_trade_ids_count"] == 1
    assert bare_attr["si8_si9_research_losses"]["combined_unique_closed_count"] == 3

    # Real point-in-time join on canonical dataset bars
    datasets, _ = load_canonical_xauusdt_datasets()
    close_ts = build_close_ts_index_v179312(datasets)
    dec_ts = int(datasets["1m"][500]["close_time"])
    px = float(datasets["1m"][500]["close"])
    ep_loss = {
        "opportunity_id": "OPP_REAL_L1",
        "episode_key": "EP_REAL_L1",
        "model_id": "SMC_M1_CONTINUATION",
        "direction": "LONG",
        "decision_timestamp_ms": dec_ts,
        "entry_reference": px,
        "entry_fill_price": round(px + 0.10, 2),
        "selected_sl": round(px - 4.0, 2),
        "take_profit": round(px + 10.0, 2),
        "entry_fill_ts": dec_ts,
        "exit_fill_ts": dec_ts + 300_000,
        "exit_fill_price": round(px - 4.0, 2),
        "bars_held": 5,
        "outcome_label": "LOSS",
        "realized_net_pnl_usd": -2.45,
        "realized_r": -0.98,
    }
    ep_win = {
        **ep_loss,
        "opportunity_id": "OPP_REAL_W1",
        "episode_key": "EP_REAL_W1",
        "outcome_label": "WIN",
        "exit_fill_price": round(px + 10.0, 2),
        "realized_net_pnl_usd": 5.10,
        "realized_r": 2.04,
    }
    joined = build_kh_01a_causal_loss_and_win_control_forensics(
        si0_all_primary_episodes=[ep_loss, ep_win],
        si8_loss_rows=[],
        si9_loss_rows=[],
        datasets=datasets,
        close_ts_index=close_ts,
    )
    l_row = joined["loss_casebook_157"][0]
    w_row = joined["win_casebook_9"][0]
    # Because both ep_loss and ep_win share identical decision_ts, model_id, direction, and geometry,
    # their causal pre-entry classification MUST be 100% identical (proving zero hindsight from outcome!)
    assert l_row["pre_entry_known"]["reason_code"] == w_row["pre_entry_known"]["reason_code"]
    assert l_row["pre_entry_known"]["raw_snapshot_joined"] is True
    assert l_row["ex_post_outcome"]["outcome_code"] == "SL_HIT_BEFORE_TP"
    assert w_row["ex_post_outcome"]["outcome_code"] == "TP_HIT_BEFORE_SL"
    for fk, fv in l_row["pre_entry_features"].items():
        if fv["known_at_ms"] is not None:
            assert fv["known_at_ms"] <= dec_ts, f"Lookahead detected in {fk}"


# ==============================================================================
# SECTION 4: TARGET / ATR DATA AUTHORITY AUDIT TESTS
# ==============================================================================

def test_sec4_target_267_audit_spread_and_atr_unit_guards() -> None:
    datasets, _ = load_canonical_xauusdt_datasets()
    close_ts = build_close_ts_index_v179312(datasets)
    dec_ts = int(datasets["1m"][1000]["close_time"])
    px = float(datasets["1m"][1000]["close"])

    atr_1m = compute_causal_tf_atr_v17931214(datasets, close_ts, "1M", dec_ts)
    atr_1h = compute_causal_tf_atr_v17931214(datasets, close_ts, "1H", dec_ts)
    assert atr_1m is not None and atr_1h is not None
    assert atr_1h > atr_1m

    # Target at > 12 M1 ATRs (within <= 12 H1 ATRs) with cost-adjusted Net RR >= 2.0
    sl_dist = max(4.0 * atr_1m, 4.0)
    tp_dist = max(16.0 * atr_1m, 3.0 * sl_dist + 8.0)
    if tp_dist > 11.5 * atr_1h:
        tp_dist = 11.0 * atr_1h
        sl_dist = max(2.0, (tp_dist - 8.0) / 3.2)
    tp_px = round(px + tp_dist, 2)
    sl_px = round(px - sl_dist, 2)
    pools = {
        "pool_1H_test": {
            "pool_id": "pool_1H_test",
            "timeframe": "1H",
            "price": tp_px,
            "known_at_ms": dec_ts - 3_600_000,
            "swept_before_decision": False,
        }
    }
    dec_row = {"decision_timestamp_ms": dec_ts, "direction": "LONG", "entry_reference": px, "selected_sl": sl_px, "take_profit": tp_px}
    feat_row = {"target_pool_id": "pool_1H_test", "target_pool_timeframe": "1H", "target_unswept": True}

    ok_aud = audit_target_reachability_row_v17931214(
        opportunity_id="OPP_T1",
        cohort_role="HISTORICAL_267_SI9_H5",
        historical_rejection="SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN",
        current_rejection="SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN",
        decision_row=dec_row,
        feature_row=feat_row,
        pools_by_id=pools,
        datasets=datasets,
        close_ts_index=close_ts,
    )
    assert ok_aud["resolved_source_tf"] == "1H"
    assert ok_aud["m1_atr_substituted_for_htf"] is False
    assert ok_aud["executable_tp_choice_preserved"] is True
    assert ok_aud["first_decisive_failure_code"] == "M1_VS_HTF_ATR_UNIT_MISMATCH_ON_VALID_HTF_POOL"

    # Zero spread assumption must be rejected fail-closed!
    zero_spread_aud = audit_target_reachability_row_v17931214(
        opportunity_id="OPP_T2",
        cohort_role="HISTORICAL_267_SI9_H5",
        historical_rejection="SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN",
        current_rejection="SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN",
        decision_row={**dec_row, "spread": 0.0},
        feature_row=feat_row,
        pools_by_id=pools,
        datasets=datasets,
        close_ts_index=close_ts,
    )
    assert zero_spread_aud["tri_state_status"] == "INVALID_INCONSISTENT"
    assert zero_spread_aud["first_decisive_failure_code"] == "MISSING_OR_ZERO_SPREAD_INVALID"

    # M1-for-HTF ATR substitution injection rejected!
    m1_sub_aud = audit_target_reachability_row_v17931214(
        opportunity_id="OPP_T3",
        cohort_role="HISTORICAL_267_SI9_H5",
        historical_rejection="SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN",
        current_rejection="SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN",
        decision_row=dec_row,
        feature_row={**feat_row, "_injected_m1_atr_substituted_for_htf": True},
        pools_by_id=pools,
        datasets=datasets,
        close_ts_index=close_ts,
    )
    assert m1_sub_aud["tri_state_status"] == "INVALID_INCONSISTENT"
    assert m1_sub_aud["first_decisive_failure_code"] == "ATR_UNDERFLOW_NONFINITE_OR_UNIT_MIX_INVALID"


# ==============================================================================
# SECTION 5 & 9: DATASET REGISTRY, FRESH OOS ESCROW & PARITY TESTS
# ==============================================================================

def test_sec5_dataset_registry_and_fresh_oos_escrow_fail_closed() -> None:
    datasets, _ = load_canonical_xauusdt_datasets()
    gap_rep = classify_historical_dataset_gaps(datasets)
    reg = build_dataset_registry_and_fresh_oos_audit_kh01a(REPO_ROOT, datasets, gap_rep)

    assert reg["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA"
    assert reg["untouched_post_freeze_datasets_found_count"] == 0
    assert reg["canonical_dataset_manifest"]["prior_use_status"] == "REUSED_RESEARCH"
    assert reg["canonical_dataset_manifest"]["permission_flags"]["allow_fresh_oos_claim"] is False
    assert reg["canonical_escrow_check"]["eligible_as_fresh_untouched_oos"] is False

    # Callgraph & Keep-As-Is matrix AST verification
    cg = build_kh_01a_callgraph_and_keep_as_is_matrix(REPO_ROOT)
    assert cg["spec_version"] == SPEC_VERSION_KH01A
    for row in cg["matrix"]:
        assert row["ast_verification"]["exists"] is True, f"Missing AST target for {row['function']}"
    kept_items = [r for r in cg["matrix"] if "KEPT AS IS" in r["action"]]
    assert len(kept_items) >= 6


def test_sec5_trade_action_fingerprint_and_adapter_parity() -> None:
    """Verify SharedStrategyEvaluator & 3 delayed-entry adapters preserve exact parity & OFF safety locks."""
    datasets, prov = load_canonical_xauusdt_datasets()
    bars_1m = list(datasets["1m"])
    bars_5m = list(datasets["5m"])
    bars_15m = list(datasets["15m"])
    bars_1h = list(datasets["1H"])
    bars_4h = list(datasets["4H"])
    bars_1d = list(datasets["1D"])
    close_ts = build_close_ts_index_v179312(datasets)

    ts_ms = int(bars_1m[1200]["close_time"])
    px = float(bars_1m[1200]["close"])
    i5 = bisect.bisect_right(close_ts["5m"], ts_ms)
    i15 = bisect.bisect_right(close_ts["15m"], ts_ms)
    ih1 = bisect.bisect_right(close_ts["1H"], ts_ms)
    ih4 = bisect.bisect_right(close_ts["4H"], ts_ms)

    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m[960:1201],
        m5_candles=bars_5m[max(0, i5 - 120) : i5],
        m15_candles=bars_15m[max(0, i15 - 120) : i15],
        h1_candles=bars_1h[max(0, ih1 - 120) : ih1],
        h4_candles=bars_4h[max(0, ih4 - 120) : ih4],
        d1_candles=bars_1d[:10],
        bid=round(px - 0.10, 2),
        ask=round(px + 0.10, 2),
        quote_timestamp_ms=ts_ms,
        quote_source_kind="SYNTHETIC_RESEARCH_QUOTE",
    )
    ev = SharedStrategyEvaluator()
    s1 = ev.evaluate_model_direction(context=ctx, model_id="SMC_M1_CONTINUATION", direction="SHORT")
    s2 = ev.evaluate_model_direction(context=ctx, model_id="SMC_M1_CONTINUATION", direction="SHORT")
    assert s1.decision_fingerprint == s2.decision_fingerprint
    assert s1.trade_action_fingerprint == s2.trade_action_fingerprint

    hashes = set()
    for mode_ctx in ("REPLAY_BACKTEST", "MARKET_SHADOW", "PRODUCTION_ORCHESTRATOR_DRY_RUN"):
        out = evaluate_shared_delayed_entry_adapter_v17931213(
            execution_context_mode=mode_ctx,
            activation_flags={"LIVE": False, "PAPER": False, "AUTO_LIVE": False, "AUTO_PAPER": False, "enabled_in_production": False},
            variant_id="D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
            opportunity_id="PARITY_TEST_1",
            episode_key="ep_parity_1",
            model_id="SMC_M1_CONTINUATION",
            direction="SHORT",
            decision_timestamp_ms=ts_ms,
            baseline_entry=px,
            baseline_sl=round(px + 4.0, 2),
            baseline_tp=round(px - 10.0, 2),
            closed_m1=bars_1m[1021:1201],
            closed_m5=bars_5m[max(0, i5 - 60) : i5],
            closed_m15=bars_15m[max(0, i15 - 60) : i15],
            closed_h1=bars_1h[max(0, ih1 - 60) : ih1],
            closed_h4=bars_4h[max(0, ih4 - 60) : ih4],
            forward_bars=bars_1m[1201:1381],
            pre_entry_features={"require_true_unicorn": True},
            fill_mode="NEXT_BAR_OPEN_BID_ASK",
            dataset_sha=prov["dataset_sha256"],
        )
        hashes.add(canonical_hash({k: v for k, v in out.items() if k != "adapter_execution_context_mode"}))
    assert len(hashes) == 1


def test_sec7_sentinel_temp_cleanup_and_independent_disk_verifier_fail_closed(tmp_path: Path) -> None:
    """Verify run-owned temp sentinel containment guard and independent disk verifier fail-closed rejection."""
    import os
    import tempfile

    from tools.kh_01a_independent_verifier import verify_kh_01a_run_dir_from_disk
    from tools.run_kh_01a_verification import (
        OWNED_TEMP_SENTINEL_NAME,
        cleanup_owned_temp_root,
        create_owned_temp_root,
    )

    run_id = f"unit_test_kh01a_{os.getpid()}"
    owned = create_owned_temp_root(run_id)
    assert owned.is_dir()
    assert (owned / OWNED_TEMP_SENTINEL_NAME).is_file()

    # Refuses deletion when expected_run_id mismatches sentinel
    with pytest.raises(RuntimeError, match="REFUSING_TEMP_DELETE_SENTINEL_RUN_MISMATCH"):
        cleanup_owned_temp_root(owned, "wrong_run_id")

    # Refuses deletion of arbitrary directory inside tmp_path if outside system temp or missing sentinel
    unowned = Path(tempfile.gettempdir()) / f"aurum_unowned_no_sentinel_{os.getpid()}"
    unowned.mkdir(parents=True, exist_ok=True)
    try:
        with pytest.raises(RuntimeError, match="REFUSING_TEMP_DELETE_MISSING_SENTINEL"):
            cleanup_owned_temp_root(unowned, run_id)
    finally:
        unowned.rmdir()

    # Valid sentinel cleanup succeeds
    clean_res = cleanup_owned_temp_root(owned, run_id)
    assert clean_res["cleaned"] is True
    assert clean_res["sentinel_verified"] is True
    assert not owned.exists()

    # Independent disk verifier fails closed on incomplete run directory
    empty_run = tmp_path / "incomplete_run"
    empty_run.mkdir(parents=True, exist_ok=True)
    ver_res = verify_kh_01a_run_dir_from_disk(empty_run, check_git_source_freeze=False)
    assert ver_res["independent_verifier_status"] == "FAIL"
    assert any("MISSING_REQUIRED_ARTIFACT" in v for v in ver_res["violations"])

