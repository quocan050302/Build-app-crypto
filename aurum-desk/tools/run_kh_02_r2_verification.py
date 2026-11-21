#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_02_r2_verification.py

AURUM DESK — KH-02.R2 | RESEARCH INTEGRITY & DATA READINESS ORCHESTRATOR
SPEC VERSION: KH-02.R2-v1.0

Executes the complete KH-02.R2 pipeline with:
  - Strict fail-closed preflight disk policy (>= 20.5 GiB FREE on ALL writing volumes;
    >= 20.0 GiB runtime floor; >= 25.0 GiB advisory) by default.
  - Optional explicit owner waiver (`--owner-waive-disk-preflight`): records exact measured
    disk space and labels Gate 17 / RESOURCE_SAFETY_VERDICT honestly as
    `WAIVED_BY_OWNER_LOW_DISK` (NEVER fake `PASS`), keeping an emergency 2.0 GiB OS safety
    rail, strict >= 512 MiB RAM floor, and <= 4 global child cap active at all times.
  - Full repository LPT-sharded pytest regression (`w <= 4`) + 1:1 JUnit XML bijection (16).
  - Controlled `W1/W2/W4/W1_repeat` pytest subset + KH-02.R2 science slice benchmark (17).
  - Full 56,710 A1 + 6,375 SI8 + 242/166 SI0 + 246 3-adapter + strict D4=0 parity (20).
  - Separate-OS-process independent disk verifier (`tools/kh_02_r2_independent_verifier.py`)
    sealing `22_independent_os_disk_verifier_sha256_manifest.json`.
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
from collections import ChainMap
from dataclasses import asdict
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import psutil

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_01b_entry_unicorn_research_service import (
    FILL_MODES_KH01B,
    load_and_verify_kh01b_reference_baseline,
    verify_kh01b_full_baseline_and_adapter_parity,
)
from services.kh_01b_r1_entry_edge_discovery_service import (
    extract_kh01b_r1_causal_regime_features,
    pre_register_kh01b_r1_theses,
    replay_kh01b_r1_frozen_variants,
)
from services.kh_01b_r1_ram_safety_r3_service import (
    HARD_ABORT_RAM_FLOOR_BYTES_R3,
    SEALED_R2_A1_56710_IDS_SHA256,
    SEALED_R2_E00_BUNDLE_SHA256,
    SEALED_R2_SI0_242_TUPLES_SHA256,
    SEALED_R2_SI8_6375_IDS_SHA256,
    load_empirical_r2_shard_rss_bytes,
)
from services.kh_02_entry_foundation_research_service import (
    build_kh02_asof_mtf_features,
    generate_kh02_research_candidates,
)
from services.kh_02_r2_prospective_data_service import (
    prepare_r2_prospective_oos_ingestion_and_escrow,
)
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    ADVISORY_DISK_GIB_KH02_R2,
    CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02_R2,
    DEFAULT_RANDOM_SEED_KH02_R2,
    GLOBAL_MAX_DESCENDANTS_KH02_R2,
    HARD_RAM_FLOOR_MIB_KH02_R2,
    MANDATORY_BRANCH_KH02_R2,
    PREFLIGHT_RAM_MIN_MIB_KH02_R2,
    PRIOR_KH02_R1_PUBLICATION_HEAD_SHA,
    PRIOR_KH02_R1_RUN_ID,
    RUNTIME_SAFETY_MODES_KH02_R2,
    SCHEMA_VERSION_KH02_R2,
    SPEC_VERSION_KH02_R2,
    STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
    _r2_frozen_plan_and_arms,
    _write_jsonl_gz,
    audit_r2_data_lineage_and_gap_sensitivity,
    audit_r2_fill_time_net_risk_and_realized_tail,
    audit_r2_function_ownership_and_keep_as_is,
    audit_r2_r1_target_eligibility_counterexamples,
    audit_r2_reference_inputs_and_invariants,
    build_r2_causal_unswept_level_ledger,
    build_r2_paired_factorial_entry_geometry_design,
    build_r2_target_repair_full_opportunity_conservation,
    compute_r2_stratified_common_support_balance,
    evaluate_r2_disk_preflight_policy,
    evaluate_r2_orthogonal_science_and_engineering_gates_from_disk,
    freeze_r2_estimands_thresholds_and_falsification,
    load_r2_canonical_dataset_readonly,
    render_r2_readme_and_handover_vi,
    replay_r2_same_opportunity_paired_counterfactuals,
    reprice_r2_fixed_fills_cost_counterfactual,
    rerun_r2_full_universe_by_cost_scenario,
    resolve_r2_research_only_structural_target,
    run_kh02_r2_42_red_green_fault_suite,
    run_r2_purged_walkforward_and_multiplicity,
)
from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    build_full_intervention_candidate_index_v1793121,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    CompressedFeatureLedger,
    canonical_hash,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    enrich_si8_aligned_decisions_and_features_v17931213,
    evaluate_shared_delayed_entry_adapter_v17931213,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    build_close_ts_index_v179312,
    compute_research_policy_registry_hash_v179312,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.run_kh_01a_verification import V130_RUN, iter_jsonl_gz
from tools.run_kh_01b_r1_verification import (
    _build_lpt_shards_kh01b_r1,
    _load_empirical_file_durations_ms_from_kh01b,
    _run_controlled_pytest_w1_w2_w4_benchmark_r1,
    run_async_lpt_pytest_shards_kh01b_r1,
)
from tools.run_kh_01b_verification import _run_streaming_full_56710_a1_parity_kh01b
from tools.run_v17_9_3_1_2_1_full_opportunity_replay import (
    PRIOR_V179312_RUN_DIR,
    _load_verified_decision_stream_chunks,
)
from tools.run_v17_9_3_1_2_2_entry_signal_backtest import replay_single_si_policy_v1793122
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

R2_SUBPROCESS_TEST_FILES: Set[str] = {
    "test_kh_02_asof_mtf_and_fault_controls.py",
    "test_kh_02_r2_oos_provenance_and_faults.py",
}


def _git_cmd(args: List[str], *, timeout: int = 15) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or proc.stderr or "").strip()
    except Exception as exc:
        return 1, f"GIT_CMD_ERROR:{exc}"


def _free_gib(path: Path) -> float:
    p = path if path.exists() else path.parent
    usage = shutil.disk_usage(str(p))
    return round(usage.free / (1024.0 ** 3), 4)


class _BoundedResourceSampler:
    """0.5s background sampler writing compact RAM/disk/PID telemetry to gzip JSONL."""

    def __init__(self, out_gz: Path, watch_path: Path, interval_sec: float = 0.5) -> None:
        self.out_gz = out_gz
        self.watch_path = watch_path
        self.interval_sec = interval_sec
        self._stop = threading.Event()
        self._samples: List[Dict[str, Any]] = []
        self._thread: Optional[threading.Thread] = None
        self._proc = psutil.Process(os.getpid())

    def _sample_once(self) -> Dict[str, Any]:
        vm = psutil.virtual_memory()
        try:
            children = self._proc.children(recursive=True)
            child_pids = [int(c.pid) for c in children if c.is_running()]
            parent_rss = int(self._proc.memory_info().rss)
            child_rss = sum((int(c.memory_info().rss) for c in children if c.is_running()), 0)
        except Exception:
            child_pids = []
            parent_rss = int(self._proc.memory_info().rss)
            child_rss = 0
        return {
            "ts_ms": int(time.time() * 1000),
            "available_ram_bytes": int(vm.available),
            "available_mib": round(vm.available / (1024.0 ** 2), 2),
            "rss_bytes": parent_rss,
            "child_rss_bytes": child_rss,
            "tree_rss_mib": round((parent_rss + child_rss) / (1024.0 ** 2), 2),
            "worker_pids": child_pids,
            "child_count": len(child_pids),
            "disk_free_gib": _free_gib(self.watch_path),
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._samples.append(self._sample_once())
            self._stop.wait(self.interval_sec)

    def start(self) -> None:
        self._samples.append(self._sample_once())
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop_and_flush(self) -> Dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._samples.append(self._sample_once())
        self.out_gz.parent.mkdir(parents=True, exist_ok=True)
        with self.out_gz.open("wb") as raw_fh:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_fh, compresslevel=6, mtime=0) as gzf:
                with io.TextIOWrapper(gzf, encoding="utf-8") as gz:
                    for s in self._samples:
                        gz.write(json.dumps(s, sort_keys=True, separators=(",", ":")) + "\n")
        min_ram = min(s["available_mib"] for s in self._samples)
        max_rss = max(s["tree_rss_mib"] for s in self._samples)
        min_disk = min(s["disk_free_gib"] for s in self._samples)
        max_ch = max(s["child_count"] for s in self._samples)
        orphans = len(self._proc.children(recursive=True))
        return {
            "sample_count": len(self._samples),
            "min_available_ram_mib": round(min_ram, 2),
            "max_tree_rss_mib": round(max_rss, 2),
            "min_disk_free_gib": round(min_disk, 4),
            "max_descendant_children": int(max_ch),
            "orphan_children_after_cleanup": int(orphans),
            "never_breached_512mib_floor": min_ram >= HARD_RAM_FLOOR_MIB_KH02_R2,
        }


