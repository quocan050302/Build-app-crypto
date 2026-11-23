"""
aurum-desk/backend/tests/test_kh03_r3_c1_worker_policy_schema.py

KH-03.R3.C1 Deterministic Table-Driven Schema & Boundary Suite (P01..P13, P16..P18):
Verifies REAL RED reproduction on immutable R3 artifact 19, GREEN fix (pytest=W4, science=W1),
strict duration type/range/conflict/mixed-key rejection, mode/workers/worker_pids pairing,
missing w=4 / w=1_repeat fail-closed blocking, exact tie-break (prefer fewer workers),
W1_repeat timing variance vs semantic SHA equality, and strict nonstandard JSON NaN/Inf rejection.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict
import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    infer_benchmark_kind_strict,
    legacy_buggy_compute_optimum_worker_policy_r3_red_repro,
    parse_benchmark_run_duration_strict,
    parse_json_strict_no_nonfinite,
    recompute_worker_policy_from_raw_receipt,
    select_measured_worker_policy_strict,
    validate_benchmark_semantic_equivalence_strict,
)

R3_SEALED_19_PATH = (
    REPO_ROOT
    / "aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/20261123T085134Z_2301d9c64898/"
    / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json"
)


def _valid_pytest_doc(
    *,
    w1: float = 50.0,
    w2: float = 40.0,
    w4: float = 30.0,
    w1r: float = 51.0,
    sha: str = "a" * 64,
    w1r_sha: str | None = None,
    run_id: str = "20261123T200000Z_1234567890ab",
    tested_sha: str = "1234567890ab" + "c" * 28,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "all_4_runs_identical": True,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "worker_pids": [1001], "wall_clock_seconds": w1, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
            {"mode_label": "w=2", "workers": 2, "worker_pids": [2001, 2002], "wall_clock_seconds": w2, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
            {"mode_label": "w=4", "workers": 4, "worker_pids": [4001, 4002, 4003, 4004], "wall_clock_seconds": w4, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": sha},
            {"mode_label": "w=1_repeat", "workers": 1, "worker_pids": [5001], "wall_clock_seconds": w1r, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": w1r_sha or sha},
        ],
    }


def _valid_science_doc(
    *,
    w1: float = 2.05,
    w2: float = 2.20,
    w4: float = 2.35,
    w1r: float = 2.10,
    sha: str = "b" * 64,
    w1r_sha: str | None = None,
    run_id: str = "20261123T200000Z_1234567890ab",
    tested_sha: str = "1234567890ab" + "c" * 28,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "all_4_runs_identical": True,
        "runs": [
            {"mode_label": "w=1", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": w1, "canonical_sorted_sha256": sha},
            {"mode_label": "w=2", "workers": 2, "chunks_count": 2, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": w2, "canonical_sorted_sha256": sha},
            {"mode_label": "w=4", "workers": 4, "chunks_count": 4, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": w4, "canonical_sorted_sha256": sha},
            {"mode_label": "w=1_repeat", "workers": 1, "chunks_count": 1, "evaluated_bars_count": 8000, "rows_count": 12062, "wall_seconds": w1r, "canonical_sorted_sha256": w1r_sha or sha},
        ],
    }


def test_p01_old_r3_actual_pytest_fixture_red_then_green_w4():
    """P01: Immutable R3 artifact 19 pytest -> old buggy logic gives empty map + W1 (RED); new logic gives W4 (GREEN)."""
    raw_19 = json.loads(R3_SEALED_19_PATH.read_text("utf-8"))
    pb = raw_19["pytest_controlled_subset_w1_w2_w4_repeat"]

    red = legacy_buggy_compute_optimum_worker_policy_r3_red_repro(pb)
    assert red["mean_wall_sec_by_mode"] == {}
    assert red["optimum_worker_policy"] == "w=1"

    green = select_measured_worker_policy_strict(
        pb,
        benchmark_kind=BENCHMARK_KIND_PYTEST,
        system_limits={"configured_child_process_cap": 4, "full_suite_safe_worker_cap": 3},
        is_historical_reference=True,
    )
    assert green["status"] == "PASS"
    assert green["provenance_role"] == "HISTORICAL_REFERENCE_ONLY"
    assert green["measured_fastest_mode"] == "w=4"
    assert green["optimum_worker_policy"] == "w=4"
    assert green["applied_worker_count"] == 3
    assert green["full_suite_speedup_extrapolated"] is False
    assert green["mean_wall_sec_by_mode"] == {"w=1": 51.698, "w=2": 41.123, "w=4": 32.947}
    assert green["w1_repeat_wall_sec"] == 56.594
    assert abs(green["measured_speedup_w4_vs_w1"] - 1.569) <= 0.002


def test_p02_old_r3_actual_science_fixture_red_then_green_w1():
    """P02: Immutable R3 artifact 19 science -> old buggy logic gives empty map + W1 (RED); new logic gives nonempty map + W1 (GREEN)."""
    raw_19 = json.loads(R3_SEALED_19_PATH.read_text("utf-8"))
    sb = raw_19["r2_science_slice_w1_w2_w4_repeat"]

    red = legacy_buggy_compute_optimum_worker_policy_r3_red_repro(sb)
    assert red["mean_wall_sec_by_mode"] == {}
    assert red["optimum_worker_policy"] == "w=1"

    green = select_measured_worker_policy_strict(
        sb,
        benchmark_kind=BENCHMARK_KIND_SCIENCE,
        system_limits={"configured_child_process_cap": 4, "full_suite_safe_worker_cap": 4},
        is_historical_reference=True,
    )
    assert green["status"] == "PASS"
    assert green["measured_fastest_mode"] == "w=1"
    assert green["optimum_worker_policy"] == "w=1"
    assert green["applied_worker_count"] == 1
    assert green["mean_wall_sec_by_mode"] == {"w=1": 2.069, "w=2": 2.181, "w=4": 2.277}
    assert green["w1_repeat_wall_sec"] == 2.712


def test_p03_four_fresh_successful_runs_pass_equivalence_and_policy():
    """P03: Four fresh valid W1/W2/W4/W1_repeat runs pass equivalence with exact run_id, tested_sha, count and SHA."""
    pdoc = _valid_pytest_doc()
    eq = validate_benchmark_semantic_equivalence_strict(
        pdoc,
        benchmark_kind=BENCHMARK_KIND_PYTEST,
        expected_run_id=pdoc["run_id"],
        expected_tested_sha=pdoc["tested_executable_sha"],
    )
    assert eq["status"] == "PASS"
    assert eq["valid"] is True
    assert eq["semantic_item_count"] == 155
    assert eq["canonical_semantic_sha256"] == "a" * 64


def test_p04_missing_w1_repeat_blocked_no_certificate():
    """P04: Missing w=1_repeat MUST block certification even if W1/W2/W4 are valid."""
    pdoc = _valid_pytest_doc()
    pdoc["runs"] = [r for r in pdoc["runs"] if r["mode_label"] != "w=1_repeat"]
    res = select_measured_worker_policy_strict(pdoc, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert res["status"] == "BLOCKED_INVALID_BENCHMARK"
    assert res["can_certify"] is False
    assert any("MISSING_REQUIRED_BENCHMARK_MODES:w=1_repeat" in v for v in res["violations"])


def test_p05_missing_w4_blocked_never_silently_w2_optimal():
    """P05: Missing w=4 MUST block certification and NEVER silently select w=2 as optimal."""
    pdoc = _valid_pytest_doc()
    pdoc["runs"] = [r for r in pdoc["runs"] if r["mode_label"] != "w=4"]
    res = select_measured_worker_policy_strict(pdoc, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert res["status"] == "BLOCKED_INVALID_BENCHMARK"
    assert res["measured_fastest_mode"] is None
    assert res["optimum_worker_policy"] != "w=2"
    assert any("MISSING_REQUIRED_BENCHMARK_MODES:w=4" in v for v in res["violations"])


def test_p06_duplicate_w2_rows_disagree_fail_schema():
    """P06: Duplicated w=2 rows that disagree MUST fail schema validation."""
    pdoc = _valid_pytest_doc()
    dup = copy.deepcopy(pdoc["runs"][1])
    dup["wall_clock_seconds"] = 99.999
    pdoc["runs"].append(dup)
    res = validate_benchmark_semantic_equivalence_strict(pdoc, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert res["valid"] is False
    assert any("DUPLICATE_INCOMPATIBLE_MODE_ROW:w=2" in v for v in res["violations"])


@pytest.mark.parametrize(
    "bad_val,expected_tok",
    [
        ("32.947", "DURATION_NON_NUMERIC_TYPE"),
        (True, "DURATION_BOOL_FORBIDDEN"),
        (False, "DURATION_BOOL_FORBIDDEN"),
        (None, "DURATION_KEY_NULL"),
        (float("nan"), "DURATION_NON_FINITE"),
        (float("inf"), "DURATION_NON_FINITE"),
        (float("-inf"), "DURATION_NON_FINITE"),
        (0, "DURATION_NON_POSITIVE"),
        (0.0, "DURATION_NON_POSITIVE"),
        (-1.5, "DURATION_NON_POSITIVE"),
        (999_999.0, "DURATION_EXCEEDS_UPPER_BOUND"),
    ],
)
def test_p07_p08_p09_strict_duration_types_nonfinite_and_nonpositive_rejected(bad_val, expected_tok):
    """P07/P08/P09: Reject string, bool, None, NaN, +Inf, -Inf, zero, negative, and huge upper-bound durations."""
    row = {"mode_label": "w=1", "workers": 1, "worker_pids": [101], "wall_clock_seconds": bad_val}
    parsed = parse_benchmark_run_duration_strict(row, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert parsed["valid"] is False
    assert any(expected_tok in v for v in parsed["violations"])


@pytest.mark.parametrize("raw_json", ['{"wall_clock_seconds": NaN}', '{"wall_clock_seconds": Infinity}', '{"wall_clock_seconds": -Infinity}'])
def test_p08_strict_json_parser_rejects_nonstandard_nan_inf_tokens(raw_json):
    """P08: Nonstandard JSON NaN/Infinity/-Infinity parse edge MUST raise ValueError."""
    with pytest.raises(ValueError, match="NONSTANDARD_JSON_CONSTANT_FORBIDDEN"):
        parse_json_strict_no_nonfinite(raw_json)


def test_p10_conflicting_wall_clock_seconds_and_elapsed_sec_fail():
    """P10: Conflicting simultaneous `wall_clock_seconds` and `elapsed_sec` MUST fail, not pick an implicit winner."""
    row = {"mode_label": "w=1", "workers": 1, "worker_pids": [101], "wall_clock_seconds": 32.947, "elapsed_sec": 51.0}
    parsed = parse_benchmark_run_duration_strict(row, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert parsed["valid"] is False
    assert any("CONFLICTING_DURATION_KEYS" in v for v in parsed["violations"])


def test_p11_ambiguous_mixture_of_pytest_and_science_keys_fails():
    """P11: Mixing pytest (`wall_clock_seconds`) and science (`wall_seconds`) keys in the same doc MUST fail."""
    pdoc = _valid_pytest_doc()
    pdoc["runs"][2]["wall_seconds"] = 2.277
    res = validate_benchmark_semantic_equivalence_strict(pdoc, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert res["valid"] is False
    assert any("MIXED_PYTEST_AND_SCIENCE_KEYS" in v or "CROSS_KIND_SCHEMA_CONTAMINATION" in v for v in res["violations"])
    inferred, errs = infer_benchmark_kind_strict(pdoc)
    assert inferred is None
    assert any("AMBIGUOUS_MIXED" in e for e in errs)


def test_p12_mode_label_w4_with_workers_1_fails():
    """P12: `mode_label=w=4` paired with `workers=1` MUST fail."""
    row = {"mode_label": "w=4", "workers": 1, "worker_pids": [101], "wall_clock_seconds": 30.0}
    parsed = parse_benchmark_run_duration_strict(row, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert parsed["valid"] is False
    assert any("MODE_LABEL_WORKERS_MISMATCH" in v for v in parsed["violations"])


def test_p13_w4_incompatible_worker_pids_count_fails():
    """P13: W4 run whose `worker_pids` count or uniqueness disagrees with `workers=4` MUST fail."""
    row_short = {"mode_label": "w=4", "workers": 4, "worker_pids": [101, 102], "wall_clock_seconds": 30.0}
    parsed_s = parse_benchmark_run_duration_strict(row_short, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert parsed_s["valid"] is False
    assert any("INCOMPATIBLE_WORKER_PIDS_COUNT" in v for v in parsed_s["violations"])

    row_dup = {"mode_label": "w=4", "workers": 4, "worker_pids": [101, 101, 102, 103], "wall_clock_seconds": 30.0}
    parsed_d = parse_benchmark_run_duration_strict(row_dup, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert parsed_d["valid"] is False
    assert any("DUPLICATE_WORKER_PIDS" in v for v in parsed_d["violations"])


def test_p16_w4_and_w2_exact_tie_prefers_fewer_workers_and_discloses_noisy_borderline():
    """P16: Exact tie between W4 and W2 picks W2 deterministically; noisy borderline gain warns and retains conservative policy."""
    # Exact tie with clear gain vs W1 (50s -> 35s)
    doc_tie = _valid_pytest_doc(w1=50.0, w2=35.0, w4=35.0, w1r=50.5)
    pol_tie = select_measured_worker_policy_strict(doc_tie, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert pol_tie["status"] == "PASS"
    assert pol_tie["tie_detected"] is True
    assert pol_tie["measured_fastest_mode"] == "w=2"
    assert pol_tie["applied_worker_count"] == 2

    # Noisy borderline gain: W2=48.5s vs W1=50.0s (3% gain < 5% min threshold & < 12% repeat noise)
    doc_noisy = _valid_pytest_doc(w1=50.0, w2=48.5, w4=48.5, w1r=56.0)
    pol_noisy = select_measured_worker_policy_strict(doc_noisy, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert pol_noisy["status"] == "PASS"
    assert pol_noisy["measured_fastest_mode"] == "w=2"
    assert pol_noisy["borderline_or_noisy_gain_warning"] is True
    assert pol_noisy["policy_recommended_mode_after_gain_rule"] == "w=1"
    assert pol_noisy["applied_worker_count"] == 1


def test_p17_w1_repeat_timing_variance_passes_determinism_with_noise_warning():
    """P17: W1_repeat differs in wall-clock timing (+20%) but SAME semantic SHA -> determinism PASS, stability warns."""
    pdoc = _valid_pytest_doc(w1=50.0, w2=40.0, w4=30.0, w1r=60.0)
    pol = select_measured_worker_policy_strict(pdoc, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert pol["status"] == "PASS"
    assert pol["timing_determinism_claimed"] is False
    assert pol["w1_repeat_wall_sec"] == 60.0
    assert abs(pol["w1_repeat_variance_pct"] - 0.20) <= 0.001
    assert pol["timing_stability_status"] == "HIGH_TIMING_NOISE_WARNING"


def test_p18_w1_repeat_semantic_sha_differs_fails_determinism():
    """P18: W1_repeat semantic SHA differs -> FAIL even when all 4 rows have valid timings."""
    pdoc = _valid_pytest_doc(sha="a" * 64, w1r_sha="f" * 64)
    pol = select_measured_worker_policy_strict(pdoc, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert pol["status"] == "BLOCKED_INVALID_BENCHMARK"
    assert pol["can_certify"] is False
    assert any("W1_REPEAT_SEMANTIC_HASH_MISMATCH" in v for v in pol["violations"])


def test_recompute_worker_policy_catches_empty_means_wrong_fastest_and_forged_speedup():
    """Recompute helper rejects empty means (P19), wrong fastest mode (P20), and forged speedup=999 (P21)."""
    pdoc = _valid_pytest_doc(w1=50.0, w2=40.0, w4=30.0, w1r=51.0)
    good = select_measured_worker_policy_strict(pdoc, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert recompute_worker_policy_from_raw_receipt(pdoc, good, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"] is True

    bad_empty = dict(good, mean_wall_sec_by_mode={})
    r_emp = recompute_worker_policy_from_raw_receipt(pdoc, bad_empty, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert r_emp["valid"] is False
    assert any("EMPTY_REPORTED_MEAN_WALL_SEC_BY_MODE" in d for d in r_emp["discrepancies"])

    bad_opt = dict(good, optimum_worker_policy="w=1", measured_fastest_mode="w=1")
    r_opt = recompute_worker_policy_from_raw_receipt(pdoc, bad_opt, benchmark_kind=BENCHMARK_KIND_PYTEST)
    assert r_opt["valid"] is False
    assert any("OPTIMUM_WORKER_POLICY_MISMATCH" in d for d in r_opt["discrepancies"])

    r_sp = recompute_worker_policy_from_raw_receipt(
        pdoc, good, benchmark_kind=BENCHMARK_KIND_PYTEST, reported_speedup_w4_vs_w1=999.0
    )
    assert r_sp["valid"] is False
    assert any("FORGED_OR_DRIFTED_SPEEDUP_W4" in d for d in r_sp["discrepancies"])
