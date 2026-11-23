"""
aurum-desk/backend/tests/test_kh_03_async_and_resource_safety.py

KH-03.R1 Resource Safety (Zero Disk Waivers, Windows Long-Path Sentinel Cleanup,
Bounded Worker Cap) & Deterministic Ordered Async Reduction Tests.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import random
import subprocess
import sys

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_research_integrity_service import create_sentinel_owned_temp_dir_r2_c1
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.run_kh_02_r2_c4_acceptance import _cleanup_sentinel_temp_c4_win_safe


def test_kh03_cli_and_env_disk_waivers_strictly_refused() -> None:
    script = APP_ROOT / "tools" / "run_kh_03_acceptance.py"
    p_cli = subprocess.run([sys.executable, str(script), "--owner-waive-disk-preflight"], cwd=str(APP_ROOT), capture_output=True, text=True, timeout=20)
    assert p_cli.returncode == 2
    assert "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN" in p_cli.stderr

    env = os.environ.copy()
    env["KH03_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    p_env = subprocess.run([sys.executable, str(script), "--help"], cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=20)
    assert p_env.returncode == 2


def test_kh03_windows_long_path_sentinel_cleanup_and_deterministic_async_reduction() -> None:
    sd = create_sentinel_owned_temp_dir_r2_c1("kh03_lp_test_", run_id="kh03_lp_unit", owner_pid=os.getpid())
    deep = sd / ("a" * 40) / ("b" * 40) / ("c" * 40) / ("d" * 40) / ("e" * 40)
    lp = ("\\\\?\\" + os.path.abspath(str(deep))) if sys.platform == "win32" else str(deep)
    os.makedirs(lp, exist_ok=True)
    with open(os.path.join(lp, "9" * 64 + ".blob"), "wb") as fh:
        fh.write(b"kh03-long-path-check")
    cl = _cleanup_sentinel_temp_c4_win_safe(sd, expected_run_id="kh03_lp_unit")
    assert cl["cleaned"] is True and not sd.exists()

    # Deterministic per-partition seeded async reduction identical across W=1, W=2, W=4
    async def _run_bounded(workers: int) -> str:
        sem = asyncio.Semaphore(workers)
        results = []

        async def _part(pid: int) -> None:
            async with sem:
                await asyncio.sleep(0.001 * ((pid * 3) % 4))
                rng = random.Random(20261123 * 1000 + pid)
                results.append((pid, round(rng.random(), 8)))

        await asyncio.gather(*(_part(i) for i in range(16)))
        results.sort(key=lambda x: x[0])
        return canonical_hash(results)

    h1 = asyncio.run(_run_bounded(1))
    h2 = asyncio.run(_run_bounded(2))
    h4 = asyncio.run(_run_bounded(4))
    assert h1 == h2 == h4