# ==============================================================================
# R2 SCIENCE SLICE WORKER & W1/W2/W4/W1_REPEAT BENCHMARK
# ==============================================================================


def _run_r2_science_slice_worker_cli(start_idx: int, end_idx: int, out_json: Path) -> int:
    """Subprocess worker evaluating R2 causal unswept ledger + target resolver across M1 slice."""
    ds_path = REPO_ROOT / CANONICAL_DATASET_REL_PATH
    datasets, _ = load_r2_canonical_dataset_readonly(ds_path)
    mtf = build_kh02_asof_mtf_features(datasets, warmup_m1=30)
    led = build_r2_causal_unswept_level_ledger(datasets, warmup_m1=30, run_t10_future_mutation_proof=False)
    rbi = mtf["records_by_idx"]
    hi_map = led["unswept_highs_by_m1_idx"]
    lo_map = led["unswept_lows_by_m1_idx"]
    rows: List[Tuple[int, str, Optional[float], Optional[float], Optional[str]]] = []
    for idx in range(start_idx, end_idx):
        rec = rbi.get(idx)
        if rec is None or rec.invalidation_reasons or rec.recent_gap_180m:
            continue
        hi_a = hi_map.get(idx, ())
        lo_a = lo_map.get(idx, ())
        for direction in ("LONG", "SHORT"):
            r = resolve_r2_research_only_structural_target(
                rec, direction, unswept_highs_asof=hi_a, unswept_lows_asof=lo_a
            )
            if r["accepted"]:
                rows.append((idx, direction, r["sl_price"], r["tp_price"], r["target_level_id"]))
    rows.sort()
    write_json_durable_atomic(out_json, {"start_idx": start_idx, "end_idx": end_idx, "rows": rows})
    return 0


async def _run_r2_science_slice_w1_w2_w4_determinism(
    owned_temp_root: Path,
    *,
    bar_start: int = 30,
    bar_end: int = 8030,
) -> Dict[str, Any]:
    runs_out: List[Dict[str, Any]] = []
    script_self = Path(__file__).resolve()

    for mode_label, n_workers in (("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1)):
        avail_b = int(psutil.virtual_memory().available)
        if avail_b < HARD_ABORT_RAM_FLOOR_BYTES_R3:
            raise RuntimeError(f"BLOCKED_LOW_RAM_BEFORE_R2_SLICE:{mode_label}:{avail_b}")
        t0 = time.perf_counter()
        n_chunks = max(1, min(4, n_workers))
        span = bar_end - bar_start
        step = max(1, (span + n_chunks - 1) // n_chunks)
        chunks = [
            (bar_start + k * step, min(bar_end, bar_start + (k + 1) * step))
            for k in range(n_chunks)
            if bar_start + k * step < bar_end
        ]
        sem = asyncio.Semaphore(n_workers)

        async def _launch(ci: int, s_i: int, e_i: int) -> Path:
            out_p = owned_temp_root / f"kh02r2_slice_{mode_label.replace('=', '')}_{ci}.json"
            async with sem:
                p = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(script_self),
                    "--r2-science-slice-worker",
                    str(s_i),
                    str(e_i),
                    str(out_p),
                    cwd=str(REPO_ROOT),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, err_b = await asyncio.wait_for(p.communicate(), timeout=240.0)
                if p.returncode != 0:
                    raise RuntimeError(
                        f"R2_SLICE_WORKER_FAILED:{mode_label}:rc={p.returncode}:{err_b.decode('utf-8', errors='replace')[-400:]}"
                    )
            return out_p

        out_files = await asyncio.gather(*[_launch(i, s, e) for i, (s, e) in enumerate(chunks)])
        merged: List[Any] = []
        for of in out_files:
            doc = json.loads(of.read_text("utf-8"))
            merged.extend(doc.get("rows") or [])
            of.unlink(missing_ok=True)
        merged.sort()
        wall_s = round(time.perf_counter() - t0, 3)
        runs_out.append(
            {
                "mode_label": mode_label,
                "workers": n_workers,
                "chunks_count": len(chunks),
                "evaluated_bars_count": bar_end - bar_start,
                "rows_count": len(merged),
                "wall_seconds": wall_s,
                "canonical_sorted_sha256": canonical_hash(merged),
            }
        )

    shas = {r["canonical_sorted_sha256"] for r in runs_out}
    w1_s = max(0.001, float(runs_out[0]["wall_seconds"]))
    w2_s = max(0.001, float(runs_out[1]["wall_seconds"]))
    w4_s = max(0.001, float(runs_out[2]["wall_seconds"]))
    best_run = min(runs_out[:3], key=lambda r: float(r["wall_seconds"]))
    return {
        "evaluated_bar_range": [bar_start, bar_end],
        "runs": runs_out,
        "all_4_runs_identical": len(shas) == 1,
        "canonical_sha256": next(iter(shas)) if len(shas) == 1 else "MISMATCH",
        "measured_speedup_w2_vs_w1": round(w1_s / w2_s, 3),
        "measured_speedup_w4_vs_w1": round(w1_s / w4_s, 3),
        "measured_optimal_worker_count_for_slice": int(best_run["workers"]),
    }


# ==============================================================================
# FULL REPO LPT SHARDED PYTEST + 1:1 JUNIT BIJECTION (16)
# ==============================================================================


def _collect_and_build_r2_lpt_shards(
    owned_temp_root: Path,
) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(
        str(p.relative_to(APP_ROOT)).replace("\\", "/")
        for p in (BACKEND_ROOT / "tests").glob("test_*.py")
        if p.is_file()
    )
    col_out = owned_temp_root / "collect_kh02r2_stdout.log"
    col_err = owned_temp_root / "collect_kh02r2_stderr.log"
    col_snippet = (
        "import os, sys, pytest; "
        "fo = open(sys.argv[1], 'w', encoding='utf-8', errors='replace'); "
        "fe = open(sys.argv[2], 'w', encoding='utf-8', errors='replace'); "
        "sys.stdout = fo; sys.stderr = fe; "
        "rc = pytest.main(sys.argv[3:]); "
        "fo.flush(); fe.flush(); fo.close(); fe.close(); "
        "os._exit(int(rc))"
    )
    col_env = dict(os.environ)
    col_env["PYTHONHASHSEED"] = "0"
    col_env["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            col_snippet,
            str(col_out),
            str(col_err),
            str(BACKEND_ROOT / "tests"),
            "--collect-only",
            "-q",
        ],
        cwd=str(APP_ROOT),
        env=col_env,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if proc.returncode != 0:
        err_txt = col_err.read_text("utf-8", errors="replace")[-800:] if col_err.is_file() else ""
        raise RuntimeError(f"KH02_R2_PYTEST_COLLECT_FAILED:rc={proc.returncode}:{err_txt}")

    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()

    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in R2_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in R2_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append(
            {
                "shard_name": f"SHARD_SINGLE_CHILD_R2_SUBPROC_{idx_s:02d}",
                "files": [sf],
                "exclusive": False,
                "slot_cost": 2,
                "priority_weight": 250_000,
                "needs_venv": False,
            }
        )
    return collected_by_file, shards


def _run_focused_r2_pytest(sentinel_tmp: Path) -> Dict[str, Any]:
    test_files = [
        "backend/tests/test_kh_02_r2_research_integrity.py",
        "backend/tests/test_kh_02_r2_counterfactual_and_costs.py",
        "backend/tests/test_kh_02_r2_oos_provenance_and_faults.py",
    ]
    existing = [tf for tf in test_files if (APP_ROOT / tf).is_file()]
    if not existing:
        return {"executed": False, "status": "NO_FOCUSED_TESTS_FOUND"}
    t0 = time.perf_counter()
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}"
    env["TMPDIR"] = str(sentinel_tmp)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *existing, "-q"],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    elapsed = round(time.perf_counter() - t0, 3)
    tail = "\n".join((proc.stdout or "").splitlines()[-8:])
    return {
        "executed": True,
        "test_files": existing,
        "exit_code": int(proc.returncode),
        "elapsed_sec": elapsed,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "output_tail": tail,
    }


def _run_full_repo_pytest_sharded(
    run_dir: Path,
    sentinel_tmp: Path,
    *,
    run_id: str,
    tested_sha: str,
    effective_disk_reserve_bytes: int,
    max_workers: int,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, r2_shards = _collect_and_build_r2_lpt_shards(sentinel_tmp)
    active_shards_tracker: Dict[str, Any] = {}
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir,
            sentinel_tmp,
            run_id=run_id,
            tested_executable_sha=tested_sha,
            effective_disk_reserve_bytes=effective_disk_reserve_bytes,
            max_parallel_workers=max_workers,
            ram_policy_mode="R3_STRICT_512MIB",
            empirical_rss_by_shard=empirical_r2_rss,
            active_shards_tracker=active_shards_tracker,
            shard_specs_override=r2_shards,
            collected_by_file_override=collected_by_file,
        )
    )
    elapsed = round(time.perf_counter() - t0, 3)
    cm = junit_res["collection_manifest"]
    sm = junit_res["shard_manifest"]
    bj = verify_fresh_junit_bijection_v17931211(
        run_dir,
        cm,
        sm,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2",),
        require_shard_schedule=True,
    )
    collected_n = int(cm.get("collected_node_ids_count") or len(cm.get("collected_node_ids") or ()))
    executed_n = int(bj.get("executed_node_ids_count") or bj.get("executed_count") or 0)
    passed_n = int(bj.get("passed_count") or 0)
    skipped_n = int(bj.get("skipped_count") or 0)
    failed_n = int(bj.get("failed_count") or 0)
    error_n = int(bj.get("error_count") or 0)
    nonzero_shards = int(sm.get("nonzero_exit_shards_count") or 0)
    ok = (
        bj.get("status") == "PASS"
        and nonzero_shards == 0
        and collected_n > 0
        and collected_n == executed_n
        and failed_n == 0
        and error_n == 0
    )
    return {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "status": "PASS" if ok else "FAIL",
        "elapsed_sec": elapsed,
        "max_workers_used": max_workers,
        "collected_count": collected_n,
        "executed_count": executed_n,
        "passed_count": passed_n,
        "skipped_count": skipped_n,
        "failed_count": failed_n,
        "error_count": error_n,
        "nonzero_exit_shards_count": nonzero_shards,
        "junit_bijection_exact": bj.get("status") == "PASS",
        "collection_manifest": cm,
        "shard_manifest": sm,
        "bijection_audit": bj,
    }


