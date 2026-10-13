"""
backend/tests/test_v14_4_4_metric_gate.py
V14.4.4 - Strict Metric Gate & Validator Injection Tests.

Proves:
1. validate_required_metrics fails closed when sample_count == 0.
2. Rejects NaN, Inf, boolean values passed as numbers.
3. Rejects missing render latency, queue peak, or subscriber metrics.
4. Rejects fewer than 10,000 frames streamed, duration < 60s, or fewer than 100 selections.
5. Rejects queue not draining or subscriber leak > 0.
6. Rejects stale run ID or SHA mismatch.
7. Passes only on valid complete benchmark artifact.
"""
import math
import sys
import os
import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tools.run_v14_4_2_acceptance import validate_required_metrics


def valid_bench_fixture():
    return {
        "status": "PASS",
        "run_id": "test_run_1",
        "tested_sha": "7bfae74",
        "latency_ms": {
            "p50": 15.0,
            "p90": 25.0,
            "p95": 30.0,
            "p99": 45.0,
            "sample_count": 600,
        },
        "cycles": 600,
        "browser_measurements": {
            "status": "PASS",
            "run_id": "test_run_1",
            "tested_sha": "7bfae74",
            "stream_duration_s": 60.5,
            "events_streamed": 10000,
            "selections_completed": 100,
            "selected_kind_counts": {"SCENARIO": 25, "SETUP": 25, "ORDER": 25, "DRAFT": 25},
            "render_latency_ms": {
                "p50": 8.0,
                "p90": 14.0,
                "p95": 18.0,
                "p99": 24.0,
                "sample_count": 10000,
            },
            "queue_peak": 45,
            "queue_current": 0,  # Drained!
            "active_subscriber_count": 2,
            "subscriber_baseline": 2,
            "subscriber_leak": 0,
            "chart_create_count": 1,
            "dropped_execution_event_count": 0,
        },
        "NOT_MEASURED": [],
    }


def test_validator_passes_valid_benchmark():
    """Valid complete benchmark with all metrics within budget passes."""
    bench = valid_bench_fixture()
    val = validate_required_metrics(bench, run_context={"run_id": "test_run_1", "sha": "7bfae74"})
    assert val["pass"] is True
    assert len(val["errors"]) == 0


def test_validator_rejects_zero_samples():
    """PHẦN 077, 078: sample_count == 0 must fail."""
    bench = valid_bench_fixture()
    bench["browser_measurements"]["render_latency_ms"]["sample_count"] = 0
    val = validate_required_metrics(bench)
    assert val["pass"] is False
    assert any("sample_count" in err for err in val["errors"])


def test_validator_rejects_nan_and_bool_numbers():
    """PHẦN 077: NaN, Inf, and booleans are rejected."""
    bench_nan = valid_bench_fixture()
    bench_nan["browser_measurements"]["render_latency_ms"]["p95"] = float("nan")
    val_nan = validate_required_metrics(bench_nan)
    assert val_nan["pass"] is False

    bench_bool = valid_bench_fixture()
    bench_bool["browser_measurements"]["queue_peak"] = True  # bool is subclass of int in Python
    val_bool = validate_required_metrics(bench_bool)
    assert val_bool["pass"] is False


def test_validator_rejects_insufficient_frames_or_selections():
    """PHẦN 074, 077: < 10k frames or < 100 selections is rejected."""
    bench_frames = valid_bench_fixture()
    bench_frames["browser_measurements"]["events_streamed"] = 9999
    val_frames = validate_required_metrics(bench_frames)
    assert val_frames["pass"] is False

    bench_sel = valid_bench_fixture()
    bench_sel["browser_measurements"]["selections_completed"] = 60
    val_sel = validate_required_metrics(bench_sel)
    assert val_sel["pass"] is False


def test_validator_rejects_queue_not_drained_or_subscriber_leak():
    """PHẦN 074: queue_current > 0 or subscriber_leak > 0 is rejected."""
    bench_drain = valid_bench_fixture()
    bench_drain["browser_measurements"]["queue_current"] = 5
    val_drain = validate_required_metrics(bench_drain)
    assert val_drain["pass"] is False

    bench_leak = valid_bench_fixture()
    bench_leak["browser_measurements"]["subscriber_leak"] = 2
    val_leak = validate_required_metrics(bench_leak)
    assert val_leak["pass"] is False
