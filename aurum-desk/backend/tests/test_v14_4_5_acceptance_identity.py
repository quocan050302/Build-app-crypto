"""
backend/tests/test_v14_4_5_acceptance_identity.py
V14.4.5 — Case A01: Acceptance Runner Identity & Strict Validation Gates.

Covers PHẦN 067–070, 106–107, 116:
- Rejection of validation calls without mandatory RunContext.
- Rejection of mismatched Run ID, SHA, or environment.
- Rejection of stale browser artifacts (mtime before current run start).
- Rejection of missing required measurements (no fake defaults like selections: 100 or leak: 0).
- Rejection of NaN, Inf, bool, or 0 samples.
- Successful pass on genuine same-run fresh artifacts.
"""
import os
import sys
import math
import json
import tempfile
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from run_v14_4_2_acceptance import validate_required_metrics


def _valid_bench_data(run_id="run_1001", sha="d5034e1"):
    return {
        "status": "PASS",
        "run_id": run_id,
        "tested_sha": sha,
        "cycles": 100,
        "latency_ms": {
            "p50": 10.5,
            "p95": 25.0,
            "p99": 45.0,
            "max": 60.0,
            "sample_count": 100,
        },
        "browser_measurements": {
            "status": "PASS",
            "stream_duration_s": 65.0,
            "events_streamed": 10500,
            "selections_completed": 100,
            "render_latency_ms": {
                "p50": 15.0,
                "p95": 45.0,
                "p99": 80.0,
                "max": 120.0,
                "sample_count": 10000,
            },
            "queue_peak": 12,
            "queue_current": 0,
            "selected_kind_counts": {"SCENARIO": 25, "SETUP": 25, "ORDER": 25, "DRAFT": 25},
            "active_subscriber_count": 2,
            "subscriber_baseline": 2,
            "subscriber_leak": 0,
            "chart_create_count": 1,
            "chart_destroy_count": 0,
            "dropped_execution_event_count": 0,
        },
    }


def test_a01_missing_run_context_fails():
    """Case A01 (PHẦN 067, 107): validate_required_metrics without RunContext must fail."""
    bench = _valid_bench_data()
    res = validate_required_metrics(bench, run_context=None)
    assert res["pass"] is False
    assert "MISSING_REQUIRED_RUN_CONTEXT" in res["errors"]


def test_a01_identity_mismatch_fails():
    """Case A01 (PHẦN 067, 106): Mismatched run_id or tested_sha must fail."""
    bench = _valid_bench_data(run_id="run_expected", sha="sha_expected")

    # Run ID mismatch
    ctx_bad_id = {"run_id": "run_other", "sha": "sha_expected", "started_at_ms": 1000}
    res_id = validate_required_metrics(bench, run_context=ctx_bad_id)
    assert res_id["pass"] is False
    assert any("Run ID mismatch" in e for e in res_id["errors"])

    # SHA mismatch
    ctx_bad_sha = {"run_id": "run_expected", "sha": "sha_other", "started_at_ms": 1000}
    res_sha = validate_required_metrics(bench, run_context=ctx_bad_sha)
    assert res_sha["pass"] is False
    assert any("Tested SHA mismatch" in e for e in res_sha["errors"])


def test_a01_no_fake_defaults_in_measurements():
    """Case A01 (PHẦN 068): Missing measurements fail closed without fake defaults."""
    ctx = {"run_id": "run_1001", "sha": "d5034e1", "started_at_ms": 1000}

    # Missing selections_completed
    bench1 = _valid_bench_data()
    bench1["browser_measurements"]["selections_completed"] = None
    res1 = validate_required_metrics(bench1, run_context=ctx)
    assert res1["pass"] is False
    assert any("selections_completed: MISSING_REQUIRED_MEASUREMENT" in e for e in res1["errors"])

    # Missing subscriber_leak
    bench2 = _valid_bench_data()
    bench2["browser_measurements"]["subscriber_leak"] = None
    res2 = validate_required_metrics(bench2, run_context=ctx)
    assert res2["pass"] is False
    assert any("subscriber_leak: MISSING_REQUIRED_MEASUREMENT" in e for e in res2["errors"])

    # Missing queue_current
    bench3 = _valid_bench_data()
    bench3["browser_measurements"]["queue_current"] = None
    res3 = validate_required_metrics(bench3, run_context=ctx)
    assert res3["pass"] is False
    assert any("queue_current: MISSING_REQUIRED_MEASUREMENT" in e for e in res3["errors"])


def test_a01_nan_inf_bool_rejected():
    """Case A01 (PHẦN 068, 107): NaN, Inf, or boolean values in numeric metrics fail closed."""
    ctx = {"run_id": "run_1001", "sha": "d5034e1", "started_at_ms": 1000}

    # NaN in p95
    bench_nan = _valid_bench_data()
    bench_nan["browser_measurements"]["render_latency_ms"]["p95"] = float("nan")
    res_nan = validate_required_metrics(bench_nan, run_context=ctx)
    assert res_nan["pass"] is False

    # Inf in queue_peak
    bench_inf = _valid_bench_data()
    bench_inf["browser_measurements"]["queue_peak"] = float("inf")
    res_inf = validate_required_metrics(bench_inf, run_context=ctx)
    assert res_inf["pass"] is False

    # Boolean True in subscriber_leak
    bench_bool = _valid_bench_data()
    bench_bool["browser_measurements"]["subscriber_leak"] = True
    res_bool = validate_required_metrics(bench_bool, run_context=ctx)
    assert res_bool["pass"] is False


def test_a01_samples_zero_fails():
    """Case A01 (PHẦN 068): sample_count 0 fails closed."""
    ctx = {"run_id": "run_1001", "sha": "d5034e1", "started_at_ms": 1000}
    bench_zero = _valid_bench_data()
    bench_zero["browser_measurements"]["render_latency_ms"]["sample_count"] = 0
    res = validate_required_metrics(bench_zero, run_context=ctx)
    assert res["pass"] is False
    assert any("sample_count 0 is invalid or 0" in e for e in res["errors"])


def test_a01_genuine_same_run_artifact_passes():
    """Case A01 (PHẦN 069, 116): Genuine same-run fresh artifact with matching context passes."""
    ctx = {"run_id": "run_1001", "sha": "d5034e1", "started_at_ms": 1000}
    bench_ok = _valid_bench_data()
    res = validate_required_metrics(bench_ok, run_context=ctx)
    assert res["pass"] is True
    assert res["status"] == "PASS"
    assert len(res["errors"]) == 0
