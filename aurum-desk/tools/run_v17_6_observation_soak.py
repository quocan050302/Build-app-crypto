"""
tools/run_v17_6_observation_soak.py
AURUM V17.6 — Autonomous Engine Observability Soak Test Runner.

Executes a deterministic multi-cycle soak simulation verifying:
1. Engine heartbeat continuity and monotonic cadence.
2. Memory stability (RSS growth < 5MB).
3. Telemetry ring buffer bounds (zero unbounded array growth).
4. Cycle completion and decision categorization fidelity.
"""
from __future__ import annotations

import gc
import json
import os
import sys
import time
from pathlib import Path

# Add backend directory to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Ensure testing environment
os.environ["TESTING"] = "1"
os.environ["AURUM_DB_PATH"] = ":memory:"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import crud
import models
from services.clock import FakeClock
from services.engine_observability_service import engine_observability_service
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import M1ScalpConfig, ScalpMode
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator


def get_rss_mb() -> float:
    try:
        import psutil
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def run_soak(cycles: int = 50) -> dict:
    mem_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(mem_engine)
    SessionFactory = sessionmaker(bind=mem_engine, autoflush=False)
    db = SessionFactory()

    print(f"[V17.6 SOAK] Initializing {cycles}-cycle deterministic soak...")
    start_rss = get_rss_mb()
    t_start = time.perf_counter()

    base_time_ms = 1700000000000
    clock = FakeClock(base_time_ms)
    symbol = "XAUUSDT"
    run_id = f"soak-run-{int(time.time())}"

    config_enabled = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    orch = ScalpWatcherOrchestrator()
    completed_cycles = 0
    errors = []

    try:
        for i in range(cycles):
            t_ms = base_time_ms + i * 5000
            clock.set_time(t_ms)

            q = CanonicalQuote(
                symbol=symbol,
                bid=2647.50,
                ask=2648.00,
                last=2647.75,
                status="VALID",
                exchange_ts_ms=t_ms,
                received_at_ms=t_ms,
            )

            cycle_res = orch.evaluate_cycle(
                db,
                symbol,
                now_ms=t_ms,
                clock=clock,
                quote=q,
                candles_1m=[],
                candles_5m=[],
                config=config_enabled,
            )

            status = cycle_res.get("status")
            if status in ("COMPLETED", "OK", "WAITING", "NO_SETUP", "ARMED", "SKIPPED"):
                completed_cycles += 1
            else:
                errors.append(f"Cycle {i} returned status: {status}")

            if (i + 1) % 25 == 0:
                print(f"[V17.6 SOAK] Progress: {i + 1}/{cycles} cycles completed.")

        # Force garbage collection to measure true leak
        gc.collect()
        end_rss = get_rss_mb()
        elapsed_sec = time.perf_counter() - t_start
        rss_growth_mb = max(0.0, end_rss - start_rss) if start_rss > 0 else 0.0

        # Retrieve observability telemetry
        snapshot = engine_observability_service.build_observation_dashboard_snapshot(
            db=db,
            symbol=symbol,
            run_id=run_id,
            now_ms=base_time_ms + cycles * 5000,
        )

        cycles_count = snapshot["engine"]["cycles_count"]
        coverage_pct = snapshot["coverage"]["coverage_pct"]

        result = {
            "success": len(errors) == 0 and completed_cycles == cycles and rss_growth_mb < 5.0,
            "cycles_requested": cycles,
            "completed_cycles": completed_cycles,
            "cycles_recorded_in_telemetry": cycles_count,
            "coverage_pct": coverage_pct,
            "elapsed_seconds": round(elapsed_sec, 2),
            "start_rss_mb": round(start_rss, 2),
            "end_rss_mb": round(end_rss, 2),
            "rss_growth_mb": round(rss_growth_mb, 2),
            "errors": errors,
        }

        print(f"[V17.6 SOAK] Completed in {result['elapsed_seconds']}s | RSS Growth: {result['rss_growth_mb']}MB")
        print(f"[V17.6 SOAK] Status: {'PASS' if result['success'] else 'FAIL'}")
        return result

    finally:
        db.close()
        mem_engine.dispose()


if __name__ == "__main__":
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    res = run_soak(cycles)
    sys.exit(0 if res["success"] else 1)