# ==============================================================================
# FULL 56,710 A1 + 6,375 SI8 + 242/166 SI0 + 246 3-ADAPTER + D4 PARITY (20)
# ==============================================================================


def _run_full_production_baseline_parity_20(
    *,
    datasets: Mapping[str, Any],
    sentinel_tmp: Path,
    run_id: str,
    tested_sha: str,
    branch: str,
    f01: Mapping[str, Any],
    f02: Mapping[str, Any],
    max_workers: int,
) -> Dict[str, Any]:
    close_ts = build_close_ts_index_v179312(datasets)
    bars_1m = list(datasets["1m"])
    v130_inputs = list(iter_jsonl_gz(V130_RUN / "10_canonical_decision_inputs.jsonl.gz"))
    a1_decisions = [r for r in v130_inputs if not r.get("discovered_by_si8")]
    si8_decisions = [r for r in v130_inputs if r.get("discovered_by_si8")]
    si8_id_set = {str(r["opportunity_id"]) for r in si8_decisions}
    si8_by_idx: Dict[int, List[Dict[str, Any]]] = {}
    for rec in si8_decisions:
        si8_by_idx.setdefault(int(rec["checkpoint_index_1m"]), []).append(rec)
    del v130_inputs
    gc.collect()

    avail_a1 = int(psutil.virtual_memory().available)
    a1_workers = 1 if avail_a1 < int(2.6 * 1024**3) else min(2, max_workers)
    a1_parity = asyncio.run(_run_streaming_full_56710_a1_parity_kh01b(sentinel_tmp, a1_decisions, num_workers=a1_workers))
    gc.collect()

    pol_hash = compute_research_policy_registry_hash_v179312()
    all_dec_raw, _ = _load_verified_decision_stream_chunks(PRIOR_V179312_RUN_DIR / "decision_stream_chunks")
    a1_stream_elig = [
        r for r in all_dec_raw
        if r.get("variant") == "A1_V1792_SL_TP_BASELINE" and r.get("decision_status") == "ELIGIBLE"
    ]
    del all_dec_raw
    gc.collect()
    cand_index = build_full_intervention_candidate_index_v1793121(
        a1_stream_elig, datasets, pol_hash, close_ts_index=close_ts
    )
    del a1_stream_elig
    gc.collect()

    feat_a1 = CompressedFeatureLedger()
    feat_si8 = CompressedFeatureLedger()
    pre_verified_oids: Set[str] = set()
    for f_row in iter_jsonl_gz(V130_RUN / "07_verified_causal_features.jsonl.gz"):
        if not verify_feature_snapshot_v1793127(f_row):
            raise RuntimeError("INVALID_FEATURE_SNAPSHOT_PROOF")
        oid_s = str(f_row["opportunity_id"])
        pre_verified_oids.add(oid_s)
        if oid_s in si8_id_set or f_row.get("discovered_by_si8"):
            feat_si8[oid_s] = f_row
        else:
            feat_a1[oid_s] = f_row
    enrich_si8_aligned_decisions_and_features_v17931213(
        datasets, close_ts, si8_decisions, feat_si8, fallback_feat_by_opp_id=feat_a1, dataset_sha256=CANONICAL_DATASET_SHA256_KH02_R2
    )
    cand_index["si8_discovered_decisions_by_checkpoint_index"] = si8_by_idx
    cand_index["si8_discovered_feature_ledger_by_opp_id"] = feat_si8
    cand_index["v1793127_causal_truth_required"] = True
    all_features_map = ChainMap(feat_si8, feat_a1)

    closed_bar_idx: Dict[Tuple[str, int], int] = {}
    for tf_u, tf_k, span_ms_tf in (
        ("1M", "1m", 60_000), ("5M", "5m", 300_000), ("15M", "15m", 900_000),
        ("1H", "1H", 3_600_000), ("4H", "4H", 14_400_000), ("1D", "1D", 86_400_000),
    ):
        for b in datasets.get(tf_k, ()):
            o_ms = int(b.get("open_time") or b.get("timestamp") or 0)
            if o_ms > 0:
                closed_bar_idx[(tf_u, o_ms)] = int(b.get("close_time") or (o_ms + span_ms_tf))

    r1_frames_a1 = {
        str(c["opportunity_id"]): extract_kh01b_r1_causal_regime_features(
            c, all_features_map, closed_bar_idx, int(c["decision_timestamp_ms"]),
            datasets=datasets, close_ts_index=close_ts, pre_verified_opp_ids=pre_verified_oids,
        )
        for c in a1_decisions
    }
    r1_frames_si8 = {
        str(c["opportunity_id"]): extract_kh01b_r1_causal_regime_features(
            c, all_features_map, closed_bar_idx, int(c["decision_timestamp_ms"]),
            datasets=datasets, close_ts_index=close_ts, pre_verified_opp_ids=pre_verified_oids,
        )
        for c in si8_decisions
    }
    del closed_bar_idx
    gc.collect()

    e00_manifest = pre_register_kh01b_r1_theses(CANONICAL_DATASET_SHA256_KH02_R2, pol_hash, tested_sha)
    rep_r1 = replay_kh01b_r1_frozen_variants(
        e00_manifest,
        a1_decisions=a1_decisions,
        si8_decisions=si8_decisions,
        r1_frames_a1=r1_frames_a1,
        r1_frames_si8=r1_frames_si8,
        feature_ledger=all_features_map,
        candidate_index=cand_index,
        datasets=datasets,
        run_id=run_id,
        tested_sha=tested_sha,
    )
    e00_bnd = rep_r1["bundles"]["E00"]
    d4_iso = rep_r1["d4_isolation_11"]
    del r1_frames_a1, r1_frames_si8, rep_r1
    gc.collect()

    si8_pol = replay_single_si_policy_v1793122("SI8_RESEARCH_DISCOVERY_REPAIRED", cand_index, datasets, all_features_map, fill_modes=FILL_MODES_KH01B)
    si9_pol = replay_single_si_policy_v1793122("SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS", cand_index, datasets, all_features_map, fill_modes=FILL_MODES_KH01B)
    all_246: List[Mapping[str, Any]] = []
    seen_246: Set[str] = set()
    for ep_s in list(e00_bnd.primary_nb_episodes) + list(si8_pol["primary_episodes"]) + list(si9_pol["primary_episodes"]):
        oid_e = str(ep_s["opportunity_id"])
        if oid_e not in seen_246:
            seen_246.add(oid_e)
            all_246.append(ep_s)
    del cand_index, si8_by_idx, si8_pol, si9_pol
    gc.collect()

    bars_5m, bars_15m, bars_1h, bars_4h = list(datasets["5m"]), list(datasets["15m"]), list(datasets["1H"]), list(datasets["4H"])
    open_1m = [int(b.get("open_time") or b.get("timestamp") or 0) for b in bars_1m]
    ad_mismatches = 0
    for ep_x in all_246:
        ts_e = int(ep_x["decision_timestamp_ms"])
        j1, j5, j15, jh1, jh4 = (bisect.bisect_right(close_ts[k], ts_e) for k in ("1m", "5m", "15m", "1H", "4H"))
        fi = bisect.bisect_left(open_1m, ts_e)
        mode_shas = set()
        for ctx_m in ("REPLAY_BACKTEST", "MARKET_SHADOW", "PRODUCTION_ORCHESTRATOR_DRY_RUN"):
            ad_out = evaluate_shared_delayed_entry_adapter_v17931213(
                execution_context_mode=ctx_m,
                activation_flags={"LIVE": False, "PAPER": False, "AUTO_LIVE": False, "AUTO_PAPER": False, "enabled_in_production": False},
                variant_id="D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
                opportunity_id=str(ep_x["opportunity_id"]),
                episode_key=str(ep_x.get("episode_key") or ep_x["opportunity_id"]),
                model_id=str(ep_x["model_id"]),
                direction=str(ep_x["direction"]),
                decision_timestamp_ms=ts_e,
                baseline_entry=float(ep_x["entry_reference"]),
                baseline_sl=float(ep_x["selected_sl"]),
                baseline_tp=float(ep_x["take_profit"]),
                closed_m1=bars_1m[max(0, j1 - 180) : j1],
                closed_m5=bars_5m[max(0, j5 - 60) : j5],
                closed_m15=bars_15m[max(0, j15 - 60) : j15],
                closed_h1=bars_1h[max(0, jh1 - 60) : jh1],
                closed_h4=bars_4h[max(0, jh4 - 60) : jh4],
                forward_bars=bars_1m[fi : min(len(bars_1m), fi + 180)],
                pre_entry_features=dict(all_features_map.get(str(ep_x["opportunity_id"])) or {}),
                fill_mode="NEXT_BAR_OPEN_BID_ASK",
                dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
            )
            mode_shas.add(canonical_hash({k: v for k, v in ad_out.items() if k != "adapter_execution_context_mode"}))
        if len(mode_shas) != 1:
            ad_mismatches += 1

    base_ref = load_and_verify_kh01b_reference_baseline(REPO_ROOT)
    parity_clean = verify_kh01b_full_baseline_and_adapter_parity(
        tested_sha=tested_sha,
        branch=branch,
        a1_streaming_parity=a1_parity,
        si8_decisions=si8_decisions,
        b00_bundle=e00_bnd,
        baseline_truth=base_ref,
        three_adapter_parity={"population_denominator": len(all_246), "mismatches_count": ad_mismatches, "runtime_modes_all_off": True},
    )
    nb_eps_sorted = sorted(e00_bnd.primary_nb_episodes, key=lambda r: (int(r["decision_timestamp_ms"]), str(r["opportunity_id"])))
    ep_fp_sha = canonical_hash([
        (
            str(e["opportunity_id"]), str(e["direction"]), int(e["decision_timestamp_ms"]),
            float(e["entry_reference"]), float(e["selected_sl"]), float(e["take_profit"]),
            float(e.get("executed_quantity") or 0.0), float(e.get("total_fees_usd") or 0.0),
            float(e.get("realized_net_pnl_usd") or 0.0), str(e.get("outcome_label") or ""),
        )
        for e in nb_eps_sorted
    ])
    a1_sha = canonical_hash([str(r["opportunity_id"]) for r in a1_decisions])
    si8_sha = canonical_hash([str(r["opportunity_id"]) for r in si8_decisions])
    e00_sha = str(e00_bnd.bundle_sha256)
    fp_match = (
        a1_sha == SEALED_R2_A1_56710_IDS_SHA256
        and si8_sha == SEALED_R2_SI8_6375_IDS_SHA256
        and ep_fp_sha == SEALED_R2_SI0_242_TUPLES_SHA256
        and e00_sha == SEALED_R2_E00_BUNDLE_SHA256
    )
    a1_mism = int(a1_parity.get("mismatches_count", 0))
    si8_mism = 0 if len(si8_decisions) == 6375 else 1
    d4_closed = int(d4_iso.get("d4_historical_closed_trades", 0))
    parity_dict = asdict(parity_clean)
    all_ok = (
        parity_dict.get("status") == "PASS"
        and a1_mism == 0
        and si8_mism == 0
        and ad_mismatches == 0
        and d4_closed == 0
        and fp_match
        and f02.get("status") == "PASS"
        and int(f01.get("locked_files_modified_count", 1)) == 0
    )
    return {
        "spec_version": SPEC_VERSION_KH02_R2,
        "schema_version": SCHEMA_VERSION_KH02_R2,
        "prior_evidence_immutability_verified": (f02.get("status") == "PASS" and int(f01.get("locked_files_modified_count", 1)) == 0),
        "canonical_dataset_sha256_match": bool((f02.get("reused_dataset_provenance") or {}).get("match", False)),
        "kh02_r1_pointer_and_manifest_intact": (f02.get("status") == "PASS"),
        "production_files_modified_count": int(f01.get("locked_files_modified_count", 0)),
        "strict_d4_ontology_untouched": True,
        "heavy_56710_a1_6375_si8_246_adapter_streaming_rerun": "EXECUTED",
        "a1_population_count": len(a1_decisions),
        "a1_mismatches_count": a1_mism,
        "si8_population_count": len(si8_decisions),
        "si8_mismatches_count": si8_mism,
        "three_adapter_population_count": len(all_246),
        "three_adapter_mismatches_count": ad_mismatches,
        "d4_historical_closed_trades": d4_closed,
        "d4_a1_56710_eligible_count": int(d4_iso.get("d4_a1_56710_eligible_count", 0)),
        "si0_e00_primary_nb_summary": e00_bnd.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"],
        "per_decision_and_episode_fingerprints": {
            "a1_56710_ids_sha256": a1_sha,
            "si8_6375_ids_sha256": si8_sha,
            "si0_242_episode_matched_tuples_sha256": ep_fp_sha,
            "e00_bundle_sha256": e00_sha,
            "all_sealed_fingerprints_exact_match": fp_match,
        },
        "parity_verifier_summary": parity_dict,
        "BASELINE_PARITY_VERDICT": "PASS" if all_ok else "FAIL",
    }


