"""
backend/tests/test_kh_01b_r1_data_safety_closure.py

Comprehensive RED/GREEN unit, integration, and separate-OS-process adversarial tamper tests
for KH-01B.R1.DSC: Data & Safety Truth Closure (§4 D1-D8, §5 F1-F4, §6 R1-R5, §8 G-DS-01..G-DS-15).
Runs purely in memory / bounded pytest `tmp_path` with zero live network calls and zero
modifications to tracked repository datasets or prior immutable artifacts.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import pytest

from domain_calculator import CostAssumptions
from services.kh_01a_r1_forensics_closure_service import build_dynamic_validated_gate_receipt_r1
from services.kh_01b_r1_data_safety_closure_service import (
    CANONICAL_DATASET_SHA256_KH01B_R1,
    MANDATORY_BRANCH_KH01B_R1,
    PRIOR_DSC_V1_BLOCKED_RUN_ID,
    PRIOR_DSC_V1_PUBLICATION_HEAD_SHA,
    PRIOR_DSC_V1_TESTED_SHA,
    SCHEMA_VERSION_KH01B_R1_DSC,
    SCHEMA_VERSION_KH01B_R1_DSC_R2,
    SPEC_VERSION_KH01B_R1_DSC,
    SPEC_VERSION_KH01B_R1_DSC_R2,
    _create_reparse_link_for_test,
    _remove_reparse_link_for_test,
    audit_gap_schema_coverage_and_provenance_from_disk,
    compute_kh01b_r1_fee_sensitivity,
    evaluate_kh01b_r1_dsc_gates_from_disk,
    evaluate_kh01b_r1_dsc_r2_gates_from_disk,
    render_kh01b_r1_dsc_r2_readme_and_handover_vi,
    render_kh01b_r1_dsc_readme_and_handover_vi,
    run_kh01b_r1_dsc_red_green_fault_suite,
    validate_latest_run_pointer_not_blocked,
)
from services.v17_9_1_decision_funnel import classify_historical_dataset_gaps
from tools.fresh_junit_io import write_json_durable_atomic, write_text_durable_atomic
from tools.kh_01b_r1_collect_market_data import (
    BITGET_D1_UTC_OFFSET_MS,
    CANONICAL_SAMPLE_END_MS,
    DEFAULT_DEV_EXTENDED_END_MS,
    DEFAULT_DEV_EXTENDED_START_MS,
    GRANULARITY_TO_GAP_AUDIT_LABEL,
    build_and_validate_future_oos_escrow_registry,
    collect_online_bitget_audit_and_extended_dev,
    compare_boundary_candles_exactly,
    compute_required_page_budget,
    derive_window_coverage_by_timeframe,
    map_gap_truth_to_acquisition_summary,
    run_offline_collector_self_audit,
    validate_and_normalize_paginated_candles,
    validate_bar_open_alignment_for_granularity,
    validate_prospective_bbo_snapshot,
    verify_http_page_cursor_progress,
)
from tools.run_kh_01a_verification import (
    OWNED_TEMP_SENTINEL_NAME,
    V130_RUN,
    create_owned_temp_root,
    iter_jsonl_gz,
)
from tools.run_kh_01b_r1_data_safety_closure import reclaim_inactive_sentinel_owned_temp_dirs
from tools.run_kh_01b_r1_verification import (
    compute_kh01b_r1_disk_reserve_policy,
    inspect_writing_volumes_and_mounts_r2,
    safe_cleanup_owned_temp_root_r1,
    verify_kh01b_r1_resource_cleanup,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFIER_DSC = REPO_ROOT / "aurum-desk/tools/kh_01b_r1_data_safety_independent_verifier.py"


def _pass_v() -> dict:
    return {"status": "PASS", "violations": []}


def test_dsc_d1_gap_schema_mapping_and_red_null_rejection() -> None:
    ts = DEFAULT_DEV_EXTENDED_START_MS
    d1_open = 1_794_844_800_000  # 2026-11-16T16:00:00Z (Bitget UTC+8 1D boundary)
    bars_6tf = {
        "1m": [
            {"timestamp": ts, "open_time": ts, "close_time": ts + 60_000, "open": 3700.0, "high": 3701.0, "low": 3699.0, "close": 3700.5, "volume": 10.0, "is_closed": True},
            {"timestamp": ts + 180_000, "open_time": ts + 180_000, "close_time": ts + 240_000, "open": 3700.5, "high": 3701.5, "low": 3700.0, "close": 3701.0, "volume": 12.0, "is_closed": True},
        ],
        "5m": [{"timestamp": ts, "open_time": ts, "close_time": ts + 300_000, "open": 3700.0, "high": 3701.5, "low": 3699.0, "close": 3701.0, "volume": 25.0, "is_closed": True}],
        "15m": [{"timestamp": ts, "open_time": ts, "close_time": ts + 900_000, "open": 3700.0, "high": 3702.0, "low": 3699.0, "close": 3701.0, "volume": 50.0, "is_closed": True}],
        "1H": [{"timestamp": ts, "open_time": ts, "close_time": ts + 3_600_000, "open": 3700.0, "high": 3705.0, "low": 3698.0, "close": 3702.0, "volume": 200.0, "is_closed": True}],
        "4H": [{"timestamp": ts, "open_time": ts, "close_time": ts + 14_400_000, "open": 3700.0, "high": 3708.0, "low": 3695.0, "close": 3703.0, "volume": 800.0, "is_closed": True}],
        "1D": [{"timestamp": d1_open, "open_time": d1_open, "close_time": d1_open + 86_400_000, "open": 3700.0, "high": 3715.0, "low": 3690.0, "close": 3706.0, "volume": 5000.0, "is_closed": True}],
    }
    raw_gap = classify_historical_dataset_gaps(bars_6tf)
    mapped = map_gap_truth_to_acquisition_summary(
        raw_gap,
        per_tf_validator_counts={k: len(v) for k, v in bars_6tf.items()},
    )
    assert mapped["status"] == "PASS"
    assert set(mapped["mapped_by_granularity"].keys()) == set(GRANULARITY_TO_GAP_AUDIT_LABEL.keys())

    m1_entry = mapped["mapped_by_granularity"]["1m"]
    assert m1_entry["authoritative_gap_label"] == "M1"
    assert m1_entry["actual_bars"] == 2
    assert m1_entry["valid_unique_closed_bars"] == 2
    assert m1_entry["total_missing_bars"] == 2
    assert isinstance(m1_entry["gap_events_by_category"], dict)
    assert isinstance(m1_entry["missing_bars_by_category"], dict)
    assert "gap_class_counts" not in m1_entry

    # Empty gap categories are explicit `{}` dicts, never None
    h1_entry = mapped["mapped_by_granularity"]["1H"]
    assert h1_entry["gap_events_by_category"] == {}
    assert h1_entry["missing_bars_by_category"] == {}

    # RED controls: legacy `per_timeframe` null dict or missing timeframe MUST fail closed
    assert map_gap_truth_to_acquisition_summary({"per_timeframe": {"1m": {"actual_bars": None}}})["status"] == "FAIL_INTEGRITY"
    missing_d1 = {"schema_version": "v1", "session_calendar_verification_status": "UNKNOWN_UNVERIFIED", "timeframes": {k: v for k, v in raw_gap["timeframes"].items() if k != "D1"}}
    assert map_gap_truth_to_acquisition_summary(missing_d1)["status"] == "FAIL_INTEGRITY"


def test_dsc_d2_48h_m1_1600_tail_bars_marked_partial_and_dynamic_page_budget() -> None:
    w_s = DEFAULT_DEV_EXTENDED_START_MS
    w_e = DEFAULT_DEV_EXTENDED_END_MS
    budget = compute_required_page_budget(w_s, w_e, 60_000, page_limit=200)
    assert budget["estimated_slots_in_span"] == 2880
    assert budget["effective_page_budget"] == 17 > 8

    tail_start = w_e - 1600 * 60_000
    page_1600 = {
        "raw_http_sha256": "a" * 64,
        "server_time_ms": w_e + 60_000,
        "rows": [[str(tail_start + i * 60_000), "3700.10", "3700.90", "3699.80", "3700.40", "11.0"] for i in range(1600)],
    }
    res_unver = validate_and_normalize_paginated_candles(
        [page_1600], symbol="XAUUSDT", granularity="1m", duration_ms=60_000,
        requested_start_ms=w_s, requested_end_ms=w_e, calendar_verified=False,
    )
    assert res_unver["status"] == "PASS"
    assert res_unver["missing_bar_intervals_count"] == 0  # Contiguous internally!
    assert res_unver["expected_closed_slots"] == 2880
    assert res_unver["observed_unique_closed_slots"] == 1600
    assert res_unver["leading_missing_slots"] == 1280
    assert res_unver["internal_missing_slots"] == 0
    assert res_unver["trailing_missing_slots"] == 0
    assert res_unver["missing_slots"] == 1280
    assert res_unver["coverage_verdict"] == "PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION"

    cov_ver_cal = derive_window_coverage_by_timeframe(
        [tail_start + i * 60_000 for i in range(1600)],
        requested_start_ms=w_s, requested_end_ms=w_e,
        granularity="1m", duration_ms=60_000, calendar_verified=True,
    )
    assert cov_ver_cal["coverage_verdict"] == "VERIFIED_PARTIAL"


def test_dsc_d3_d4_exact_decimal_boundary_dedupe_precision_collision_and_pagination_guards(tmp_path: Path) -> None:
    ts = DEFAULT_DEV_EXTENDED_START_MS
    # Direct Decimal comparator checks
    sig_a = {"symbol": "XAUUSDT", "granularity": "1m", "open_ms": ts, "open_dec": Decimal("3700.10"), "high_dec": Decimal("3701.0"), "low_dec": Decimal("3699.5"), "close_dec": Decimal("3700.5"), "volume_dec": Decimal("10.00")}
    sig_b_exact = {**sig_a, "open_dec": Decimal("3700.1"), "volume_dec": Decimal("10")}
    sig_c_near = {**sig_a, "open_dec": Decimal("3700.10004")}
    assert compare_boundary_candles_exactly(sig_a, sig_b_exact) is True
    assert compare_boundary_candles_exactly(sig_a, sig_c_near) is False

    # Cross-page exact boundary overlap -> PASS_WITH_DEDUP_RECEIPT, keeps both origin SHAs
    p1 = {"raw_http_sha256": "1" * 64, "server_time_ms": ts + 600_000, "rows": [[str(ts), "3700.10", "3701.00", "3699.50", "3700.50", "10.0"], [str(ts + 60_000), "3700.50", "3701.50", "3700.00", "3701.20", "15.5"]]}
    p2 = {"raw_http_sha256": "2" * 64, "server_time_ms": ts + 600_000, "rows": [[str(ts + 60_000), "3700.5", "3701.5", "3700.0", "3701.2", "15.50"], [str(ts + 120_000), "3701.20", "3702.00", "3700.80", "3701.70", "18.0"]]}
    dedup = validate_and_normalize_paginated_candles([p1, p2], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    assert dedup["status"] == "PASS"
    assert dedup["dedup_verdict"] == "PASS_WITH_DEDUP_RECEIPT"
    assert dedup["accepted_closed_bars_count"] == 3
    assert dedup["exact_boundary_duplicates_count"] == 1
    assert dedup["conflicting_duplicates_count"] == 0
    assert dedup["bars"][1]["origin_page_http_sha256s"] == ["1" * 64, "2" * 64]

    # Precision-colliding near-equal (equal at 2dp: 3700.504 vs 3700.501) -> FAIL!
    p2_near = {"raw_http_sha256": "3" * 64, "server_time_ms": ts + 600_000, "rows": [[str(ts + 60_000), "3700.504", "3701.50", "3700.00", "3701.20", "15.5"], [str(ts + 120_000), "3701.20", "3702.00", "3700.80", "3701.70", "18.0"]]}
    near_res = validate_and_normalize_paginated_candles([p1, p2_near], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    assert near_res["status"] == "FAIL"
    assert near_res["conflicting_duplicates_count"] == 1

    # Within-page duplicate -> FAIL
    p_within = {"raw_http_sha256": "4" * 64, "server_time_ms": ts + 600_000, "rows": [p1["rows"][0], p1["rows"][0]]}
    assert validate_and_normalize_paginated_candles([p_within], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)["status"] == "FAIL"

    # Cursor progress helper rejects stalled cursor and repeated page SHA
    prog_bad = verify_http_page_cursor_progress(prev_cursor_end_ms=ts + 120_000, next_cursor_end_ms=ts + 180_000, prev_earliest_open_ms=ts + 60_000, curr_earliest_open_ms=ts + 60_000, raw_http_sha256="1" * 64, seen_page_shas={"1" * 64})
    assert prog_bad["progressed_strictly_backward"] is False

    # Simulated online collector with repeat-cursor loop halts cleanly with VERIFIED_PARTIAL (never hangs!)
    class _MockResp:
        def __init__(self, code: int, payload: dict) -> None:
            self.status_code = code
            self.content = json.dumps(payload, sort_keys=True).encode("utf-8")

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP_{self.status_code}")

    class _MockPartialLoopClient:
        def __init__(self) -> None:
            self.candle_calls = 0

        def get(self, url: str, params: dict | None = None) -> _MockResp:
            import time as _t
            p = params or {}
            now_m = int(_t.time() * 1000)
            if "contracts" in url:
                return _MockResp(200, {"code": "00000", "data": [{"symbol": "XAUUSDT", "symbolStatus": "normal", "makerFeeRate": "0.0002", "takerFeeRate": "0.0006", "minTradeNum": "0.01", "pricePlace": "2", "sizeMultiplier": "0.01"}]})
            if "ticker" in url:
                return _MockResp(200, {"code": "00000", "requestTime": now_m, "data": [{"ts": str(now_m - 25), "bidPr": "3700.10", "askPr": "3700.25", "bidSz": "5.0", "askSz": "4.0"}]})
            self.candle_calls += 1
            gran = p.get("granularity", "1m")
            if gran == "1D":
                d1_o = 1_794_844_800_000
                if int(p.get("endTime", 0)) <= d1_o:
                    return _MockResp(200, {"code": "00000", "requestTime": DEFAULT_DEV_EXTENDED_END_MS + 100_000, "data": []})
                return _MockResp(200, {"code": "00000", "requestTime": DEFAULT_DEV_EXTENDED_END_MS + 100_000, "data": [[str(d1_o), "3700.0", "3710.0", "3690.0", "3705.0", "5000.0"]]})
            # Return same single tail bar repeatedly on subsequent pages -> cursor guard must halt immediately!
            dur = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000}[gran]
            bar_o = DEFAULT_DEV_EXTENDED_END_MS - dur
            return _MockResp(200, {"code": "00000", "requestTime": DEFAULT_DEV_EXTENDED_END_MS + 100_000, "data": [[str(bar_o), "3700.0", "3701.0", "3699.0", "3700.5", "20.0"]]})

        def close(self) -> None:
            pass

    mock_cli = _MockPartialLoopClient()
    col_out = tmp_path / "sim_col"
    sim_res = collect_online_bitget_audit_and_extended_dev(
        col_out,
        freeze_ts_ms=DEFAULT_DEV_EXTENDED_END_MS + 100_000,
        http_client=mock_cli,
        volume_free_override={"repo": 35 * 1024**3, "output": 35 * 1024**3, "temp": 35 * 1024**3},
        sleep_fn=lambda _s: None,
    )
    assert sim_res["DATA_ACQUISITION_VERDICT"] == "VERIFIED_PARTIAL"
    assert sim_res["gap_mapping_verification"]["status"] == "PASS"
    assert sim_res["per_timeframe_validation"]["1m"]["coverage_verdict"] == "PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION"
    assert mock_cli.candle_calls <= 15  # Halted immediately on stalled cursor (2 calls per TF)!


def test_dsc_d5_d6_d7_d8_timeframe_alignment_provenance_bbo_escrow_and_collector_disk_gate(tmp_path: Path) -> None:
    ts = DEFAULT_DEV_EXTENDED_START_MS
    assert validate_bar_open_alignment_for_granularity(ts + 60_000, "5m", 300_000)[0] is False
    assert validate_bar_open_alignment_for_granularity(ts, "15m", 900_000)[0] is True
    assert validate_bar_open_alignment_for_granularity(ts, "1D", 86_400_000, d1_utc_offset_ms=BITGET_D1_UTC_OFFSET_MS)[0] is False
    assert validate_bar_open_alignment_for_granularity(1_794_844_800_000, "1D", 86_400_000, d1_utc_offset_ms=BITGET_D1_UTC_OFFSET_MS)[0] is True

    # Offline self-audit makes zero network calls
    off = run_offline_collector_self_audit()
    assert off["network_requests_made"] == 0
    assert off["offline_48h_1600_partial_guard_verified"] is True
    assert off["offline_6tf_gap_mapping_verified"] is True

    # Collector preflight disk check blocks before making any HTTP call when < 20.5 GiB
    low_disk_col = collect_online_bitget_audit_and_extended_dev(
        tmp_path / "blocked_col",
        freeze_ts_ms=DEFAULT_DEV_EXTENDED_END_MS + 100_000,
        volume_free_override={"repo": int(13.4667 * 1024**3), "output": int(13.4667 * 1024**3), "temp": int(13.4667 * 1024**3)},
    )
    assert low_disk_col["DATA_ACQUISITION_VERDICT"] == "BLOCKED_LOW_DISK"
    assert low_disk_col["network_requests_made"] == 0

    # BBO & Escrow guards
    bad_bbo = validate_prospective_bbo_snapshot({
        "symbol": "XAUUSDT", "raw_http_sha256": "a" * 64, "receive_ts_ms": ts, "exchange_ts_ms": ts,
        "bid": 3700.1, "ask": 3700.2, "bid_size": 1.0, "ask_size": 1.0, "claims_continuous_historical_series": True,
    })
    assert bad_bbo["status"] == "FAIL"

    bad_esc = build_and_validate_future_oos_escrow_registry(
        freeze_timestamp_ms=ts, frozen_candidate_code_sha="s", frozen_config_sha256="c",
        proposed_oos_entries=[{
            "dataset_id": "one_bbo", "dataset_sha256": "9" * 64,
            "earliest_bar_open_ms": CANONICAL_SAMPLE_END_MS + 900_000_000,
            "latest_bar_close_ms": CANONICAL_SAMPLE_END_MS + 900_600_000,
            "strategy_evaluation_count": 0, "partition_role": "FRESH_UNTOUCHED", "source_kind": "PROSPECTIVE_POINT_SNAPSHOT",
        }],
    )
    assert bad_esc["status"] == "FAIL"


def test_dsc_f1_f2_f3_f4_decimal_fee_sensitivity_and_negative_controls() -> None:
    nb_rows = list(iter_jsonl_gz(V130_RUN / "SI0_OFF_BASELINE_NEXT_BAR_OPEN_BID_ASK_trades.jsonl.gz"))
    sp2_rows = list(iter_jsonl_gz(V130_RUN / "SI0_OFF_BASELINE_STRESS_WIDER_SPREAD_SLIPPAGE_2X_trades.jsonl.gz"))

    res = compute_kh01b_r1_fee_sensitivity(nb_rows, sp2_rows)
    assert res["status"] == "PASS"
    assert res["actual_account_effective_fee_tier"] == "UNKNOWN_NOT_LOCALLY_VERIFIED"
    assert CostAssumptions().taker_fee_rate == 0.0004
    assert CostAssumptions().maker_fee_rate == 0.0002

    par = res["original_e00_b00_reference_parity"]
    assert par["scheduled_count"] == 242
    assert par["closed_count"] == 166
    assert par["gross_pnl_usd"] == -142.7836
    assert par["total_fees_usd"] == 203.1135
    assert par["net_pnl_usd"] == -345.8971
    assert par["per_trade_decimal_0004_fee_mismatches_count"] == 0

    fo_04 = res["FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS"]["0.0004"]
    fo_06 = res["FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS"]["0.0006"]
    assert fo_04["net_pnl_1x_usd"] == -345.8971
    assert fo_06["total_fees_1x_usd"] > fo_04["total_fees_1x_usd"]
    assert fo_06["net_pnl_1x_usd"] < fo_04["net_pnl_1x_usd"]
    assert fo_06["stress_2x_all_cost_net_pnl_usd"] < fo_06["net_pnl_1x_usd"]
    assert fo_06["stress_3x_all_cost_net_pnl_usd"] < fo_06["stress_2x_all_cost_net_pnl_usd"]

    rr_04 = res["FULL_RE_RISKED_REPLAY"]["0.0004"]
    rr_06 = res["FULL_RE_RISKED_REPLAY"]["0.0006"]
    assert rr_06["rejected_by_fee_aware_min_rr_or_risk_cap_count"] >= rr_04["rejected_by_fee_aware_min_rr_or_risk_cap_count"]
    assert rr_06["net_pnl_1x_usd"] < 0.0

    # Negative controls F4
    assert compute_kh01b_r1_fee_sensitivity(nb_rows[:5], claimed_actual_account_fee_tier="VIP2", require_exact_e00_baseline_parity=False)["status"] == "FAIL_PROVENANCE"
    assert compute_kh01b_r1_fee_sensitivity(nb_rows[:5], apply_both_entry_and_exit_legs=False, require_exact_e00_baseline_parity=False)["status"] == "FAIL_PROVENANCE"
    assert compute_kh01b_r1_fee_sensitivity(nb_rows[:5], scenario_label_map={"0.0006": "0.0004"}, require_exact_e00_baseline_parity=False)["status"] == "FAIL_PROVENANCE"
    broken_ep = [{**nb_rows[0], "executed_quantity": None}]
    assert compute_kh01b_r1_fee_sensitivity(broken_ep, require_exact_e00_baseline_parity=False)["status"] == "NOT_IDENTIFIABLE"


def test_dsc_r1_to_r5_strict_20gib_disk_policy_and_owned_temp_reclamation() -> None:
    assert compute_kh01b_r1_disk_reserve_policy(int(35.0 * 1024**3))["disk_safety_verdict"] == "PASS_STRICT_20GIB"
    for g in (20.49, 18.5, 17.1503, 13.4667, 8.0):
        p = compute_kh01b_r1_disk_reserve_policy(int(g * 1024**3))
        assert p["can_proceed"] is False
        assert p["disk_safety_verdict"] == "BLOCKED_LOW_DISK"
        assert p["lowered_reserve_without_approval"] is False

    mounts_info = inspect_writing_volumes_and_mounts_r2(repo_root=REPO_ROOT, verify_atomic_write=True)
    assert set(mounts_info["writing_volumes_free_bytes"].keys()) == {"repo", "output", "temp"}
    assert mounts_info["distinct_underlying_filesystems_count"] >= 1
    assert mounts_info["atomic_write_probes"]["output"]["atomic_write_rename_verified"] is True
    assert mounts_info["atomic_write_probes"]["temp"]["atomic_write_rename_verified"] is True

    # Dead-PID sentinel-owned temp dir reclaimed safely; active-PID & unowned dirs preserved!
    base_tmp = Path(tempfile.gettempdir()).resolve()
    dead_dir = base_tmp / f"ak1_dead_{os.getpid()}"
    dead_dir.mkdir(parents=True, exist_ok=True)
    write_json_durable_atomic(dead_dir / OWNED_TEMP_SENTINEL_NAME, {"run_id": "dead_test_run", "owner_pid": 999_999_991})
    (dead_dir / "payload.bin").write_bytes(b"x" * 128)

    live_dir = create_owned_temp_root(f"live_test_{os.getpid()}")
    unowned_dir = base_tmp / f"ak1_nosentinel_{os.getpid()}"
    unowned_dir.mkdir(parents=True, exist_ok=True)

    try:
        rcpt = reclaim_inactive_sentinel_owned_temp_dirs()
        assert not dead_dir.exists()
        assert live_dir.exists()
        assert unowned_dir.exists()
        assert rcpt["unowned_paths_deleted_count"] == 0
    finally:
        safe_cleanup_owned_temp_root_r1(live_dir, f"live_test_{os.getpid()}")
        shutil.rmtree(unowned_dir, ignore_errors=True)
        shutil.rmtree(dead_dir, ignore_errors=True)

    # Symlink / NTFS-junction root refused without deleting target
    sym_root = base_tmp / f"ak1_symroot_{os.getpid()}"
    target_keep = base_tmp / f"keep_target_{os.getpid()}"
    target_keep.mkdir(parents=True, exist_ok=True)
    (target_keep / "sentinel_payload.txt").write_text("KEEP_ME", encoding="utf-8")
    try:
        _create_reparse_link_for_test(target_keep, sym_root)
        with pytest.raises(RuntimeError, match="REFUSING_TEMP_DELETE_SYMLINK_ROOT"):
            safe_cleanup_owned_temp_root_r1(sym_root, "any")
        assert target_keep.exists()
        assert (target_keep / "sentinel_payload.txt").read_text("utf-8") == "KEEP_ME"
    finally:
        _remove_reparse_link_for_test(sym_root)
        shutil.rmtree(target_keep, ignore_errors=True)


def _build_valid_dsc_bundle(base_dir: Path, *, blocked_low_disk: bool = True) -> Path:
    run_id = "TEST_DSC_BLOCKED" if blocked_low_disk else "TEST_DSC_PASS"
    sha = "fedcba9876543210fedcba9876543210fedcba98"
    r = base_dir / ("dsc_blocked" if blocked_low_disk else "dsc_pass")
    (r / "receipts").mkdir(parents=True, exist_ok=True)

    free_gib = 13.4667 if blocked_low_disk else 35.0
    d_pol = compute_kh01b_r1_disk_reserve_policy(int(free_gib * 1024**3))

    gap_cov_prov = audit_gap_schema_coverage_and_provenance_from_disk(REPO_ROOT)
    nb_rows = list(iter_jsonl_gz(V130_RUN / "SI0_OFF_BASELINE_NEXT_BAR_OPEN_BID_ASK_trades.jsonl.gz"))
    sp2_rows = list(iter_jsonl_gz(V130_RUN / "SI0_OFF_BASELINE_STRESS_WIDER_SPREAD_SLIPPAGE_2X_trades.jsonl.gz"))
    f06 = compute_kh01b_r1_fee_sensitivity(nb_rows, sp2_rows)
    f12 = run_kh01b_r1_dsc_red_green_fault_suite(REPO_ROOT)

    f00 = {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC, "schema_version": SCHEMA_VERSION_KH01B_R1_DSC,
        "run_id": run_id, "branch": MANDATORY_BRANCH_KH01B_R1, "tested_executable_sha": sha,
        "orchestrator_pid": -777, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1,
        "disk_reserve_policy": d_pol,
        "source_tree_unchanged_during_run": True, "prior_artifacts_unchanged_during_run": True,
    }
    f01 = {"locked_files_modified_count": 0}
    f02 = gap_cov_prov["doc_02"]
    f03 = {
        "status": "PASS", "failed_controls_count": 0, "passed_controls_count": 9,
        "m1_48h_dynamic_page_budget": compute_required_page_budget(DEFAULT_DEV_EXTENDED_START_MS, DEFAULT_DEV_EXTENDED_END_MS, 60_000),
    }
    f04 = gap_cov_prov["doc_04"]
    f05 = gap_cov_prov["doc_05"]
    f07 = {
        "disk_reserve_policy": d_pol, "policy_injections_status": "PASS",
        "runtime_telemetry": {"max_children_observed": 2, "orphan_pids_at_cleanup_count": 0, "temp_dirs_cleaned_with_sentinel_check": True, "unowned_paths_deleted_count": 0},
    }

    if blocked_low_disk:
        f08 = {"execution_status": "NOT_RUN_BLOCKED_LOW_DISK"}
        f09 = {"execution_status": "NOT_RUN_BLOCKED_LOW_DISK", "focused_in_memory_unit_tests": {"status": "PASS", "executed_count": 20, "passed_count": 20, "failed_count": 0}}
        f10 = {"execution_status": "NOT_RUN_BLOCKED_LOW_DISK"}
    else:
        (r / "junit").mkdir(parents=True, exist_ok=True)
        nodes = [
            "backend/tests/test_kh_01b_r1_data_safety_closure.py::test_a",
            "backend/tests/test_kh_01b_entry_unicorn_research.py::test_b",
            "backend/tests/test_kh_01a_data_and_verification_foundation.py::test_c",
            "backend/tests/test_v17_9_3_1_2_7_causal_truth.py::test_d",
        ]
        tcs_xml = "".join(
            f'<testcase classname="{n.split("::")[0].replace("/", ".").removesuffix(".py")}" name="{n.split("::")[1]}" time="0.01"/>'
            for n in nodes
        )
        xml_p = r / "junit/SHARD_01.xml"
        xml_p.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite name="pytest" tests="4" errors="0" failures="0" skipped="0">{tcs_xml}</testsuite>', encoding="utf-8")
        xml_sha = hashlib.sha256(xml_p.read_bytes()).hexdigest()
        xml_mt = int(xml_p.stat().st_mtime_ns)
        f08 = {"execution_status": "EXECUTED", "pytest_subset_w1_w2_w4_repeat_identical": True, "r1_variant_slice_w1_w2_w4_repeat_identical": True, "measured_speedup_w4_vs_w1": 2.1}
        f09 = {
            "execution_status": "EXECUTED",
            "collection_manifest": {"run_id": run_id, "tested_executable_sha": sha, "collect_exit_code": 0, "collected_node_ids": nodes},
            "shard_manifest": {
                "run_id": run_id, "tested_executable_sha": sha, "run_start_epoch_ns": xml_mt - 1000, "nonzero_exit_shards_count": 0,
                "shards": [{"shard_id": "SHARD_01", "shard_name": "SHARD_01", "run_id": run_id, "tested_executable_sha": sha, "pid": 1234, "reused": False, "reused_from_prior_run": False, "exit_code": 0, "timed_out": False, "scheduled_node_ids": nodes, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": xml_sha, "xml_mtime_ns": xml_mt, "junit_sha256": xml_sha, "junit_mtime_ns": xml_mt}],
            },
            "independent_bijection_verification": {"status": "PASS", "counters": {"collected": 4, "executed": 4, "passed": 4, "skipped": 0, "failed": 0, "errors": 0}},
        }
        f10 = {"execution_status": "EXECUTED", "status": "PASS", "a1_evaluated_count": 56710, "a1_mismatches_count": 0, "three_adapter_episodes_denominator": 246, "three_adapter_mismatches_count": 0}

    esc = build_and_validate_future_oos_escrow_registry(freeze_timestamp_ms=DEFAULT_DEV_EXTENDED_START_MS, frozen_candidate_code_sha=sha, frozen_config_sha256="cfg")
    f11 = {"future_oos_escrow": esc, "ACTIVATION_READY": "NO", "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}}

    for fname, doc in (
        ("00_preflight_branch_head_dataset_disk_volumes.json", f00),
        ("01_immutable_baseline_and_keep_as_is_callgraph.json", f01),
        ("02_gap_schema_mapping_red_green_and_six_tf_completeness.json", f02),
        ("03_pagination_cursor_order_boundary_duplicates_negative_controls.json", f03),
        ("04_extended_dev_requested_vs_observed_window_coverage.json", f04),
        ("05_bitget_origin_fee_quote_calendar_provenance.json", f05),
        ("06_baseline_0p04_vs_public_0p06_fee_sensitivity_caveats.json", f06),
        ("07_strict_20gib_disk_policy_injections_and_resource_telemetry.json", f07),
        ("08_async_w1_w2_w4_repeat_determinism_and_speed.json", f08),
        ("09_full_regression_collected_executed_junit_exit_bijection.json", f09),
        ("10_backtest_original_parity_three_fill_and_adapter_fingerprints.json", f10),
        ("11_data_oos_partition_never_evaluated_verdict.json", f11),
        ("12_independent_verifier_fault_injections.json", f12),
    ):
        write_json_durable_atomic(r / fname, doc)

    target_map = {
        "G-DS-01": "00_preflight_branch_head_dataset_disk_volumes.json",
        "G-DS-02": "02_gap_schema_mapping_red_green_and_six_tf_completeness.json",
        "G-DS-03": "03_pagination_cursor_order_boundary_duplicates_negative_controls.json",
        "G-DS-04": "04_extended_dev_requested_vs_observed_window_coverage.json",
        "G-DS-05": "05_bitget_origin_fee_quote_calendar_provenance.json",
        "G-DS-06": "06_baseline_0p04_vs_public_0p06_fee_sensitivity_caveats.json",
        "G-DS-07": "07_strict_20gib_disk_policy_injections_and_resource_telemetry.json",
        "G-DS-08": "07_strict_20gib_disk_policy_injections_and_resource_telemetry.json",
        "G-DS-09": "09_full_regression_collected_executed_junit_exit_bijection.json",
        "G-DS-10": "10_backtest_original_parity_three_fill_and_adapter_fingerprints.json",
        "G-DS-11": "08_async_w1_w2_w4_repeat_determinism_and_speed.json",
        "G-DS-12": "12_independent_verifier_fault_injections.json",
        "G-DS-13": "11_data_oos_partition_never_evaluated_verdict.json",
        "G-DS-14": "00_preflight_branch_head_dataset_disk_volumes.json",
    }
    skip_rcpts = {"G-DS-07", "G-DS-09", "G-DS-10", "G-DS-11"} if blocked_low_disk else set()
    for gid, rel_p in target_map.items():
        if gid not in skip_rcpts:
            build_dynamic_validated_gate_receipt_r1(
                gate_id=gid, run_dir=r, run_id=run_id, tested_sha=sha,
                target_rel_path=rel_p, validator_fn=_pass_v, write_json_fn=write_json_durable_atomic,
            )

    f14 = evaluate_kh01b_r1_dsc_gates_from_disk(r, run_id, sha)
    write_json_durable_atomic(r / "14_final_orthogonal_verdicts_and_blockers.json", f14)
    rm, ho = render_kh01b_r1_dsc_readme_and_handover_vi(
        run_id=run_id, branch=MANDATORY_BRANCH_KH01B_R1, starting_head_sha=sha, tested_sha=sha,
        dataset_sha=CANONICAL_DATASET_SHA256_KH01B_R1,
        f00=f00, f01=f01, f02=f02, f03=f03, f04=f04, f05=f05, f06=f06, f07=f07,
        f08=f08, f09=f09, f10=f10, f11=f11, f12=f12, f14=f14,
    )
    write_text_durable_atomic(r / "README_KH01B_R1_DATA_SAFETY_CLOSURE.md", rm)
    write_text_durable_atomic(r / "HANDOVER_KH01B_R1_DATA_SAFETY_VI.txt", ho)
    return r


def test_dsc_subproc_independent_verifier_green_and_9_adversarial_tampers(tmp_path: Path) -> None:
    blocked_dir = _build_valid_dsc_bundle(tmp_path, blocked_low_disk=True)
    pass_dir = _build_valid_dsc_bundle(tmp_path, blocked_low_disk=False)

    def _run_v(d: Path) -> tuple[int, dict]:
        p = subprocess.run(
            [sys.executable, str(VERIFIER_DSC), "--run-dir", str(d), "--skip-git-freeze-check"],
            capture_output=True, text=True,
        )
        return p.returncode, json.loads(p.stdout)

    # GREEN 1: Honest BLOCKED_LOW_DISK bundle passes verifier integrity check with recomputed BLOCKED_LOW_DISK
    rc_b, out_b = _run_v(blocked_dir)
    assert rc_b == 0 and out_b["independent_verifier_status"] == "PASS", f"Blocked GREEN failed: {out_b}"
    assert out_b["recomputed_engineering_closure_verdict"] == "BLOCKED_LOW_DISK"

    # GREEN 2: Complete >=20.5 GiB PASS_STRICT_20GIB bundle passes verifier with recomputed PASS
    rc_p, out_p = _run_v(pass_dir)
    assert rc_p == 0 and out_p["independent_verifier_status"] == "PASS", f"Pass GREEN failed: {out_p}"
    assert out_p["recomputed_engineering_closure_verdict"] == "PASS"

    # Tamper 1: Forging ENGINEERING_CLOSURE_VERDICT="PASS" under BLOCKED_LOW_DISK -> FAIL
    t1 = tmp_path / "t1"
    shutil.copytree(blocked_dir, t1)
    f14_1 = json.loads((t1 / "14_final_orthogonal_verdicts_and_blockers.json").read_text("utf-8"))
    f14_1["orthogonal_verdicts"]["ENGINEERING_CLOSURE_VERDICT"] = "PASS"
    write_json_durable_atomic(t1 / "14_final_orthogonal_verdicts_and_blockers.json", f14_1)
    rc1, out1 = _run_v(t1)
    assert rc1 != 0 and any("FORGED" in v for v in out1["violations"])

    # Tamper 2: Planting fake JUnit XML when regression was NOT_RUN_BLOCKED_LOW_DISK -> FAIL
    t2 = tmp_path / "t2"
    shutil.copytree(blocked_dir, t2)
    (t2 / "junit").mkdir(parents=True, exist_ok=True)
    (t2 / "junit/FAKE.xml").write_text("<testsuite tests='1'/>", encoding="utf-8")
    rc2, out2 = _run_v(t2)
    assert rc2 != 0 and any("FAKE_JUNIT_XML" in v for v in out2["violations"])

    # Tamper 3: Injecting null actual_bars into 02 -> FAIL
    t3 = tmp_path / "t3"
    shutil.copytree(blocked_dir, t3)
    f02_3 = json.loads((t3 / "02_gap_schema_mapping_red_green_and_six_tf_completeness.json").read_text("utf-8"))
    f02_3["extended_dev_snapshot_six_tf_gap_summary"]["timeframes"]["1m"]["actual_bars"] = None
    write_json_durable_atomic(t3 / "02_gap_schema_mapping_red_green_and_six_tf_completeness.json", f02_3)
    rc3, out3 = _run_v(t3)
    assert rc3 != 0 and any("GAP_02_NULL_NUMERIC" in v for v in out3["violations"])

    # Tamper 4: Relabeling 1600/2880 M1 partial coverage as VERIFIED_COMPLETE in 04 -> FAIL
    t4 = tmp_path / "t4"
    shutil.copytree(blocked_dir, t4)
    f04_4 = json.loads((t4 / "04_extended_dev_requested_vs_observed_window_coverage.json").read_text("utf-8"))
    f04_4["coverage_by_timeframe"]["1m"]["coverage_verdict"] = "VERIFIED_COMPLETE"
    write_json_durable_atomic(t4 / "04_extended_dev_requested_vs_observed_window_coverage.json", f04_4)
    rc4, out4 = _run_v(t4)
    assert rc4 != 0 and any("PARTIAL_MISLABELED_COMPLETE" in v for v in out4["violations"])

    # Tamper 5: Claiming unverified VIP fee tier as known in 06 -> FAIL
    t5 = tmp_path / "t5"
    shutil.copytree(blocked_dir, t5)
    f06_5 = json.loads((t5 / "06_baseline_0p04_vs_public_0p06_fee_sensitivity_caveats.json").read_text("utf-8"))
    f06_5["actual_account_effective_fee_tier"] = "VIP3_VERIFIED"
    write_json_durable_atomic(t5 / "06_baseline_0p04_vs_public_0p06_fee_sensitivity_caveats.json", f06_5)
    rc5, out5 = _run_v(t5)
    assert rc5 != 0 and any("UNVERIFIED_ACCOUNT_TIER_CLAIMED" in v for v in out5["violations"])

    # Tamper 6: Tampering 0.0006 net PnL to positive +25.0 USD in 06 -> FAIL
    t6 = tmp_path / "t6"
    shutil.copytree(blocked_dir, t6)
    f06_6 = json.loads((t6 / "06_baseline_0p04_vs_public_0p06_fee_sensitivity_caveats.json").read_text("utf-8"))
    f06_6["FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS"]["0.0006"]["net_pnl_1x_usd"] = 25.0
    write_json_durable_atomic(t6 / "06_baseline_0p04_vs_public_0p06_fee_sensitivity_caveats.json", f06_6)
    rc6, out6 = _run_v(t6)
    assert rc6 != 0 and any("PNL_TAMPERED" in v or "FABRICATED_PROFITABILITY" in v for v in out6["violations"])

    # Tamper 7: Lowering enforced_reserve_gib to 10.0 in 00 -> FAIL
    t7 = tmp_path / "t7"
    shutil.copytree(pass_dir, t7)
    f00_7 = json.loads((t7 / "00_preflight_branch_head_dataset_disk_volumes.json").read_text("utf-8"))
    f00_7["disk_reserve_policy"]["enforced_reserve_gib"] = 10.0
    write_json_durable_atomic(t7 / "00_preflight_branch_head_dataset_disk_volumes.json", f00_7)
    rc7, out7 = _run_v(t7)
    assert rc7 != 0 and any("DISK_RESERVE_LOWERED" in v for v in out7["violations"])

    # Tamper 8: Activating LIVE="ON" or promoting KH-01C candidate in 14 -> FAIL
    t8 = tmp_path / "t8"
    shutil.copytree(pass_dir, t8)
    f14_8 = json.loads((t8 / "14_final_orthogonal_verdicts_and_blockers.json").read_text("utf-8"))
    f14_8["orthogonal_verdicts"]["RUNTIME_SAFETY_MODES"]["LIVE"] = "ON"
    f14_8["orthogonal_verdicts"]["KH01C_ADVANCEMENT_VERDICT"] = "PROMOTED"
    write_json_durable_atomic(t8 / "14_final_orthogonal_verdicts_and_blockers.json", f14_8)
    rc8, out8 = _run_v(t8)
    assert rc8 != 0 and any("UNSAFE_RUNTIME_MODES" in v for v in out8["violations"])

    # Tamper 9: Deleting dynamic gate receipt G-DS-06.json -> FAIL
    t9 = tmp_path / "t9"
    shutil.copytree(blocked_dir, t9)
    (t9 / "receipts/G-DS-06.json").unlink()
    rc9, out9 = _run_v(t9)
    assert rc9 != 0 and any("INDEPENDENT_GATE_REEVALUATION_FAILED" in v for v in out9["violations"])


def _build_valid_dsc_r2_bundle(base_dir: Path, *, blocked_low_disk: bool = False) -> Path:
    sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    run_id = f"{'20261120T010000Z' if blocked_low_disk else '20261120T020000Z'}_{sha[:12]}"
    r = base_dir / ("r2_blocked" if blocked_low_disk else "r2_pass")
    (r / "receipts").mkdir(parents=True, exist_ok=True)

    free_gib = 17.1503 if blocked_low_disk else 45.0
    fb = int(free_gib * 1024**3)
    d_pol = compute_kh01b_r1_disk_reserve_policy(fb, volume_free_bytes_by_label={"repo": fb, "output": fb, "temp": fb})

    gap_cov_prov = audit_gap_schema_coverage_and_provenance_from_disk(REPO_ROOT)
    nb_rows = list(iter_jsonl_gz(V130_RUN / "SI0_OFF_BASELINE_NEXT_BAR_OPEN_BID_ASK_trades.jsonl.gz"))
    sp2_rows = list(iter_jsonl_gz(V130_RUN / "SI0_OFF_BASELINE_STRESS_WIDER_SPREAD_SLIPPAGE_2X_trades.jsonl.gz"))
    f05 = compute_kh01b_r1_fee_sensitivity(nb_rows, sp2_rows)
    f11 = run_kh01b_r1_dsc_red_green_fault_suite(REPO_ROOT)

    f00 = {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC_R2,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC_R2,
        "run_id": run_id,
        "branch": MANDATORY_BRANCH_KH01B_R1,
        "head_at_start_sha": PRIOR_DSC_V1_PUBLICATION_HEAD_SHA,
        "tested_executable_sha": sha,
        "orchestrator_pid": -888,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1,
        "disk_mounts_audit": {
            "distinct_underlying_filesystems_count": 2,
            "mounts_by_label": {
                k: {"mountpoint": "D:\\", "st_dev": 1, "fstype": "NTFS", "free_bytes": fb, "free_gib": round(fb / (1024**3), 4), "free_gb_decimal": round(fb / (1000**3), 4), "meets_strict_20p5_gib_preflight": not blocked_low_disk}
                for k in ("repo", "output", "temp", "venv_or_python")
            },
        },
        "disk_reserve_policy": d_pol,
        "source_tree_unchanged_during_run": True,
        "prior_artifacts_unchanged_during_run": True,
    }
    f01 = {"locked_files_modified_count": 0, "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}}
    f02 = gap_cov_prov["doc_02"]
    f03 = {
        "status": "PASS",
        "failed_controls_count": 0,
        "passed_controls_count": 9,
        "m1_48h_dynamic_page_budget": compute_required_page_budget(DEFAULT_DEV_EXTENDED_START_MS, DEFAULT_DEV_EXTENDED_END_MS, 60_000),
    }
    f04 = {**gap_cov_prov["doc_04"], "provenance_audit": gap_cov_prov["doc_05"], "online_capture_executed": False, "online_capture_verdict": "NOT_RUN"}
    f06 = {
        "disk_reserve_policy": d_pol,
        "policy_injections_status": "PASS",
        "stale_owned_temp_reclamation": {"reclaimed_owned_dirs_count": 0, "reclaimed_total_bytes": 0, "reclaimed_total_mib": 0.0, "unowned_paths_deleted_count": 0},
        "runtime_telemetry": {
            "max_children_observed": 2,
            "orphan_pids_at_cleanup_count": 0,
            "min_free_disk_gib": round(fb / (1024**3), 4),
            "post_cleanup_free_gib": round(fb / (1024**3), 4),
            "parent_rss_mib": 120.0,
            "peak_child_tree_rss_mib": 210.0,
            "temp_dirs_cleaned_with_sentinel_check": True,
            "unowned_paths_deleted_count": 0,
        },
    }
    f07 = {
        "status": "PASS",
        "branch": MANDATORY_BRANCH_KH01B_R1,
        "head_at_start_sha": PRIOR_DSC_V1_PUBLICATION_HEAD_SHA,
        "prior_dsc_v1_tested_sha": PRIOR_DSC_V1_TESTED_SHA,
        "tested_executable_sha": sha,
        "run_id": run_id,
        "run_id_embeds_tested_sha12": True,
        "stale_tested_sha_reused": False,
    }

    if blocked_low_disk:
        f08 = {"execution_status": "NOT_RUN_BLOCKED_LOW_DISK", "completed": False}
        f09 = {"execution_status": "NOT_RUN_BLOCKED_LOW_DISK", "completed": False}
        f10 = {"execution_status": "NOT_RUN_BLOCKED_LOW_DISK", "completed": False}
    else:
        (r / "junit").mkdir(parents=True, exist_ok=True)
        nodes = [
            f"backend/tests/test_kh_01b_r1_data_safety_closure.py::test_case_{i:02d}"
            for i in range(22)
        ] + [
            "backend/tests/test_kh_01b_entry_unicorn_research.py::test_b",
            "backend/tests/test_kh_01a_data_and_verification_foundation.py::test_c",
            "backend/tests/test_v17_9_3_1_2_7_causal_truth.py::test_d",
        ]
        tcs_xml = "".join(
            f'<testcase classname="{n.split("::")[0].replace("/", ".").removesuffix(".py")}" name="{n.split("::")[1]}" time="0.01"/>'
            for n in nodes
        )
        xml_p = r / "junit/SHARD_01.xml"
        xml_p.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite name="pytest" tests="{len(nodes)}" errors="0" failures="0" skipped="0">{tcs_xml}</testsuite>', encoding="utf-8")
        xml_sha = hashlib.sha256(xml_p.read_bytes()).hexdigest()
        xml_mt = int(xml_p.stat().st_mtime_ns)

        w_runs_py = [{"mode_label": m, "workers": w, "worker_pids": [100 + w], "wall_clock_seconds": 1.5, "canonical_sorted_outcome_sha256": "c" * 64} for m, w in (("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1))]
        w_runs_sl = [{"mode_label": m, "worker_partitions": w, "worker_pids": [200 + w], "wall_clock_seconds": 2.0, "canonical_sorted_sha256": "d" * 64} for m, w in (("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1))]
        f08 = {
            "execution_status": "EXECUTED",
            "completed": True,
            "pytest_controlled_subset_w1_w2_w4_repeat": {"runs": w_runs_py, "all_4_runs_identical": True},
            "pytest_subset_w1_w2_w4_repeat_identical": True,
            "measured_speedup_w4_vs_w1": 2.2,
            "speedup_truth_label": "MEANINGFUL_PARALLEL_SPEEDUP",
            "r1_variant_slice_w1_w2_w4_repeat": {"runs": w_runs_sl, "all_4_runs_identical": True},
            "r1_variant_slice_w1_w2_w4_repeat_identical": True,
        }
        f09 = {
            "execution_status": "EXECUTED",
            "completed": True,
            "collection_manifest": {"run_id": run_id, "tested_executable_sha": sha, "collect_exit_code": 0, "collected_node_ids": nodes},
            "shard_manifest": {
                "run_id": run_id, "tested_executable_sha": sha, "run_start_epoch_ns": xml_mt - 1000, "nonzero_exit_shards_count": 0,
                "shards": [{"shard_id": "SHARD_01", "shard_name": "SHARD_01", "run_id": run_id, "tested_executable_sha": sha, "pid": 1234, "reused": False, "reused_from_prior_run": False, "exit_code": 0, "timed_out": False, "scheduled_node_ids": nodes, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": xml_sha, "xml_mtime_ns": xml_mt, "junit_sha256": xml_sha, "junit_mtime_ns": xml_mt}],
            },
            "independent_bijection_verification": {"status": "PASS", "counters": {"collected": len(nodes), "executed": len(nodes), "passed": len(nodes), "skipped": 0, "failed": 0, "errors": 0}},
        }
        f10 = {
            "execution_status": "EXECUTED",
            "completed": True,
            "status": "PASS",
            "a1_population_denominator": 56710,
            "a1_evaluated_count": 56710,
            "a1_mismatches_count": 0,
            "si8_separate_population_count": 6375,
            "total_unmixed_universe_count": 63085,
            "three_adapter_episodes_denominator": 246,
            "three_adapter_mismatches_count": 0,
            "si0_e00_primary_nb_summary": {
                "total_scheduled_episodes": 242,
                "closed_trades_count": 166,
                "wins_count": 9,
                "losses_count": 157,
                "total_realized_gross_usd": -142.7836,
                "total_fees_usd": 203.1135,
                "total_realized_net_usd": -345.8971,
                "expectancy_net_r": -0.8474,
            },
            "si0_e00_fill_mode_summaries": {
                "NEXT_BAR_OPEN_BID_ASK": {"total_realized_net_usd": -345.8971},
                "LEGACY_CLOSE_FILL": {"total_realized_net_usd": -312.0},
                "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {"total_realized_net_usd": -410.0},
            },
            "d4_strict_and_adjacent_isolation": {"d4_historical_closed_trades": 0, "d4_historical_expectancy_r": None, "adjacent_counted_toward_d4_count": 0},
        }

    esc = build_and_validate_future_oos_escrow_registry(freeze_timestamp_ms=DEFAULT_DEV_EXTENDED_START_MS, frozen_candidate_code_sha=sha, frozen_config_sha256="cfg")
    f12 = {"future_oos_escrow": esc, "ACTIVATION_READY": "NO", "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"}}
    f13 = {
        "status": "PASS",
        "source_tree_unchanged_during_run": True,
        "prior_artifacts_unchanged_during_run": True,
        "prior_dsc_v1_blocked_run_intact": True,
        "all_prior_latest_run_pointers_intact": True,
        "modified_prior_files_count": 0,
    }

    for fname, doc in (
        ("00_preflight_branch_head_code_sha_disk_mounts_dataset.json", f00),
        ("01_function_callgraph_and_immutability_manifest.json", f01),
        ("02_gap_schema_six_tf_and_actual_missingness.json", f02),
        ("03_pagination_decimal_dedupe_cursor_and_red_green.json", f03),
        ("04_requested_window_coverage_origin_and_online_capture_status.json", f04),
        ("05_fee_0p04_0p06_fixed_fill_and_rerisked_truth.json", f05),
        ("06_strict_disk_recovery_actions_and_resource_monitor.json", f06),
        ("07_sha_lock_current_HEAD_vs_tested_executable_execution_graph.json", f07),
        ("08_async_w1_w2_w4_w1repeat_hashes_timing_and_worker_manifest.json", f08),
        ("09_full_regression_junit_collected_executed_bijection.json", f09),
        ("10_full_baseline_backtest_three_fill_1pos_adapter_and_fingerprint_parity.json", f10),
        ("11_negative_controls_fault_injection_independent_verifier.json", f11),
        ("12_OOS_untouched_and_activation_off_receipt.json", f12),
        ("13_prior_artifact_and_code_immutability_diffs.json", f13),
    ):
        write_json_durable_atomic(r / fname, doc)

    target_map = {
        "G-DS-01": "00_preflight_branch_head_code_sha_disk_mounts_dataset.json",
        "G-DS-02": "02_gap_schema_six_tf_and_actual_missingness.json",
        "G-DS-03": "03_pagination_decimal_dedupe_cursor_and_red_green.json",
        "G-DS-04": "04_requested_window_coverage_origin_and_online_capture_status.json",
        "G-DS-05": "04_requested_window_coverage_origin_and_online_capture_status.json",
        "G-DS-06": "05_fee_0p04_0p06_fixed_fill_and_rerisked_truth.json",
        "G-DS-07": "06_strict_disk_recovery_actions_and_resource_monitor.json",
        "G-DS-08": "06_strict_disk_recovery_actions_and_resource_monitor.json",
        "G-DS-09": "08_async_w1_w2_w4_w1repeat_hashes_timing_and_worker_manifest.json",
        "G-DS-10": "09_full_regression_junit_collected_executed_bijection.json",
        "G-DS-11": "10_full_baseline_backtest_three_fill_1pos_adapter_and_fingerprint_parity.json",
        "G-DS-12": "12_OOS_untouched_and_activation_off_receipt.json",
        "G-DS-13": "11_negative_controls_fault_injection_independent_verifier.json",
        "G-DS-14": "13_prior_artifact_and_code_immutability_diffs.json",
        "R2-A": "07_sha_lock_current_HEAD_vs_tested_executable_execution_graph.json",
        "R2-B": "06_strict_disk_recovery_actions_and_resource_monitor.json",
        "R2-C": "09_full_regression_junit_collected_executed_bijection.json",
        "R2-D": "10_full_baseline_backtest_three_fill_1pos_adapter_and_fingerprint_parity.json",
        "R2-E": "04_requested_window_coverage_origin_and_online_capture_status.json",
        "R2-F": "11_negative_controls_fault_injection_independent_verifier.json",
        "R2-G": "13_prior_artifact_and_code_immutability_diffs.json",
    }
    skip_rcpts = {"G-DS-07", "G-DS-09", "G-DS-10", "G-DS-11", "R2-B", "R2-C", "R2-D"} if blocked_low_disk else set()
    for gid, rel_p in target_map.items():
        if gid not in skip_rcpts:
            build_dynamic_validated_gate_receipt_r1(
                gate_id=gid, run_dir=r, run_id=run_id, tested_sha=sha,
                target_rel_path=rel_p, validator_fn=_pass_v, write_json_fn=write_json_durable_atomic,
            )

    f14 = evaluate_kh01b_r1_dsc_r2_gates_from_disk(r, run_id, sha)
    write_json_durable_atomic(r / "14_final_orthogonal_verdicts_gate_matrix_blockers.json", f14)
    rm, ho = render_kh01b_r1_dsc_r2_readme_and_handover_vi(
        run_id=run_id, branch=MANDATORY_BRANCH_KH01B_R1, head_at_start_sha=PRIOR_DSC_V1_PUBLICATION_HEAD_SHA,
        tested_sha=sha, dataset_sha=CANONICAL_DATASET_SHA256_KH01B_R1,
        f00=f00, f01=f01, f02=f02, f03=f03, f04=f04, f05=f05, f06=f06, f07=f07,
        f08=f08, f09=f09, f10=f10, f11=f11, f12=f12, f13=f13, f14=f14,
    )
    write_text_durable_atomic(r / "README_KH01B_R1_DSC_R2.md", rm)
    write_text_durable_atomic(r / "HANDOVER_KH01B_R1_DSC_R2_VI.txt", ho)
    return r


def test_dsc_r2_sha_graph_latest_pointer_and_31_fault_controls() -> None:
    suite = run_kh01b_r1_dsc_red_green_fault_suite(REPO_ROOT)
    assert suite["status"] == "PASS"
    assert suite["total_mutations"] >= 31
    assert suite["failed_mutations"] == 0
    assert validate_latest_run_pointer_not_blocked({"run_id": PRIOR_DSC_V1_BLOCKED_RUN_ID, "engineering_closure_verdict": "BLOCKED_LOW_DISK"})["status"] == "FAIL"
    assert validate_latest_run_pointer_not_blocked({"run_id": "20261120T090000Z_ok", "engineering_closure_verdict": "PASS", "disk_safety_verdict": "PASS_STRICT_20GIB"})["status"] == "PASS"


def test_dsc_r2_subproc_independent_verifier_green_and_10_adversarial_tampers(tmp_path: Path) -> None:
    r2_blocked = _build_valid_dsc_r2_bundle(tmp_path, blocked_low_disk=True)
    r2_pass = _build_valid_dsc_r2_bundle(tmp_path, blocked_low_disk=False)

    def _run_v(d: Path) -> tuple[int, dict]:
        p = subprocess.run(
            [sys.executable, str(VERIFIER_DSC), "--r2", "--run-dir", str(d), "--skip-git-freeze-check"],
            capture_output=True, text=True,
        )
        return p.returncode, json.loads(p.stdout)

    # GREEN 1: Honest 17.1503 GiB BLOCKED_LOW_DISK R2 bundle passes integrity check as BLOCKED_LOW_DISK
    rc_b, out_b = _run_v(r2_blocked)
    assert rc_b == 0 and out_b["independent_verifier_status"] == "PASS", f"R2 Blocked GREEN failed: {out_b}"
    assert out_b["recomputed_engineering_closure_verdict"] == "BLOCKED_LOW_DISK"

    # GREEN 2: Complete >=20.5 GiB R2 bundle passes integrity check as PASS
    rc_p, out_p = _run_v(r2_pass)
    assert rc_p == 0 and out_p["independent_verifier_status"] == "PASS", f"R2 Pass GREEN failed: {out_p}"
    assert out_p["recomputed_engineering_closure_verdict"] == "PASS"
    assert (r2_pass / "15_independent_verifier_manifest_and_content_sha256.json").is_file()

    # R2 Tamper 1: Forged ENGINEERING_CLOSURE_VERDICT="PASS" in 14 while 06/00 disk BLOCKED -> FAIL
    rt1 = tmp_path / "rt1"
    shutil.copytree(r2_blocked, rt1)
    d14 = json.loads((rt1 / "14_final_orthogonal_verdicts_gate_matrix_blockers.json").read_text("utf-8"))
    d14["orthogonal_verdicts"]["ENGINEERING_CLOSURE_VERDICT"] = "PASS"
    write_json_durable_atomic(rt1 / "14_final_orthogonal_verdicts_gate_matrix_blockers.json", d14)
    rc, out = _run_v(rt1)
    assert rc != 0 and any("FORGED" in v for v in out["violations"])

    # R2 Tamper 2: Fabricated JUnit XML under BLOCKED_LOW_DISK -> FAIL
    rt2 = tmp_path / "rt2"
    shutil.copytree(r2_blocked, rt2)
    (rt2 / "junit").mkdir(parents=True, exist_ok=True)
    (rt2 / "junit/FAKE.xml").write_text("<testsuite tests='15'/>", encoding="utf-8")
    rc, out = _run_v(rt2)
    assert rc != 0 and any("FAKE_JUNIT_XML" in v for v in out["violations"])

    # R2 Tamper 3: Substituting <=20 focused tests for full repository regression in 09 -> FAIL
    rt3 = tmp_path / "rt3"
    shutil.copytree(r2_pass, rt3)
    d09 = json.loads((rt3 / "09_full_regression_junit_collected_executed_bijection.json").read_text("utf-8"))
    d09["independent_bijection_verification"]["counters"]["collected"] = 15
    d09["independent_bijection_verification"]["counters"]["executed"] = 15
    write_json_durable_atomic(rt3 / "09_full_regression_junit_collected_executed_bijection.json", d09)
    rc, out = _run_v(rt3)
    assert rc != 0 and any("JUNIT_BIJECTION" in v or "INDEPENDENT_GATE_REEVALUATION_FAILED" in v for v in out["violations"])

    # R2 Tamper 4: Missing w=4 worker shard in 08 -> FAIL
    rt4 = tmp_path / "rt4"
    shutil.copytree(r2_pass, rt4)
    d08 = json.loads((rt4 / "08_async_w1_w2_w4_w1repeat_hashes_timing_and_worker_manifest.json").read_text("utf-8"))
    d08["pytest_controlled_subset_w1_w2_w4_repeat"]["runs"] = d08["pytest_controlled_subset_w1_w2_w4_repeat"]["runs"][:3]
    write_json_durable_atomic(rt4 / "08_async_w1_w2_w4_w1repeat_hashes_timing_and_worker_manifest.json", d08)
    rc, out = _run_v(rt4)
    assert rc != 0 and any("WORKER_08" in v or "INDEPENDENT_GATE_REEVALUATION_FAILED" in v for v in out["violations"])

    # R2 Tamper 5: Stale tested_executable_sha in 07 -> FAIL
    rt5 = tmp_path / "rt5"
    shutil.copytree(r2_pass, rt5)
    d07 = json.loads((rt5 / "07_sha_lock_current_HEAD_vs_tested_executable_execution_graph.json").read_text("utf-8"))
    d07["tested_executable_sha"] = PRIOR_DSC_V1_TESTED_SHA
    write_json_durable_atomic(rt5 / "07_sha_lock_current_HEAD_vs_tested_executable_execution_graph.json", d07)
    rc, out = _run_v(rt5)
    assert rc != 0 and any("TESTED_EXECUTABLE_SHA_MISMATCH" in v for v in out["violations"])

    # R2 Tamper 6: Fake completed 2880/2880 M1 on 1600-bar snapshot in 04 -> FAIL
    rt6 = tmp_path / "rt6"
    shutil.copytree(r2_pass, rt6)
    d04 = json.loads((rt6 / "04_requested_window_coverage_origin_and_online_capture_status.json").read_text("utf-8"))
    d04["coverage_by_timeframe"]["1m"]["coverage_verdict"] = "VERIFIED_COMPLETE"
    write_json_durable_atomic(rt6 / "04_requested_window_coverage_origin_and_online_capture_status.json", d04)
    rc, out = _run_v(rt6)
    assert rc != 0 and any("PARTIAL_MISLABELED_COMPLETE" in v for v in out["violations"])

    # R2 Tamper 7: SI0-242 rerisked fee study claimed as full 56,710 opportunity rerun in 05 -> FAIL
    rt7 = tmp_path / "rt7"
    shutil.copytree(r2_pass, rt7)
    d05 = json.loads((rt7 / "05_fee_0p04_0p06_fixed_fill_and_rerisked_truth.json").read_text("utf-8"))
    d05["FULL_RE_RISKED_REPLAY"]["0.0006"]["scope_is_full_56710_opportunity_rerun"] = True
    write_json_durable_atomic(rt7 / "05_fee_0p04_0p06_fixed_fill_and_rerisked_truth.json", d05)
    rc, out = _run_v(rt7)
    assert rc != 0 and any("242_RERISK_CLAIMED_AS_FULL_56710_RERUN" in v for v in out["violations"])

    # R2 Tamper 8: Tampered raw HTTP receipt SHA on online capture in 04 -> FAIL
    rt8 = tmp_path / "rt8"
    shutil.copytree(r2_pass, rt8)
    d04_8 = json.loads((rt8 / "04_requested_window_coverage_origin_and_online_capture_status.json").read_text("utf-8"))
    d04_8["online_capture_executed"] = True
    d04_8["online_capture_verdict"] = "VERIFIED_PARTIAL"
    d04_8["online_capture_summary"] = {
        "DATA_ACQUISITION_VERDICT": "VERIFIED_PARTIAL",
        "partition_role": "DEV_EXTENDED_RESEARCH",
        "eligible_for_future_fresh_oos": False,
        "dev_extended_snapshot_rel_path": "ledgers/missing.jsonl.gz",
        "dev_extended_snapshot_sha256": "0" * 64,
        "origin_http_receipts_rel_path": "receipts/missing_http.json",
        "origin_http_receipts_sha256": "0" * 64,
    }
    write_json_durable_atomic(rt8 / "04_requested_window_coverage_origin_and_online_capture_status.json", d04_8)
    rc, out = _run_v(rt8)
    assert rc != 0 and any("ONLINE_SNAPSHOT_SHA256_TAMPERED_OR_MISSING" in v for v in out["violations"])

    # R2 Tamper 9: Promoting BLOCKED_LOW_DISK run into parent latest_run.json -> FAIL
    rt9_parent = tmp_path / "rt9_ns"
    rt9 = rt9_parent / r2_blocked.name
    shutil.copytree(r2_blocked, rt9)
    d00_9 = json.loads((rt9 / "00_preflight_branch_head_code_sha_disk_mounts_dataset.json").read_text("utf-8"))
    write_json_durable_atomic(rt9_parent / "latest_run.json", {"run_id": d00_9["run_id"], "engineering_closure_verdict": "BLOCKED_LOW_DISK"})
    rc, out = _run_v(rt9)
    assert rc != 0 and any("BLOCKED_RUN_ILLEGALLY_PROMOTED_TO_LATEST_RUN_JSON" in v for v in out["violations"])

    # R2 Tamper 10: Wrong branch ("main") in 00 -> FAIL
    rt10 = tmp_path / "rt10"
    shutil.copytree(r2_pass, rt10)
    d00_10 = json.loads((rt10 / "00_preflight_branch_head_code_sha_disk_mounts_dataset.json").read_text("utf-8"))
    d00_10["branch"] = "main"
    write_json_durable_atomic(rt10 / "00_preflight_branch_head_code_sha_disk_mounts_dataset.json", d00_10)
    rc, out = _run_v(rt10)
    assert rc != 0 and any("WRONG_BRANCH" in v for v in out["violations"])
