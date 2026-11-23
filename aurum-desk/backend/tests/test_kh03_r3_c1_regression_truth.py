"""
aurum-desk/backend/tests/test_kh03_r3_c1_regression_truth.py

KH-03.R3.C1 Backward-Compatible Shim, Legacy R1 Callgraph Guard & 14 NEW Worker-Policy Fault Controls:
Verifies `run_kh_03_r3_acceptance._compute_optimum_worker_policy` works for both single-arg
backward-compatible callers and explicit `benchmark_kind` callers on immutable R3 artifact 19,
verifies ZERO unauthorized R1 calls introduced by C1, and verifies 14 NEW unique C1 worker-policy
RED/GREEN fault controls (`KH03R3C1-F01`..`F14`).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    legacy_buggy_compute_optimum_worker_policy_r3_red_repro,
    parse_benchmark_run_duration_strict,
    parse_json_strict_no_nonfinite,
    recompute_worker_policy_from_raw_receipt,
    select_measured_worker_policy_strict,
)
from services.kh_03_r3_legacy_research_guard import audit_kh03_r3_legacy_research_callgraph
from tools.run_kh_03_r3_acceptance import _compute_optimum_worker_policy

R3_SEALED_19_PATH = (
    REPO_ROOT
    / "aurum-desk/docs/artifacts/kh_03_r3_entry_exit_diagnosis/20261123T085134Z_2301d9c64898/"
    / "19_w1_w2_w4_w1repeat_hashes_timings_speedups.json"
)


def run_new_kh03_r3_c1_worker_policy_fault_controls() -> List[Dict[str, Any]]:
    """Executes 14 NEW unique, non-redundant worker-policy RED/GREEN fault controls (KH03R3C1-F01..F14)."""
    raw_19 = json.loads(R3_SEALED_19_PATH.read_text("utf-8"))
    pb = raw_19["pytest_controlled_subset_w1_w2_w4_repeat"]
    sb = raw_19["r2_science_slice_w1_w2_w4_repeat"]
    g_p = select_measured_worker_policy_strict(
        pb, benchmark_kind=BENCHMARK_KIND_PYTEST,
        system_limits={"configured_child_process_cap": 4, "full_suite_safe_worker_cap": 3},
        is_historical_reference=True,
    )
    g_s = select_measured_worker_policy_strict(
        sb, benchmark_kind=BENCHMARK_KIND_SCIENCE,
        system_limits={"configured_child_process_cap": 4, "full_suite_safe_worker_cap": 4},
        is_historical_reference=True,
    )

    out: List[Dict[str, Any]] = []

    def _rec(fid: str, name: str, fn: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        out.append({
            "fault_id": fid, "name": name, "anchored_function": fn,
            "red_rejected": bool(red_ok), "green_accepted": bool(green_ok),
            "status": "PASS" if (red_ok and green_ok) else "FAIL", "diagnostic": detail,
        })

    # F01: Old buggy R3 pytest policy yields empty map + w=1; new yields w=4 + 3 means
    red_p = legacy_buggy_compute_optimum_worker_policy_r3_red_repro(pb)
    _rec("KH03R3C1-F01", "R3_PYTEST_EMPTY_MEANS_BUG_FIXED", "select_measured_worker_policy_strict", red_p["mean_wall_sec_by_mode"] == {} and red_p["optimum_worker_policy"] == "w=1", g_p["status"] == "PASS" and g_p["measured_fastest_mode"] == "w=4" and len(g_p["mean_wall_sec_by_mode"]) == 3, "pytest wall_clock_seconds parsed -> w=4")

    # F02: Old buggy R3 science policy yields empty map; new yields nonempty map + w=1
    red_s = legacy_buggy_compute_optimum_worker_policy_r3_red_repro(sb)
    _rec("KH03R3C1-F02", "R3_SCIENCE_EMPTY_MEANS_BUG_FIXED", "select_measured_worker_policy_strict", red_s["mean_wall_sec_by_mode"] == {}, g_s["status"] == "PASS" and g_s["measured_fastest_mode"] == "w=1" and len(g_s["mean_wall_sec_by_mode"]) == 3, "science wall_seconds parsed -> w=1")

    # F03: Missing w=1_repeat blocks certification
    pb_no_rep = copy.deepcopy(pb)
    pb_no_rep["runs"] = [r for r in pb_no_rep["runs"] if r["mode_label"] != "w=1_repeat"]
    _rec("KH03R3C1-F03", "MISSING_W1_REPEAT_BLOCKED", "validate_benchmark_semantic_equivalence_strict", select_measured_worker_policy_strict(pb_no_rep, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)["status"] == "BLOCKED_INVALID_BENCHMARK", g_p["status"] == "PASS", "w=1_repeat mandatory")

    # F04: Missing w=4 blocks certification (never silently picks w=2)
    pb_no_w4 = copy.deepcopy(pb)
    pb_no_w4["runs"] = [r for r in pb_no_w4["runs"] if r["mode_label"] != "w=4"]
    r_no_w4 = select_measured_worker_policy_strict(pb_no_w4, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)
    _rec("KH03R3C1-F04", "MISSING_W4_NEVER_SILENTLY_W2", "select_measured_worker_policy_strict", r_no_w4["status"] == "BLOCKED_INVALID_BENCHMARK" and r_no_w4["optimum_worker_policy"] != "w=2", g_p["measured_fastest_mode"] == "w=4", "missing w=4 fail-closed")

    # F05: Bool & string durations rejected
    _rec("KH03R3C1-F05", "BOOL_AND_STRING_DURATION_REJECTED", "parse_benchmark_run_duration_strict", (not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": True}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"]) and (not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": "32.9"}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"]), parse_benchmark_run_duration_strict(pb["runs"][0], benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"], "bool/str rejected")

    # F06: Non-finite NaN/Inf & nonstandard JSON rejected
    nan_json_blocked = False
    try:
        parse_json_strict_no_nonfinite('{"wall_clock_seconds": NaN}')
    except ValueError:
        nan_json_blocked = True
    _rec("KH03R3C1-F06", "NONFINITE_NAN_INF_AND_JSON_REJECTED", "parse_json_strict_no_nonfinite", nan_json_blocked and not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": float("inf")}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"], True, "NaN/Inf blocked")

    # F07: Zero and negative durations rejected
    _rec("KH03R3C1-F07", "ZERO_AND_NEGATIVE_DURATION_REJECTED", "parse_benchmark_run_duration_strict", (not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": 0.0}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"]) and (not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": -3.0}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"]), True, "zero/neg blocked")

    # F08: Conflicting wall_clock_seconds vs elapsed_sec rejected
    _rec("KH03R3C1-F08", "CONFLICTING_DURATION_KEYS_REJECTED", "parse_benchmark_run_duration_strict", not parse_benchmark_run_duration_strict({"mode_label": "w=1", "workers": 1, "wall_clock_seconds": 32.0, "elapsed_sec": 55.0}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"], True, "conflicting keys fail-closed")

    # F09: Mixed pytest/science keys rejected
    pb_mix = copy.deepcopy(pb)
    pb_mix["runs"][0]["wall_seconds"] = 2.1
    _rec("KH03R3C1-F09", "MIXED_PYTEST_SCIENCE_KEYS_REJECTED", "validate_benchmark_semantic_equivalence_strict", select_measured_worker_policy_strict(pb_mix, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)["status"] == "BLOCKED_INVALID_BENCHMARK", True, "cross-kind keys rejected")

    # F10: mode_label/workers mismatch & worker_pids count mismatch rejected
    _rec("KH03R3C1-F10", "MODE_WORKERS_AND_PIDS_MISMATCH_REJECTED", "parse_benchmark_run_duration_strict", (not parse_benchmark_run_duration_strict({"mode_label": "w=4", "workers": 1, "wall_clock_seconds": 30.0}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"]) and (not parse_benchmark_run_duration_strict({"mode_label": "w=4", "workers": 4, "worker_pids": [1, 2], "wall_clock_seconds": 30.0}, benchmark_kind=BENCHMARK_KIND_PYTEST)["valid"]), True, "worker identity enforced")

    # F11: W1_repeat semantic SHA drift rejected
    pb_sha = copy.deepcopy(pb)
    pb_sha["runs"][3]["canonical_sorted_outcome_sha256"] = "0" * 64
    _rec("KH03R3C1-F11", "W1_REPEAT_SEMANTIC_SHA_DRIFT_REJECTED", "validate_benchmark_semantic_equivalence_strict", select_measured_worker_policy_strict(pb_sha, benchmark_kind=BENCHMARK_KIND_PYTEST, is_historical_reference=True)["status"] == "BLOCKED_INVALID_BENCHMARK", g_p["status"] == "PASS", "w1_repeat SHA drift blocked")

    # F12: Resource floor breach (disk < 20.5 GiB or RAM < 512 MiB or waiver=True) blocks fail-closed
    r_low = select_measured_worker_policy_strict(pb, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits={"preflight_disk_free_gib": 18.0, "disk_free_gib": 18.0, "available_ram_mib": 300.0}, is_historical_reference=True)
    _rec("KH03R3C1-F12", "RESOURCE_FLOOR_BREACH_BLOCKS_POLICY", "select_measured_worker_policy_strict", r_low["status"] == "BLOCKED_RESOURCE" and r_low["applied_worker_count"] == 0, g_p["applied_worker_count"] == 3, "low disk/RAM fail-closed")

    # F13: Recomputer rejects empty means, wrong fastest mode, and forged speedup=999
    bad_rec = recompute_worker_policy_from_raw_receipt(pb, dict(g_p, mean_wall_sec_by_mode={}, optimum_worker_policy="w=1"), benchmark_kind=BENCHMARK_KIND_PYTEST, reported_speedup_w4_vs_w1=999.0, is_historical_reference=True)
    good_rec = recompute_worker_policy_from_raw_receipt(pb, g_p, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits={"configured_child_process_cap": 4, "full_suite_safe_worker_cap": 3}, is_historical_reference=True)
    _rec("KH03R3C1-F13", "RECOMPUTE_CATCHES_EMPTY_WRONG_OPT_FORGED_SPEEDUP", "recompute_worker_policy_from_raw_receipt", not bad_rec["valid"] and len(bad_rec["discrepancies"]) >= 3, good_rec["valid"], "recompute discrepancies caught")

    # F14: Full-suite speedup extrapolation rejected & applied cap <= 3 enforced on shim
    bad_ext = recompute_worker_policy_from_raw_receipt(pb, dict(g_p, full_suite_speedup_extrapolated=True, applied_worker_count=4), benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits={"configured_child_process_cap": 4, "full_suite_safe_worker_cap": 3}, is_historical_reference=True)
    _rec("KH03R3C1-F14", "EXTRAPOLATION_AND_OVERCAP_REJECTED", "recompute_worker_policy_from_raw_receipt", not bad_ext["valid"], g_p["full_suite_speedup_extrapolated"] is False and g_p["applied_worker_count"] <= 3, "no unmeasured full-suite speedup")

    return out


def test_r3_shim_backward_compat_single_arg_and_explicit_kind():
    raw_19 = json.loads(R3_SEALED_19_PATH.read_text("utf-8"))
    pb = raw_19["pytest_controlled_subset_w1_w2_w4_repeat"]
    sb = raw_19["r2_science_slice_w1_w2_w4_repeat"]

    # Single-arg backward compatibility
    p_single = _compute_optimum_worker_policy(pb)
    assert p_single["status"] == "PASS"
    assert p_single["optimum_worker_policy"] == "w=4"
    assert p_single["mean_wall_sec_by_mode"] == {"w=1": 51.698, "w=2": 41.123, "w=4": 32.947}
    assert p_single["applied_worker_count"] <= 3

    s_single = _compute_optimum_worker_policy(sb)
    assert s_single["status"] == "PASS"
    assert s_single["optimum_worker_policy"] == "w=1"
    assert s_single["mean_wall_sec_by_mode"] == {"w=1": 2.069, "w=2": 2.181, "w=4": 2.277}

    # Empty / unidentified doc fails closed instead of silently passing
    empty_res = _compute_optimum_worker_policy({"runs": []})
    assert empty_res["status"] == "BLOCKED_INVALID_BENCHMARK"
    assert empty_res["can_certify"] is False


def test_new_14_c1_worker_policy_red_green_fault_controls_all_pass():
    controls = run_new_kh03_r3_c1_worker_policy_fault_controls()
    assert len(controls) == 14
    assert len({c["fault_id"] for c in controls}) == 14
    assert all(c["status"] == "PASS" for c in controls), [c for c in controls if c["status"] != "PASS"]


def test_c1_introduces_zero_unauthorized_legacy_r1_callers():
    cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
    assert cg["LEGACY_METHOD_GUARD"] == "PASS"
    assert cg["unauthorized_r1_callers_count"] == 0