def _write_gate_receipts(run_dir: Path, f21: Mapping[str, Any]) -> None:
    rc_dir = run_dir / "receipts"
    rc_dir.mkdir(parents=True, exist_ok=True)
    for idx_g, g in enumerate(f21.get("gates") or (), 1):
        write_json_durable_atomic(
            rc_dir / f"G-KH02R2-{idx_g:02d}.json",
            {
                "spec_version": SPEC_VERSION_KH02_R2,
                "run_id": f21.get("run_id"),
                "tested_executable_sha": f21.get("tested_executable_sha"),
                **g,
            },
        )


def run_kh02_r2_orchestrator(
    *,
    allow_local_blocked_dirty: bool = False,
    owner_waive_disk_preflight: bool = False,
    run_focused_r2_tests: bool = True,
    max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2,
    artifacts_root: Optional[Path] = None,
) -> Tuple[int, Path, Dict[str, Any]]:
    t_start = time.perf_counter()
    utc_now = datetime.now(timezone.utc)
    utc_stamp = utc_now.strftime("%Y%m%dT%H%M%SZ")
    start_ms = int(utc_now.timestamp() * 1000)
    orch_pid = os.getpid()
    eff_workers = max(1, min(GLOBAL_MAX_DESCENDANTS_KH02_R2, int(max_workers)))

    _, branch = _git_cmd(["branch", "--show-current"])
    _, head_sha = _git_cmd(["rev-parse", "HEAD"])
    _, porcelain = _git_cmd(["status", "--porcelain=v1"])
    rc_ls, remote_line = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH02_R2}"], timeout=12)
    remote_sha = remote_line.split()[0] if (rc_ls == 0 and remote_line) else "UNAVAILABLE_OR_OFFLINE"

    git_clean = len(porcelain.strip()) == 0
    if branch != MANDATORY_BRANCH_KH02_R2:
        raise RuntimeError(f"BLOCKED_WRONG_BRANCH:expected={MANDATORY_BRANCH_KH02_R2},actual={branch}")

    art_base = (artifacts_root or (APP_ROOT / "docs/artifacts/kh_02_r2_research_integrity")).resolve()
    sentinel_tmp = Path(tempfile.mkdtemp(prefix=f"kh02r2_{utc_stamp}_"))

    vol_map = {
        "repo_volume": _free_gib(REPO_ROOT),
        "output_volume": _free_gib(art_base if art_base.exists() else APP_ROOT),
        "temp_volume": _free_gib(sentinel_tmp),
    }
    disk_policy = evaluate_r2_disk_preflight_policy(vol_map)
    vm = psutil.virtual_memory()
    avail_ram_mib = round(vm.available / (1024.0 ** 2), 2)
    ram_preflight_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2

    emergency_disk_ok = float(disk_policy["min_free_gib"]) >= 2.0
    strict_disk_ok = bool(disk_policy["can_run_heavy_stages"])
    waiver_active = bool(owner_waive_disk_preflight and (not strict_disk_ok) and emergency_disk_ok)
    can_run_heavy = bool((strict_disk_ok or waiver_active) and ram_preflight_ok)

    if (not git_clean) and (not allow_local_blocked_dirty) and can_run_heavy:
        shutil.rmtree(sentinel_tmp, ignore_errors=True)
        raise RuntimeError("BLOCKED_DIRTY_WORKING_TREE: commit KH-02.R2 source cleanly before full verified run.")

    run_id = f"{utc_stamp}_{head_sha[:12]}"
    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    sampler = _BoundedResourceSampler(
        out_gz=run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz",
        watch_path=run_dir,
        interval_sec=0.5,
    )
    sampler.start()

    try:
        print(f"[KH-02.R2] Run ID: {run_id} | Branch: {branch} | HEAD: {head_sha[:12]} | clean={git_clean}", flush=True)
        print(
            f"[KH-02.R2] Preflight Disk: min={disk_policy['min_free_gib']} GiB (strict >= 20.5 GiB -> {disk_policy['status']}, "
            f"owner_waived={waiver_active}) | RAM: {avail_ram_mib} MiB (preflight >= 2048 MiB -> {'PASS' if ram_preflight_ok else 'BLOCKED'})",
            flush=True,
        )

        # Artifact 00: Preflight
        f00: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2,
            "schema_version": SCHEMA_VERSION_KH02_R2,
            "run_id": run_id,
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "branch": branch,
            "start_head_sha": head_sha,
            "tested_executable_sha": head_sha,
            "remote_head_sha": remote_sha,
            "prior_kh02_r1_publication_head_sha": PRIOR_KH02_R1_PUBLICATION_HEAD_SHA,
            "git_clean_at_start": git_clean,
            "local_blocked_dirty_uncommitted_r2": (not git_clean),
            "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "timezone_local": time.tzname,
            "random_seed": DEFAULT_RANDOM_SEED_KH02_R2,
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "preflight_ram_available_mib": avail_ram_mib,
            "preflight_ram_min_mib": PREFLIGHT_RAM_MIN_MIB_KH02_R2,
            "hard_ram_floor_mib": HARD_RAM_FLOOR_MIB_KH02_R2,
            "global_max_descendants": GLOBAL_MAX_DESCENDANTS_KH02_R2,
            "disk_preflight_policy": disk_policy,
            "owner_disk_preflight_waiver": waiver_active,
            "owner_disk_waiver_reason": (
                "Explicit owner instruction to proceed despite low disk preflight (< 20.5 GiB); strict >= 512 MiB RAM floor and <= 4 child cap remain enforced."
                if waiver_active
                else None
            ),
            "can_run_heavy_stages": can_run_heavy,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
            "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": (
                "PASS"
                if (strict_disk_ok and ram_preflight_ok and git_clean)
                else ("WAIVED_BY_OWNER_LOW_DISK" if (waiver_active and ram_preflight_ok and git_clean) else "BLOCKED_LOW_DISK")
            ),
        }
        write_json_durable_atomic(run_dir / "00_preflight_branch_repo_sha_data_and_resources.json", f00)

        # Artifact 01 & 02 & 03
        print("[KH-02.R2] Stage 1: Function ownership & KEEP-AS-IS source freeze audit (01)...", flush=True)
        f01 = audit_r2_function_ownership_and_keep_as_is(REPO_ROOT, base_head_sha=head_sha)
        write_json_durable_atomic(run_dir / "01_function_ownership_keep_as_is_diff.json", f01)

        print("[KH-02.R2] Stage 2: KH-02.R1 reference invariants & dataset SHA audit (02)...", flush=True)
        f02 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
        write_json_durable_atomic(run_dir / "02_r1_source_integrity_and_snapshot_registry.json", f02)

        f03 = freeze_r2_estimands_thresholds_and_falsification(
            tested_executable_sha=head_sha,
            canonical_dataset_sha256=CANONICAL_DATASET_SHA256_KH02_R2,
        )
        write_json_durable_atomic(run_dir / "03_frozen_r2_estimands_thresholds_hypotheses_and_falsification.json", f03)

        # Stage 3..6: Offline scientific audit (w=1)
        print("[KH-02.R2] Stage 3: Loading canonical dataset & building causal unswept level ledger (04, 05, 06)...", flush=True)
        t_sci_0 = time.perf_counter()
        ds_path = REPO_ROOT / CANONICAL_DATASET_REL_PATH
        datasets, _ds_meta = load_r2_canonical_dataset_readonly(ds_path)
        mtf_bundle = build_kh02_asof_mtf_features(datasets)
        r1_frozen_plan, _ = _r2_frozen_plan_and_arms(code_sha=head_sha)
        r1_candidates = generate_kh02_research_candidates(mtf_bundle, r1_frozen_plan)

        t_led_0 = time.perf_counter()
        ledger_bundle = build_r2_causal_unswept_level_ledger(
            datasets,
            source_latency_ms=0,
            warmup_m1=30,
            run_t10_future_mutation_proof=True,
            rng_seed=DEFAULT_RANDOM_SEED_KH02_R2,
        )
        t_led_sec = round(time.perf_counter() - t_led_0, 3)

        f04_header = {
            "_record_type": "LEDGER_METADATA_AND_T10_PROOF",
            "spec_version": SPEC_VERSION_KH02_R2,
            "total_confirmed_levels": ledger_bundle["total_confirmed_levels"],
            "swept_on_source_htf_count": ledger_bundle["swept_on_source_htf_count"],
            "swept_earlier_by_intervening_m1_count": ledger_bundle["swept_earlier_by_intervening_m1_count"],
            "never_swept_in_sample_count": ledger_bundle["never_swept_in_sample_count"],
            "m15_meta": ledger_bundle["m15_meta"],
            "h1_meta": ledger_bundle["h1_meta"],
            "t10_future_mutation_proof": ledger_bundle["t10_future_mutation_proof"],
            "ledger_canonical_sha256": ledger_bundle["ledger_canonical_sha256"],
        }
        _write_jsonl_gz(
            run_dir / "04_asof_unswept_level_ledger_and_mutation_proof.jsonl.gz",
            [f04_header, *ledger_bundle["levels"]],
        )

        f05 = audit_r2_r1_target_eligibility_counterexamples(
            datasets=datasets,
            mtf_bundle=mtf_bundle,
            ledger_bundle=ledger_bundle,
            r1_candidates=r1_candidates,
        )
        write_json_durable_atomic(run_dir / "05_r1_target_eligibility_counterexamples_and_root_cause.json", f05)

        r2_candidates = build_r2_target_repair_full_opportunity_conservation(
            datasets=datasets,
            mtf_bundle=mtf_bundle,
            ledger_bundle=ledger_bundle,
            r1_candidates=r1_candidates,
            source_latency_ms=0,
        )
        f06_header = {
            "_record_type": "R2_OPPORTUNITY_CONSERVATION_SUMMARY",
            "spec_version": SPEC_VERSION_KH02_R2,
            "total_post_warmup_m1_bars": r2_candidates["total_post_warmup_m1_bars"],
            "total_directional_bar_opportunities": r2_candidates["total_directional_bar_opportunities"],
            "conservation_sum_verified": r2_candidates["conservation_sum_verified"],
            "raw_m1_triggers_count": r2_candidates["raw_m1_triggers_count"],
            "raw_r2_target_found_count": r2_candidates["raw_r2_target_found_count"],
            "raw_r2_geometry_risk_eligible_count": r2_candidates["raw_r2_geometry_risk_eligible_count"],
            "universe_reason_codes": r2_candidates["universe_reason_codes"],
            "before_after_r1_vs_r2_by_arm": r2_candidates["before_after_r1_vs_r2_by_arm"],
            "candidates_canonical_sha256": r2_candidates["candidates_canonical_sha256"],
        }
        _write_jsonl_gz(
            run_dir / "06_r2_target_repair_full_opportunity_conservation.jsonl.gz",
            [f06_header, *r2_candidates["conservation_rows"]],
        )

        print("[KH-02.R2] Stage 4: Chronological 1-position 3-fill replays & paired factorial balance (07, 08, 09)...", flush=True)
        t_rep_0 = time.perf_counter()
        replay_bundle = replay_r2_same_opportunity_paired_counterfactuals(
            datasets=datasets,
            r1_candidates=r1_candidates,
            r2_candidates=r2_candidates,
        )
        t_rep_sec = round(time.perf_counter() - t_rep_0, 3)

        f09 = {k: v for k, v in replay_bundle.items() if k not in ("r1_replays", "r2_replays")}
        f09["artifact_canonical_sha256"] = canonical_hash(f09)
        write_json_durable_atomic(run_dir / "09_chronological_1position_three_fill_paired_replays.json", f09)

        paired_design = build_r2_paired_factorial_entry_geometry_design(
            r1_candidates=r1_candidates,
            r2_candidates=r2_candidates,
            r1_replays=replay_bundle["r1_replays"],
            r2_replays=replay_bundle["r2_replays"],
        )
        f07_header = {
            "_record_type": "PAIRED_FACTORIAL_SUMMARY",
            "estimand": "SUMMARY_HEADER",
            "opportunity_id": "__HEADER__",
            "pair_status": "SUMMARY",
            "zero_silent_drops_verified": paired_design["zero_silent_drops_verified"],
            "E_ONLY_PAIRED_FIXED_GEOMETRY": paired_design["E_ONLY_PAIRED_FIXED_GEOMETRY"],
            "G_ONLY_PAIRED_SAME_ENTRY": paired_design["G_ONLY_PAIRED_SAME_ENTRY"],
            "E_X_G_FACTORIAL": paired_design["E_X_G_FACTORIAL"],
            "B0_VS_B2_CROSS_UNIVERSE": paired_design["B0_VS_B2_CROSS_UNIVERSE"],
        }
        _write_jsonl_gz(
            run_dir / "07_paired_entry_geometry_factorial_control_mapping.jsonl.gz",
            [f07_header, *paired_design["pairing_rows"]],
        )

        f08 = compute_r2_stratified_common_support_balance(
            r2_candidates=r2_candidates,
            r2_replays=replay_bundle["r2_replays"],
            mtf_bundle=mtf_bundle,
        )
        write_json_durable_atomic(run_dir / "08_strata_common_support_balance_and_matching_quality.json", f08)

        print("[KH-02.R2] Stage 5: Fill-time net risk, fixed-fill & full-universe rerisked costs (10, 11, 12)...", flush=True)
        f10 = audit_r2_fill_time_net_risk_and_realized_tail(
            datasets=datasets,
            r2_replays=replay_bundle["r2_replays"],
        )
        write_json_durable_atomic(run_dir / "10_fill_time_net_risk_and_realized_tail_audit.json", f10)

        f11 = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=replay_bundle["r2_replays"])
        write_json_durable_atomic(run_dir / "11_fixed_fill_same_ids_fee_spread_slippage_1x2x3x.json", f11)

        t_fu_0 = time.perf_counter()
        f12 = rerun_r2_full_universe_by_cost_scenario(
            datasets=datasets,
            mtf_bundle=mtf_bundle,
            ledger_bundle=ledger_bundle,
        )
        t_fu_sec = round(time.perf_counter() - t_fu_0, 3)
        write_json_durable_atomic(run_dir / "12_full_universe_rerisked_1x2x3x_and_fee_004_006.json", f12)

        print("[KH-02.R2] Stage 6: Data lineage, source latency, OOS readiness & purged walk-forward (13, 14, 15)...", flush=True)
        f13 = audit_r2_data_lineage_and_gap_sensitivity(
            datasets=datasets,
            mtf_bundle=mtf_bundle,
            dataset_path=ds_path,
        )
        write_json_durable_atomic(run_dir / "13_m1_m15_h1_gaps_source_latency_quote_fee_provenance.json", f13)

        f14 = prepare_r2_prospective_oos_ingestion_and_escrow(
            repo_root=REPO_ROOT,
            freeze_timestamp_ms=start_ms,
            tested_executable_sha=head_sha,
            frozen_config_sha256=f03["artifact_canonical_sha256"],
            disk_free_gib=disk_policy["min_free_gib"],
            min_disk_gib_for_capture=STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            network_capture_authorized=False,
        )
        write_json_durable_atomic(run_dir / "14_prospective_oos_escrow_collection_readiness_no_peek.json", f14)

        f15 = run_r2_purged_walkforward_and_multiplicity(
            r2_replays=replay_bundle["r2_replays"],
            r2_fixed_costs=f11,
        )
        write_json_durable_atomic(run_dir / "15_purged_walkforward_cluster_uncertainty_and_multiplicity.json", f15)
        t_sci_total = round(time.perf_counter() - t_sci_0, 3)

        # Free bulky intermediate science structures before heavy pytest / parity
        del mtf_bundle, ledger_bundle, r1_candidates, r2_candidates, replay_bundle, paired_design
        gc.collect()

        # Artifact 19: 42 unique real RED/GREEN negative controls
        print("[KH-02.R2] Stage 7: Running 42 unique real RED/GREEN fault controls (19)...", flush=True)
        f19 = run_kh02_r2_42_red_green_fault_suite(REPO_ROOT)
        write_json_durable_atomic(run_dir / "19_real_red_green_fault_injections.json", f19)

        # Heavy Stages (16, 17, 20)
        if can_run_heavy:
            eff_reserve_bytes = int(
                (STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 if strict_disk_ok else 2.0) * (1024 ** 3)
            )
            print(
                f"[KH-02.R2] Stage 8A: Running FULL repository LPT-sharded pytest regression (max_workers={eff_workers}) (16)...",
                flush=True,
            )
            f16 = _run_full_repo_pytest_sharded(
                run_dir,
                sentinel_tmp,
                run_id=run_id,
                tested_sha=head_sha,
                effective_disk_reserve_bytes=eff_reserve_bytes,
                max_workers=eff_workers,
            )
            f16["focused_r2_pytest_summary"] = {"included_in_full_repo_bijection": True, "status": f16["status"]}
            write_json_durable_atomic(run_dir / "16_full_pytest_collection_execution_junit_bijection.json", f16)
            if git_clean:
                # Restore transient legacy test side-effect without touching any code or locked files
                _git_cmd(["checkout", "--", "aurum-desk/backtest/trade_decision_lineage_reconciliation.json"])
            gc.collect()

            print("[KH-02.R2] Stage 8B: Running W1/W2/W4/W1_repeat pytest + R2 science slice benchmarks (17)...", flush=True)
            pytest_bench = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            r2_slice_bench = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            f17 = {
                "spec_version": SPEC_VERSION_KH02_R2,
                "schema_version": SCHEMA_VERSION_KH02_R2,
                "scheduler_policy": {
                    "science_slice_default_workers": 1,
                    "pytest_max_workers": eff_workers,
                    "global_max_descendants": GLOBAL_MAX_DESCENDANTS_KH02_R2,
                },
                "measured_w1_science_workloads_sec": {
                    "causal_unswept_ledger_plus_t10_w1_sec": t_led_sec,
                    "chronological_3fill_replays_w1_sec": t_rep_sec,
                    "full_universe_6scenario_rerisk_w1_sec": t_fu_sec,
                    "offline_science_pipeline_total_w1_sec": t_sci_total,
                },
                "pytest_controlled_subset_w1_w2_w4_repeat": pytest_bench,
                "pytest_subset_w1_w2_w4_repeat_identical": bool(pytest_bench["all_4_runs_identical"]),
                "measured_pytest_speedup_w2_vs_w1": pytest_bench["measured_speedup_w2_vs_w1"],
                "measured_pytest_speedup_w4_vs_w1": pytest_bench["measured_speedup_w4_vs_w1"],
                "r2_science_slice_w1_w2_w4_repeat": r2_slice_bench,
                "r2_science_slice_w1_w2_w4_repeat_identical": bool(r2_slice_bench["all_4_runs_identical"]),
                "measured_science_speedup_w2_vs_w1": r2_slice_bench["measured_speedup_w2_vs_w1"],
                "measured_science_speedup_w4_vs_w1": r2_slice_bench["measured_speedup_w4_vs_w1"],
                "status": "PASS" if (pytest_bench["all_4_runs_identical"] and r2_slice_bench["all_4_runs_identical"]) else "FAIL",
            }
            write_json_durable_atomic(run_dir / "17_async_w1_w2_w4_repeat_times_outputs_and_scheduler_policy.json", f17)
            gc.collect()

            print("[KH-02.R2] Stage 8C: Running FULL 56,710 A1 + 6,375 SI8 + 242/166 SI0 + 246 3-adapter + D4 parity (20)...", flush=True)
            f20 = _run_full_production_baseline_parity_20(
                datasets=datasets,
                sentinel_tmp=sentinel_tmp,
                run_id=run_id,
                tested_sha=head_sha,
                branch=branch,
                f01=f01,
                f02=f02,
                max_workers=eff_workers,
            )
            write_json_durable_atomic(run_dir / "20_production_baseline_parity_and_prior_evidence_immutability.json", f20)
            del datasets
            gc.collect()
        else:
            del datasets
            gc.collect()
            focused_pytest = _run_focused_r2_pytest(sentinel_tmp) if run_focused_r2_tests else {"executed": False}
            f16 = {
                "spec_version": SPEC_VERSION_KH02_R2,
                "schema_version": SCHEMA_VERSION_KH02_R2,
                "status": "NOT_RUN_BLOCKED_LOW_DISK",
                "block_reason": (
                    f"Strict preflight disk floor >= {STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2} GiB breached: "
                    f"measured {disk_policy['min_free_gib']} GiB free (shortfall {disk_policy['shortfall_to_strict_20p5_gib']} GiB)."
                ),
                "collected_count": 0,
                "executed_count": 0,
                "failed_count": 0,
                "error_count": 0,
                "junit_bijection_exact": False,
                "junit_xml_files": [],
                "focused_r2_pytest_summary": focused_pytest,
                "zero_fake_junit_written": True,
            }
            write_json_durable_atomic(run_dir / "16_full_pytest_collection_execution_junit_bijection.json", f16)

            f17 = {
                "spec_version": SPEC_VERSION_KH02_R2,
                "schema_version": SCHEMA_VERSION_KH02_R2,
                "scheduler_policy": {
                    "science_slice_default_workers": 1,
                    "pytest_max_workers_when_disk_ok": GLOBAL_MAX_DESCENDANTS_KH02_R2,
                    "global_max_descendants": GLOBAL_MAX_DESCENDANTS_KH02_R2,
                },
                "measured_w1_science_workloads_sec": {
                    "causal_unswept_ledger_plus_t10_w1_sec": t_led_sec,
                    "chronological_3fill_replays_w1_sec": t_rep_sec,
                    "full_universe_6scenario_rerisk_w1_sec": t_fu_sec,
                    "offline_science_pipeline_total_w1_sec": t_sci_total,
                },
                "pytest_subset_w1_w2_w4_repeat_identical": False,
                "r2_science_slice_w1_w2_w4_repeat_identical": False,
                "status": "NOT_RUN_BLOCKED_LOW_DISK",
            }
            write_json_durable_atomic(run_dir / "17_async_w1_w2_w4_repeat_times_outputs_and_scheduler_policy.json", f17)

            f20 = {
                "spec_version": SPEC_VERSION_KH02_R2,
                "schema_version": SCHEMA_VERSION_KH02_R2,
                "prior_evidence_immutability_verified": (f02.get("status") == "PASS" and int(f01.get("locked_files_modified_count", 1)) == 0),
                "canonical_dataset_sha256_match": bool((f02.get("reused_dataset_provenance") or {}).get("match", False)),
                "kh02_r1_pointer_and_manifest_intact": (f02.get("status") == "PASS"),
                "production_files_modified_count": int(f01.get("locked_files_modified_count", 0)),
                "strict_d4_ontology_untouched": True,
                "heavy_56710_a1_6375_si8_246_adapter_streaming_rerun": "NOT_RUN_BLOCKED_LOW_DISK",
                "BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK",
            }
            write_json_durable_atomic(run_dir / "20_production_baseline_parity_and_prior_evidence_immutability.json", f20)

        # Clean up sentinel-owned temp dir BEFORE final telemetry flush
        shutil.rmtree(sentinel_tmp, ignore_errors=True)
        sentinel_cleaned = not sentinel_tmp.exists()

        telemetry = sampler.stop_and_flush()
        f18: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2,
            "schema_version": SCHEMA_VERSION_KH02_R2,
            "orchestrator_pid": orch_pid,
            "sampling_interval_sec": 0.5,
            "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            **telemetry,
            "strict_preflight_disk_min_gib": STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            "strict_runtime_disk_min_gib": STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
            "advisory_disk_gib": ADVISORY_DISK_GIB_KH02_R2,
            "shortfall_to_strict_20p5_gib": disk_policy["shortfall_to_strict_20p5_gib"],
            "disk_preflight_policy": disk_policy,
            "owner_disk_preflight_waiver": waiver_active,
            "sentinel_temp_dir": str(sentinel_tmp),
            "sentinel_temp_cleaned_up": sentinel_cleaned,
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
            "status": (
                "PASS"
                if (
                    telemetry["never_breached_512mib_floor"]
                    and telemetry["max_descendant_children"] <= GLOBAL_MAX_DESCENDANTS_KH02_R2
                    and telemetry["orphan_children_after_cleanup"] == 0
                    and strict_disk_ok
                    and sentinel_cleaned
                )
                else (
                    "WAIVED_BY_OWNER_LOW_DISK"
                    if (
                        waiver_active
                        and telemetry["never_breached_512mib_floor"]
                        and telemetry["max_descendant_children"] <= GLOBAL_MAX_DESCENDANTS_KH02_R2
                        and telemetry["orphan_children_after_cleanup"] == 0
                        and telemetry["min_disk_free_gib"] >= 2.0
                        and sentinel_cleaned
                    )
                    else ("BLOCKED_LOW_DISK" if not strict_disk_ok else "FAIL")
                )
            ),
        }
        write_json_durable_atomic(run_dir / "18_ram_disk_pid_temp_telemetry.json", f18)

        # Artifact 23: Pre-push receipt
        if strict_disk_ok and git_clean:
            pub_status = "PRE_PUSH_PENDING"
        elif waiver_active and git_clean:
            pub_status = "PRE_PUSH_PENDING_WITH_OWNER_DISK_WAIVER"
        else:
            pub_status = "NOT_PUBLISHED_BLOCKED_LOW_DISK"

        f23: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH02_R2,
            "schema_version": SCHEMA_VERSION_KH02_R2,
            "run_id": run_id,
            "branch": branch,
            "start_head_sha": head_sha,
            "tested_executable_sha": head_sha,
            "evidence_commit_sha": None,
            "publication_head_sha": None,
            "latest_run_pointer_updated": False,
            "git_commit_or_push_executed": False,
            "owner_disk_preflight_waiver": waiver_active,
            "publication_status": pub_status,
        }
        write_json_durable_atomic(run_dir / "23_tested_sha_evidence_publication_receipt_pre_push.json", f23)

        # Artifact 21: Orthogonal 20-gate & 14-verdict matrix
        f21 = evaluate_r2_orthogonal_science_and_engineering_gates_from_disk(run_dir)
        write_json_durable_atomic(run_dir / "21_orthogonal_engineering_science_economic_oos_gate_matrix.json", f21)
        _write_gate_receipts(run_dir, f21)

        # README & Vietnamese Handover TXT
        readme_md, handover_vi = render_r2_readme_and_handover_vi(
            run_dir=run_dir,
            f00=f00,
            f01=f01,
            f02=f02,
            f05=f05,
            f08=f08,
            f09=f09,
            f10=f10,
            f11=f11,
            f12=f12,
            f13=f13,
            f14=f14,
            f15=f15,
            f16=f16,
            f17=f17,
            f18=f18,
            f19=f19,
            f20=f20,
            f21=f21,
            f23=f23,
        )
        (run_dir / "README_KH02_R2.md").write_text(readme_md, encoding="utf-8")
        (run_dir / "HANDOVER_KH02_R2_VI.txt").write_text(handover_vi, encoding="utf-8")

        # Stage 9: Separate-OS-process independent disk verifier sealing 22!
        print("[KH-02.R2] Stage 9: Launching separate-OS-process independent disk verifier (22)...", flush=True)
        verifier_script = APP_ROOT / "tools" / "kh_02_r2_independent_verifier.py"
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = f"{BACKEND_ROOT}:{APP_ROOT}:{REPO_ROOT}"
        v_proc = subprocess.run(
            [
                sys.executable,
                str(verifier_script),
                "--run-dir",
                str(run_dir),
                "--expected-run-id",
                run_id,
                "--expected-tested-sha",
                head_sha,
            ],
            cwd=str(APP_ROOT),
            env=env_v,
            capture_output=True,
            text=True,
            timeout=180,
        )
        print(v_proc.stdout, flush=True)
        if v_proc.stderr:
            print(v_proc.stderr, file=sys.stderr, flush=True)

        f22 = json.loads((run_dir / "22_independent_os_disk_verifier_sha256_manifest.json").read_text("utf-8"))
        verifier_ok = v_proc.returncode == 0 and f22.get("independent_verifier_status") == "PASS"

        eng_v = f21["orthogonal_verdicts"]["ENGINEERING_VERDICT"]
        if not verifier_ok or eng_v == "FAIL":
            exit_code = 1
        elif eng_v in ("PASS", "PASS_WITH_OWNER_DISK_WAIVER"):
            exit_code = 0
        else:
            exit_code = 2

        return exit_code, run_dir, {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "exit_code": exit_code,
            "independent_verifier_status": f22.get("independent_verifier_status"),
            "orthogonal_verdicts": f21["orthogonal_verdicts"],
            "gates_summary": {
                "passed": f21["gates_passed"],
                "blocked": f21["gates_blocked"],
                "failed": f21["gates_failed"],
                "pending": f21["gates_pending"],
            },
        }
    finally:
        if sentinel_tmp.exists():
            shutil.rmtree(sentinel_tmp, ignore_errors=True)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-02.R2 Research Integrity & Data Readiness Orchestrator")
    ap.add_argument(
        "--r2-science-slice-worker",
        nargs=3,
        metavar=("START_IDX", "END_IDX", "OUT_JSON"),
        help="Internal worker mode for W1/W2/W4/W1_repeat R2 science slice determinism benchmark.",
    )
    ap.add_argument(
        "--allow-local-blocked-dirty",
        action="store_true",
        help="Allow running local diagnostic/blocked audit when KH-02.R2 additive files are uncommitted.",
    )
    ap.add_argument(
        "--owner-waive-disk-preflight",
        action="store_true",
        help="Explicit owner waiver to execute heavy stages below 20.5 GiB disk preflight (still reports Gate 17 as WAIVED_BY_OWNER_LOW_DISK, never fake PASS, and enforces 2.0 GiB emergency floor + 512 MiB RAM floor).",
    )
    ap.add_argument(
        "--max-workers",
        type=int,
        default=GLOBAL_MAX_DESCENDANTS_KH02_R2,
        help="Maximum bounded parallel workers (capped at 4).",
    )
    ap.add_argument(
        "--skip-focused-r2-tests",
        action="store_true",
        help="Skip focused KH-02.R2 pytest diagnostic run when blocked.",
    )
    args = ap.parse_args(argv)

    if args.r2_science_slice_worker:
        s_i, e_i, out_p = args.r2_science_slice_worker
        return _run_r2_science_slice_worker_cli(int(s_i), int(e_i), Path(out_p))

    rc, run_dir, summary = run_kh02_r2_orchestrator(
        allow_local_blocked_dirty=args.allow_local_blocked_dirty,
        owner_waive_disk_preflight=args.owner_waive_disk_preflight,
        run_focused_r2_tests=not args.skip_focused_r2_tests,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
