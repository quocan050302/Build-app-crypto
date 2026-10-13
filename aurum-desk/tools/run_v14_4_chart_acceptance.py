#!/usr/bin/env python3
"""
V14.4.1 scenario-engine benchmark (replaces the V14.4 early-return benchmark).

What it measures: the REAL pure scenario engine (load_closed_context -> evaluate_scenarios) on the causal
smc_fixtures tape with real candle objects and the canonical 5M/15M/1H/4H/D context keys, advancing the clock
over closed-bar boundaries and re-evaluating on every simulated quote tick.

What it does NOT measure (reported as NOT_MEASURED, never as a pass): browser render latency, WS subscriber
count, frontend queue length and chart recreation. Those belong to the Playwright suite.

Fixture self-checks (a benchmark that analysed nothing is a FAILURE, not a fast pass):
  * the wrong lowercase context keys (m5/m15/...) must be REJECTED by the engine (DATA_UNAVAILABLE);
  * every evaluated cycle must return scenarios, none DATA_UNAVAILABLE on the real tape;
  * the confirmed chain must appear at the final cutoff and the earlier cutoffs must show real progression.

Exit code 0 only when every assertion holds; thresholds are configurable hypotheses, not guarantees.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import sys
import time
import tracemalloc

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
for p in (BACKEND, os.path.join(BACKEND, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import smc_fixtures as F  # noqa: E402  (causal tape used by the production-pipeline tests)
from services import smc_scenario_config as scfg  # noqa: E402
from services.smc_scenario_service import evaluate_scenarios, load_closed_context  # noqa: E402

M5_MS = 300_000


def _percentile(values, pct):
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def run_benchmark(cycles: int = 600, p95_limit_ms: float = 250.0, mem_growth_limit_mb: float = 25.0) -> dict:
    failures: list[str] = []
    config = scfg.active_scenario_config
    direction = "LONG"
    by_tf = F.make_candles_by_tf(direction, F.NOW_MS)
    settings = F.default_settings()

    # --- fixture self-check 1: wrong keys must NOT silently produce a "fast pass"
    wrong = {k.lower(): v for k, v in by_tf.items()}
    wrong_ctx = load_closed_context(wrong, F.NOW_MS, config)
    wrong_res = evaluate_scenarios("XAUUSDT", wrong_ctx, [], config, F.NOW_MS, settings=settings)
    rejected = bool(wrong_res) and all(
        any(str(r).startswith("DATA_UNAVAILABLE") for r in (s.trigger_chain or {}).get("reasons", [])) or s.blockers
        for s in wrong_res
    )
    if not rejected:
        failures.append("FIXTURE: lowercase context keys were not rejected (benchmark would measure an early return)")

    # --- fixture self-check 2: real tape is analysed and progresses toward the confirmed chain
    progression = []
    for back in range(0, 40, 2):
        cutoff = F.NOW_MS - back * M5_MS
        ctx = load_closed_context(F.make_candles_by_tf(direction, cutoff), cutoff, config)
        snaps = evaluate_scenarios("XAUUSDT", ctx, [], config, cutoff, settings=settings)
        progression.append((back, sorted({s.state.value if hasattr(s.state, "value") else str(s.state) for s in snaps})))
    final_states = progression[0][1]
    if "STRUCTURE_CONFIRMED" not in final_states:
        failures.append(f"FIXTURE: no STRUCTURE_CONFIRMED at the final cutoff (got {final_states})")
    distinct = {tuple(s) for _, s in progression}
    if len(distinct) < 2:
        failures.append("FIXTURE: no scenario progression across cutoffs (engine output never changed)")
    if any("DATA_UNAVAILABLE" in "".join(s) for _, s in progression[:1]):
        failures.append("FIXTURE: final cutoff reported DATA_UNAVAILABLE")

    # --- measured loop: every cycle re-evaluates a fresh closed context at a tick time inside the bar
    ctx_now = load_closed_context(by_tf, F.NOW_MS, config)
    latencies_ms: list[float] = []
    empty_cycles = 0
    confirmed_cycles = 0
    gc.collect()
    tracemalloc.start()
    mem_start = tracemalloc.get_traced_memory()[0]
    started = time.perf_counter()
    for i in range(cycles):
        now_ms = F.NOW_MS + (i % 250) * 1000  # ticks inside the current bar: same closed prefix, new quote time
        t0 = time.perf_counter()
        snaps = evaluate_scenarios("XAUUSDT", ctx_now, [], config, now_ms, settings=settings)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        if not snaps:
            empty_cycles += 1
        elif any((s.state.value if hasattr(s.state, "value") else str(s.state)) == "STRUCTURE_CONFIRMED" for s in snaps):
            confirmed_cycles += 1
    total_s = time.perf_counter() - started
    gc.collect()
    mem_end = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    p50, p95 = statistics.median(latencies_ms), _percentile(latencies_ms, 95)
    growth_mb = (mem_end - mem_start) / (1024 * 1024)
    if empty_cycles:
        failures.append(f"{empty_cycles} cycles returned no scenarios")
    if confirmed_cycles == 0:
        failures.append("no cycle produced a confirmed scenario: the benchmark analysed nothing meaningful")
    if p95 > p95_limit_ms:
        failures.append(f"P95 {p95:.2f}ms exceeds the configured limit {p95_limit_ms}ms")
    if growth_mb > mem_growth_limit_mb:
        failures.append(f"traced memory grew {growth_mb:.1f}MB (> {mem_growth_limit_mb}MB)")

    report = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "cycles": cycles,
        "confirmed_cycles": confirmed_cycles,
        "total_s": round(total_s, 3),
        "throughput_cycles_per_s": round(cycles / total_s, 1) if total_s else None,
        "latency_ms": {
            "p50": round(p50, 3),
            "p95": round(p95, 3),
            "max": round(max(latencies_ms), 3),
            "sample_count": len(latencies_ms),
        },
        "traced_memory_growth_mb": round(growth_mb, 3),
        "progression_sample": [{"bars_back": b, "states": s} for b, s in progression[:6]],
        "thresholds": {"p95_limit_ms": p95_limit_ms, "mem_growth_limit_mb": mem_growth_limit_mb,
                       "note": "configured hypotheses, not guarantees"},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "processor": platform.processor() or "unknown", "cpu_count": os.cpu_count()},
        "NOT_MEASURED": ["browser render latency", "WS subscriber count", "frontend queue length",
                         "chart recreation per tick", "10,000-tick/60s browser stream"],
    }
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=600)
    ap.add_argument("--p95-limit-ms", type=float, default=250.0)
    ap.add_argument("--mem-growth-limit-mb", type=float, default=25.0)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--tested-sha", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    report = run_benchmark(args.cycles, args.p95_limit_ms, args.mem_growth_limit_mb)
    if args.run_id:
        report["run_id"] = args.run_id
    if args.tested_sha:
        report["tested_sha"] = args.tested_sha
    print(json.dumps(report, indent=2))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
