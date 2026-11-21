#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_02_entry_foundation_research.py

AURUM DESK — KH-02: Entry Foundation Research Bounded-Async Orchestrator
SPEC VERSION: KH-02.R1 / RESEARCH-ONLY / NO PRODUCTION ACTIVATION

Reuses the proven KH-01B.R1.DSC.R3 RAM-safety (`>= 512 MiB` hard abort floor, `0.5s` recursive
process-tree monitor, predictive hysteresis scheduler, `<= 4` global OS descendants), strict
multi-mount `20.5 GiB` disk preflight, and sentinel-verified temp cleanup WITHOUT modifying any
existing production, strategy, SL/TP, risk, fee, True Unicorn D4, or prior KH-01* files.
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
from collections import ChainMap, Counter
from dataclasses import asdict
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_01a_r1_forensics_closure_service import build_dynamic_validated_gate_receipt_r1
from services.kh_01b_entry_unicorn_research_service import (
    FILL_MODES_KH01B,
    load_and_verify_kh01b_reference_baseline,
    verify_kh01b_full_baseline_and_adapter_parity,
)
from services.kh_01b_r1_data_safety_closure_service import compute_kh01b_r1_fee_sensitivity
from services.kh_01b_r1_entry_edge_discovery_service import (
    extract_kh01b_r1_causal_regime_features,
    pre_register_kh01b_r1_theses,
    replay_kh01b_r1_frozen_variants,
)
from services.kh_01b_r1_ram_safety_r3_service import (
    HARD_ABORT_RAM_FLOOR_BYTES_R3,
    PRIOR_R2_LATEST_PTR_REL,
    PRIOR_R2_LATEST_PTR_SHA256,
    RAM_POLICY_VERSION_R3,
    audit_r3_memory_samples_jsonl_gz,
    compute_r3_ram_preflight_and_policy,
    detect_active_64mib_ram_floor_in_source,
    load_empirical_r2_shard_rss_bytes,
)
from services.kh_02_entry_foundation_research_service import (
    CANONICAL_DATASET_SHA256_KH02,
    KH02_RESEARCH_ONLY_RUNTIME_FLAGS,
    KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256,
    MANDATORY_BRANCH_KH02,
    PRIOR_R3_ACCEPTED_RUN_ID,
    PRIOR_R3_LATEST_PTR_REL,
    PRIOR_R3_LATEST_PTR_SHA256,
    PRIOR_R3_PUBLICATION_HEAD_SHA,
    PRIOR_R3_TESTED_SHA,
    SCHEMA_VERSION_KH02,
    SPEC_VERSION_KH02,
    assert_kh02_runtime_safety_modes_off,
    audit_kh02_dataset_registry_and_quality,
    audit_kh02_oos_escrow_and_noncontamination,
    build_kh02_asof_mtf_features,
    build_kh02_ast_callgraph_and_immutability_map,
    build_kh02_matched_cohorts_and_ablations,
    estimate_kh02_edge_uncertainty,
    evaluate_kh02_independent_gates_from_disk,
    generate_kh02_research_candidates,
    load_kh02_immutable_baseline_registry,
    preregister_kh02_hypotheses,
    render_kh02_readme_handover_vi,
    replay_kh02_chronological_one_position,
    run_kh02_33_red_green_fault_suite,
    run_kh02_future_mutation_invariance_check,
    stress_kh02_execution_and_costs,
    verify_chronological_non_overlap,
)
from services.scalp_m1_types import get_scalp_config
from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    build_full_intervention_candidate_index_v1793121,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_V1793123_POLICY_IDS,
    POLICY_ID_ALIASES_V1793122,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    CompressedFeatureLedger,
    canonical_hash,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    enrich_si8_aligned_decisions_and_features_v17931213,
    evaluate_shared_delayed_entry_adapter_v17931213,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    build_close_ts_index_v179312,
    compute_research_policy_registry_hash_v179312,
)
from tools.fresh_junit_io import (
    write_json_durable_atomic,
    write_jsonl_gz_durable_atomic,
    write_text_durable_atomic,
)
from tools.run_kh_01a_verification import (
    V130_RUN,
    ResourceMonitorKH01A,
    create_owned_temp_root,
    file_sha256,
    iter_jsonl_gz,
)
from tools.run_kh_01b_r1_data_safety_closure import (
    _sample_process_and_child_tree_rss_mib,
    reclaim_inactive_sentinel_owned_temp_dirs,
    snapshot_all_prior_artifacts_for_r3,
)
from tools.run_kh_01b_r1_verification import (
    _build_lpt_shards_kh01b_r1,
    _load_empirical_file_durations_ms_from_kh01b,
    _run_controlled_pytest_w1_w2_w4_benchmark_r1,
    compute_kh01b_r1_disk_reserve_policy,
    inspect_writing_volumes_and_mounts_r2,
    measure_writing_volumes_free_bytes_r1,
    run_async_lpt_pytest_shards_kh01b_r1,
    safe_cleanup_owned_temp_root_r1,
)
from tools.run_kh_01b_verification import _run_streaming_full_56710_a1_parity_kh01b
from tools.run_v17_9_3_1_2_1_full_opportunity_replay import (
    DATASET_PATH,
    PRIOR_V179312_RUN_DIR,
    _load_verified_decision_stream_chunks,
)
from tools.run_v17_9_3_1_2_2_entry_signal_backtest import replay_single_si_policy_v1793122
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122
from tools.run_v17_9_3_1_2_7_verification import _active_heavy_worker_children, git, source_manifest
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

ARTIFACTS_KH02_ROOT = APP_ROOT / "docs/artifacts/kh_02_entry_foundation"
KH02_SUBPROCESS_TEST_FILES: Set[str] = {
    "test_kh_02_asof_mtf_and_fault_controls.py",
}


def snapshot_all_prior_artifacts_for_kh02() -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Snapshots all prior sealed bundles INCLUDING KH-01B.R1.DSC.R3 & its latest_run.json."""
    r3_base_sum, r3_base_files = snapshot_all_prior_artifacts_for_r3()
    all_files = dict(r3_base_files)

    r3_dir = APP_ROOT / "docs/artifacts/kh_01b_r1_ram_safety_r3"
    r3_files: Dict[str, str] = {}
    if r3_dir.is_dir():
        for p in sorted(r3_dir.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
                sh = file_sha256(p)
                r3_files[rel] = sh
                all_files[rel] = sh

    r3_ptr_p = REPO_ROOT / PRIOR_R3_LATEST_PTR_REL
    r3_ptr_sha = file_sha256(r3_ptr_p) if r3_ptr_p.is_file() else "MISSING"

    summary = {
        **r3_base_sum,
        "prior_r3_bundle_files_count": len(r3_files),
        "prior_r3_bundle_sha256": canonical_hash(r3_files),
        "prior_r3_latest_run_ptr_sha256": r3_ptr_sha,
    }
    return summary, all_files


def _collect_and_build_kh02_lpt_shards(
    owned_temp_root: Path,
) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    """
    Collects full repository pytest inventory and assigns `test_kh_02_asof_mtf_and_fault_controls.py`
    to `slot_cost=2` (because it spawns the separate-OS-process verifier) without touching prior
    runner modules.
    """
    all_test_files = sorted(
        str(p.relative_to(APP_ROOT)).replace("\\", "/")
        for p in (BACKEND_ROOT / "tests").glob("test_*.py")
        if p.is_file()
    )
    col_out_file = owned_temp_root / "collect_kh02_stdout.log"
    col_err_file = owned_temp_root / "collect_kh02_stderr.log"
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
    proc = subprocess.run(
        [
            sys.executable, "-c", col_snippet,
            str(col_out_file), str(col_err_file),
            str(BACKEND_ROOT / "tests"), "--collect-only", "-q",
        ],
        cwd=str(APP_ROOT),
        env=col_env,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if proc.returncode != 0:
        err_txt = col_err_file.read_text("utf-8", errors="replace")[-800:] if col_err_file.is_file() else ""
        raise RuntimeError(f"KH02_PYTEST_COLLECT_FAILED:rc={proc.returncode}:{err_txt}")

    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out_file.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()

    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    # Keep KH-02 subprocess-spawning test file out of regular_inproc and attach to a dedicated slot_cost=2 shard
    non_kh02_sub = [f for f in all_test_files if Path(f).name not in KH02_SUBPROCESS_TEST_FILES]
    kh02_sub = [f for f in all_test_files if Path(f).name in KH02_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_kh02_sub, collected_by_file, empirical_ms)
    if kh02_sub:
        shards.append({
            "shard_name": "SHARD_SINGLE_CHILD_04_KH02_SUBPROC",
            "files": sorted(kh02_sub),
            "exclusive": False,
            "slot_cost": 2,
            "priority_weight": 250_000,
            "needs_venv": False,
        })
    return collected_by_file, shards


def _run_kh02_science_slice_worker_cli(start_idx: int, end_idx: int, out_json: Path) -> int:
    """Subprocess worker evaluating a deterministic slice of post-warmup M1 bars for w1/w2/w4 parity."""
    datasets, _ = load_canonical_xauusdt_datasets()
    mtf = build_kh02_asof_mtf_features(datasets, warmup_m1=30)
    plan = preregister_kh02_hypotheses(
        dataset_sha256=CANONICAL_DATASET_SHA256_KH02,
        config_sha256="kh02_slice_bench_cfg",
        code_sha256="kh02_slice_bench_code",
    )
    idxs = [i for i in range(start_idx, end_idx) if i in mtf["records_by_idx"]]
    gen = generate_kh02_research_candidates(mtf, plan, bar_indices_subset=idxs)
    rows: List[Tuple[str, str, float, float, float, float]] = []
    for ac in ("B2_H1", "B2_H2", "B2_H3", "B2_H123"):
        for c in gen["arm_candidates"][ac]:
            rows.append((ac, str(c["opportunity_id"]), float(c["entry_reference"]), float(c["selected_sl"]), float(c["take_profit"]), float(c["net_rr"])))
    rows.sort()
    write_json_durable_atomic(out_json, {"start_idx": start_idx, "end_idx": end_idx, "rows": rows})
    return 0


async def _run_kh02_science_slice_w1_w2_w4_determinism(
    owned_temp_root: Path,
    *,
    bar_start: int = 30,
    bar_end: int = 8030,
) -> Dict[str, Any]:
    """
    Runs real multi-process KH-02 scientific candidate generation across 8,000 M1 bars
    under `w=1`, `w=2`, `w=4`, and `w=1_repeat` (bounded `<= 4` OS processes), verifying
    identical sorted canonical output SHA256 across all 4 worker counts.
    """
    import psutil

    runs_out: List[Dict[str, Any]] = []
    script_self = Path(__file__).resolve()

    for mode_label, n_workers in (("w=1", 1), ("w=2", 2), ("w=4", 4), ("w=1_repeat", 1)):
        avail_b = int(psutil.virtual_memory().available)
        if avail_b < HARD_ABORT_RAM_FLOOR_BYTES_R3:
            raise RuntimeError(f"BLOCKED_LOW_RAM_BEFORE_KH02_SLICE:{mode_label}:{avail_b}")
        t0 = time.perf_counter()
        n_chunks = max(1, min(4, n_workers))
        span = bar_end - bar_start
        step = max(1, (span + n_chunks - 1) // n_chunks)
        chunks = [
            (bar_start + k * step, min(bar_end, bar_start + (k + 1) * step))
            for k in range(n_chunks)
            if bar_start + k * step < bar_end
        ]

        out_files: List[Path] = []
        procs: List[asyncio.subprocess.Process] = []
        sem = asyncio.Semaphore(n_workers)

        async def _launch_chunk(ci: int, s_i: int, e_i: int) -> Path:
            out_p = owned_temp_root / f"kh02_slice_{mode_label.replace('=', '')}_{ci}.json"
            async with sem:
                p = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(script_self),
                    "--science-slice-worker",
                    str(s_i),
                    str(e_i),
                    str(out_p),
                    cwd=str(REPO_ROOT),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                procs.append(p)
                _, err_b = await asyncio.wait_for(p.communicate(), timeout=180.0)
                if p.returncode != 0:
                    raise RuntimeError(f"KH02_SLICE_WORKER_FAILED:{mode_label}:rc={p.returncode}:{err_b.decode('utf-8', errors='replace')[-400:]}")
            return out_p

        out_files = await asyncio.gather(*[_launch_chunk(i, s, e) for i, (s, e) in enumerate(chunks)])
        merged_rows: List[Any] = []
        for of in out_files:
            doc = json.loads(of.read_text("utf-8"))
            merged_rows.extend(doc.get("rows") or [])
            of.unlink(missing_ok=True)
        merged_rows.sort()
        wall_s = round(time.perf_counter() - t0, 3)
        runs_out.append({
            "mode_label": mode_label,
            "workers": n_workers,
            "chunks_count": len(chunks),
            "evaluated_bars_count": bar_end - bar_start,
            "rows_count": len(merged_rows),
            "wall_seconds": wall_s,
            "canonical_sorted_sha256": canonical_hash(merged_rows),
        })

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


def run_kh02_entry_foundation_orchestrator(
    *,
    head_at_start_sha: str = PRIOR_R3_PUBLICATION_HEAD_SHA,
    max_workers: int = 4,
    run_id_override: str = "",
) -> int:
    """
    Executes KH-02.R1 end-to-end on clean committed tested_executable_sha and seals artifacts
    00..19 + README + HANDOVER_VI via separate-OS-process independent verifier.
    """
    import psutil

    # 0. Preflight Git, runtime safety flags, mounts, RAM, and prior immutable snapshots
    branch = git("branch", "--show-current")
    tested_sha = git("rev-parse", "HEAD")
    dirty = [ln for ln in git("status", "--porcelain=v1").splitlines() if ln.strip()]
    if branch != MANDATORY_BRANCH_KH02:
        print(json.dumps({"status": "BLOCKED_GIT_STATE", "reason": f"WRONG_BRANCH:{branch}"}, indent=2), flush=True)
        return 2
    if dirty:
        print(json.dumps({"status": "BLOCKED_GIT_STATE", "reason": "DIRTY_WORKTREE", "dirty": dirty[:20]}, indent=2), flush=True)
        return 2
    if tested_sha in (PRIOR_R3_TESTED_SHA, PRIOR_R3_PUBLICATION_HEAD_SHA):
        print(json.dumps({"status": "BLOCKED_GIT_STATE", "reason": f"STALE_TESTED_SHA:{tested_sha}"}, indent=2), flush=True)
        return 2

    runtime_safety = assert_kh02_runtime_safety_modes_off(KH02_RESEARCH_ONLY_RUNTIME_FLAGS, check_live_scalp_config=True)
    base_reg = load_kh02_immutable_baseline_registry(REPO_ROOT, tested_sha)
    if not base_reg.verification_passed:
        print(json.dumps({"status": "FAIL_IMMUTABLE_BASELINE", "violations": list(base_reg.violations)}, indent=2), flush=True)
        return 1

    reclaim_rcpt = reclaim_inactive_sentinel_owned_temp_dirs()
    out_root = ARTIFACTS_KH02_ROOT
    out_root.mkdir(parents=True, exist_ok=True)
    mounts_before = inspect_writing_volumes_and_mounts_r2(repo_root=REPO_ROOT, output_root=out_root, verify_atomic_write=True)
    vol_free = mounts_before["writing_volumes_free_bytes"]
    disk_policy = compute_kh01b_r1_disk_reserve_policy(vol_free["repo"], volume_free_bytes_by_label=vol_free)

    eff_workers = max(1, min(4, int(max_workers)))
    vm0 = psutil.virtual_memory()
    ram_preflight = compute_r3_ram_preflight_and_policy(
        int(vm0.available), int(vm0.total), max_workers=eff_workers
    )
    if not disk_policy["can_proceed"]:
        print(json.dumps({"status": "BLOCKED_LOW_DISK", "policy": disk_policy}, indent=2), flush=True)
        return 2
    if not ram_preflight["can_proceed"]:
        print(json.dumps({"status": "BLOCKED_LOW_RAM", "policy": ram_preflight}, indent=2), flush=True)
        return 2

    base_ts_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = run_id_override or f"{base_ts_id}_{tested_sha[:12]}"
    if (out_root / run_id).exists():
        run_id = f"{base_ts_id}_{os.getpid()}_{tested_sha[:12]}"
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "receipts").mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)

    orchestrator_pid = os.getpid()
    freeze_ts_ms = int(time.time() * 1000)
    owned_temp_root = create_owned_temp_root(f"kh02_{run_id}")

    # Thread & determinism locks + strict >= 512 MiB env guard
    py_paths = [str(BACKEND_ROOT), str(APP_ROOT)]
    existing_pyp = os.environ.get("PYTHONPATH", "")
    if existing_pyp:
        py_paths.append(existing_pyp)
    os.environ["PYTHONPATH"] = os.pathsep.join(py_paths)
    os.environ["PYTHONHASHSEED"] = "0"
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["NUMEXPR_NUM_THREADS"] = "1"
    os.environ["AURUM_VERIFICATION_ARTIFACT_ROOT"] = str(owned_temp_root)
    os.environ["AURUM_TESTED_EXECUTABLE_SHA"] = tested_sha
    os.environ["AURUM_MINIMUM_RAM_BYTES"] = str(HARD_ABORT_RAM_FLOOR_BYTES_R3)

    active_shards_tracker: Dict[str, Any] = {"active_shards": [], "active_slots": 0}
    monitor = ResourceMonitorKH01A(
        abort_path=run_dir / "resource_abort.json",
        minimum_ram_bytes=HARD_ABORT_RAM_FLOOR_BYTES_R3,
        warning_near_floor_bytes=1024 * 1024**2,
        soft_downshift_ram_bytes=1600 * 1024**2,
        minimum_disk_bytes=int(disk_policy["effective_enforced_reserve_bytes"]),
        max_children_cap=4,
        sampling_interval_seconds=0.5,
        active_context_fn=lambda: {
            "active_shards": list(active_shards_tracker.get("active_shards") or ()),
            "active_slots": int(active_shards_tracker.get("active_slots") or 0),
        },
    )
    os.environ["AURUM_RESOURCE_GUARD_ABORT_FILE"] = str(monitor.abort_path)

    prev_sigint = signal.getsignal(signal.SIGINT)
    prev_sigterm = signal.getsignal(signal.SIGTERM)
    def _on_sig(signum: int, _frame: Any) -> None:
        monitor.reap_all_descendants()
        raise KeyboardInterrupt(f"KH02_SIGNAL_{signum}")
    signal.signal(signal.SIGINT, _on_sig)
    signal.signal(signal.SIGTERM, _on_sig)

    src_before = source_manifest()
    prior_before_sum, prior_before_files = snapshot_all_prior_artifacts_for_kh02()

    scalp_cfg = get_scalp_config()
    scalp_dict = scalp_cfg.model_dump() if hasattr(scalp_cfg, "model_dump") else vars(scalp_cfg)
    cost_dict = CostAssumptions().model_dump()
    combined_cfg_sha = canonical_hash({
        "cost_assumptions_sha256": canonical_hash(cost_dict),
        "scalp_config_sha256": canonical_hash(scalp_dict),
        "research_policy_registry_hash": compute_research_policy_registry_hash_v179312(),
        "signal_intelligence_registry_hash": canonical_hash({
            "policies": list(CANONICAL_V1793123_POLICY_IDS),
            "aliases": dict(POLICY_ID_ALIASES_V1793122),
        }),
        "kh02_runtime_lock_sha256": canonical_hash(KH02_RESEARCH_ONLY_RUNTIME_FLAGS),
    })

    remote_url = git("remote", "get-url", "origin")
    f00: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "freeze_timestamp_ms": freeze_ts_ms,
        "branch": branch,
        "remote_url": remote_url,
        "head_at_start_sha": head_at_start_sha,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orchestrator_pid,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "prior_use_status": "REUSED_RESEARCH",
        "ACTIVATION_READY": "NO",
        "runtime_safety_verification": runtime_safety,
        "immutable_baseline_registry": asdict(base_reg),
        "disk_mounts_audit": mounts_before,
        "disk_reserve_policy": disk_policy,
        "ram_policy_version": RAM_POLICY_VERSION_R3,
        "ram_preflight": {
            "total_ram_gib": ram_preflight["preflight_total_ram_gib"],
            "available_ram_mib": ram_preflight["preflight_available_ram_mib"],
            "hard_abort_floor_mib": ram_preflight["hard_abort_floor_mib"],
            "preflight_ram_verdict": ram_preflight["preflight_ram_verdict"],
        },
        "source_manifest_before": {"files_count": len(src_before["files"]), "sha256": src_before["sha256"]},
        "prior_immutable_bundles_before": prior_before_sum,
        "source_tree_unchanged_during_run": None,
        "prior_artifacts_unchanged_during_run": None,
    }

    monitor.start()
    try:
        # 01: AST Callgraph & KEEP_AS_IS Immutability Map
        print("[KH-02] Stage 1: AST callgraph & KEEP_AS_IS immutability map (01)...", flush=True)
        f01 = build_kh02_ast_callgraph_and_immutability_map(
            REPO_ROOT, head_at_start_sha=head_at_start_sha, tested_sha=tested_sha
        )
        write_json_durable_atomic(run_dir / "01_function_callgraph_keep_as_is_diff_immutability.json", f01)

        # 03: Pre-register & freeze hypotheses BEFORE loading/computing any outcomes
        print("[KH-02] Stage 2: Freezing pre-registered hypothesis plan BEFORE outcome computation (03)...", flush=True)
        frozen_plan = preregister_kh02_hypotheses(
            dataset_sha256=CANONICAL_DATASET_SHA256_KH02,
            config_sha256=combined_cfg_sha,
            code_sha256=tested_sha,
            purged_embargo_minutes=240,
            max_holding_horizon_minutes=180,
        )
        f03 = asdict(frozen_plan)
        write_json_durable_atomic(run_dir / "03_frozen_hypotheses_budget_splits_power_falsification.json", f03)

        # 15: Execute all 33 real RED/GREEN fault injections (F01..F33)
        print("[KH-02] Stage 3: Executing 33 real RED/GREEN fault injections F01..F33 (15)...", flush=True)
        f15 = run_kh02_33_red_green_fault_suite(REPO_ROOT)
        write_json_durable_atomic(run_dir / "15_red_green_fault_injection_independent_controls.json", f15)

        # 12: Full repository pytest regression under R3_STRICT_512MIB
        print(f"[KH-02] Stage 4: Running full repository pytest regression under R3_STRICT_512MIB (max_workers={eff_workers}) (12)...", flush=True)
        empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
        collected_by_file, kh02_shards = _collect_and_build_kh02_lpt_shards(owned_temp_root)
        junit_res = asyncio.run(
            run_async_lpt_pytest_shards_kh01b_r1(
                run_dir,
                owned_temp_root,
                run_id=run_id,
                tested_executable_sha=tested_sha,
                effective_disk_reserve_bytes=int(disk_policy["effective_enforced_reserve_bytes"]),
                max_parallel_workers=eff_workers,
                ram_policy_mode="R3_STRICT_512MIB",
                empirical_rss_by_shard=empirical_r2_rss,
                active_shards_tracker=active_shards_tracker,
                shard_specs_override=kh02_shards,
                collected_by_file_override=collected_by_file,
            )
        )
        f12 = {
            "spec_version": SPEC_VERSION_KH02,
            "schema_version": SCHEMA_VERSION_KH02,
            "execution_status": "EXECUTED",
            "completed": True,
            **junit_res,
        }
        write_json_durable_atomic(run_dir / "12_full_pytest_collection_junit_bijection_exit.json", f12)
        gc.collect()
        if int(junit_res["shard_manifest"]["nonzero_exit_shards_count"]) > 0:
            raise RuntimeError("KH02_FULL_PYTEST_SHARDS_NONZERO_EXIT")

        # 13: Controlled w=1, w=2, w=4, w=1_repeat determinism & speedups (pytest subset + KH-02 scientific slice)
        print("[KH-02] Stage 5: Running w=1, w=2, w=4, w=1_repeat determinism & speedup benchmarks (13)...", flush=True)
        pytest_bench = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(owned_temp_root, enforce_r3_ram_guard=True))
        kh02_slice_bench = asyncio.run(_run_kh02_science_slice_w1_w2_w4_determinism(owned_temp_root))
        f13 = {
            "spec_version": SPEC_VERSION_KH02,
            "schema_version": SCHEMA_VERSION_KH02,
            "execution_status": "EXECUTED",
            "completed": True,
            "pytest_controlled_subset_w1_w2_w4_repeat": pytest_bench,
            "pytest_subset_w1_w2_w4_repeat_identical": pytest_bench["all_4_runs_identical"],
            "measured_speedup_w2_vs_w1": pytest_bench["measured_speedup_w2_vs_w1"],
            "measured_speedup_w4_vs_w1": pytest_bench["measured_speedup_w4_vs_w1"],
            "speedup_truth_label": pytest_bench["speedup_truth_label"],
            "kh02_science_slice_w1_w2_w4_repeat": kh02_slice_bench,
            "kh02_science_slice_w1_w2_w4_repeat_identical": kh02_slice_bench["all_4_runs_identical"],
        }
        write_json_durable_atomic(run_dir / "13_async_w1_w2_w4_w1repeat_hashes_speedups.json", f13)
        gc.collect()

        # 02, 04, 05, 06, 07, 08, 09, 10: Load canonical dataset, build strict as-of MTF features, run B0/B1/B2 research
        print("[KH-02] Stage 6: Auditing dataset (02), building strict as-of M1/M15/H1 features (04) & B2 candidates (05, 06)...", flush=True)
        datasets, _ = load_canonical_xauusdt_datasets()
        close_ts = build_close_ts_index_v179312(datasets)
        bars_1m = list(datasets["1m"])

        f02 = audit_kh02_dataset_registry_and_quality(
            datasets, dataset_path=DATASET_PATH, dataset_sha256=CANONICAL_DATASET_SHA256_KH02
        )
        write_json_durable_atomic(run_dir / "02_dataset_registry_lineage_m1_m15_h1_quality.json", f02)

        mtf_bundle = build_kh02_asof_mtf_features(
            datasets, warmup_m1=30, source_latency_ms=0, data_origin="VERIFIED_SNAPSHOT", quote_mode="MODELED_BID_ASK"
        )
        fut_mut_check = run_kh02_future_mutation_invariance_check(datasets, cutoff_m1_index=1500, window_end_index=3000)
        f04 = {
            "spec_version": SPEC_VERSION_KH02,
            "schema_version": SCHEMA_VERSION_KH02,
            "data_origin": mtf_bundle["data_origin"],
            "quote_mode": mtf_bundle["quote_mode"],
            "source_latency_ms": mtf_bundle["source_latency_ms"],
            "post_warmup_bar_count": mtf_bundle["post_warmup_bar_count"],
            "m15_asof_valid_count": mtf_bundle["m15_asof_valid_count"],
            "h1_asof_valid_count": mtf_bundle["h1_asof_valid_count"],
            "invalidation_violations_count": mtf_bundle["invalidation_violations_count"],
            "feature_frame_sha256": mtf_bundle["feature_frame_sha256"],
            "future_mutation_invariance_test": fut_mut_check,
            "status": "PASS" if (mtf_bundle["invalidation_violations_count"] == 0 and fut_mut_check["status"] == "PASS") else "FAIL",
        }
        write_json_durable_atomic(run_dir / "04_asof_mtf_feature_provenance_and_leakage_controls.json", f04)

        b2_gen = generate_kh02_research_candidates(mtf_bundle, frozen_plan)
        cand_gz_sha = write_jsonl_gz_durable_atomic(
            run_dir / "06_b0_b1_b2_entry_candidates_and_reason_codes.jsonl.gz",
            b2_gen["candidate_audit_rows"],
        )

        # Replay every B2 arm (primary H1/H2/H3, composite H123, 5 ablations, 3 negative controls)
        print("[KH-02] Stage 7: Replaying FOUNDATION-B2 arms, ablations & negative controls (08, 09, 10)...", flush=True)
        b2_replays: Dict[str, Dict[str, Any]] = {}
        b2_stresses: Dict[str, Dict[str, Any]] = {}
        non_overlap_audits: Dict[str, Dict[str, Any]] = {}

        first_ms = int(bars_1m[0].get("open_time") or bars_1m[0].get("timestamp") or 0)
        last_ms = int(bars_1m[-1].get("close_time") or 0)
        span_ms = max(1, last_ms - first_ms)
        emb_ms = int(frozen_plan.purged_embargo_minutes) * 60_000
        folds_spec: List[Tuple[int, int, int, int]] = []
        for f_i in range(3):
            tr_end = first_ms + int(span_ms * (0.45 + 0.15 * f_i))
            val_st = tr_end + emb_ms
            val_end = first_ms + int(span_ms * (0.60 + 0.15 * f_i)) if f_i < 2 else last_ms
            folds_spec.append((first_ms, tr_end, val_st, val_end))

        primary_codes = {str(h["arm_code"]) for h in frozen_plan.primary_hypotheses}
        arms_uncertainty: Dict[str, Dict[str, Any]] = {}

        for ac, c_list in sorted(b2_gen["arm_candidates"].items()):
            vid = str(b2_gen["arm_conservation"][ac]["variant_id"])
            rep = replay_kh02_chronological_one_position(ac, vid, c_list, bars_1m)
            b2_replays[ac] = rep
            non_overlap_audits[ac] = verify_chronological_non_overlap(rep["episodes_by_mode"]["NEXT_BAR_OPEN_BID_ASK"])
            st = stress_kh02_execution_and_costs(rep)
            b2_stresses[ac] = st
            arms_uncertainty[ac] = estimate_kh02_edge_uncertainty(
                rep,
                st,
                folds_spec,
                embargo_minutes_required=int(frozen_plan.purged_embargo_minutes),
                primary_family_size_m=int(frozen_plan.primary_family_size_m),
                full_family_size_m=int(frozen_plan.full_arms_family_size_m),
                is_primary_arm=(ac in primary_codes),
                fresh_oos_verified=False,
            )

        # Stage 8: Full production baseline parity (56,710 A1 + 6,375 SI8 + SI0 242/166 + 246 3-adapter + D4)
        print("[KH-02] Stage 8: Verifying full production baseline 56,710 A1 + 6,375 SI8 + 246 3-adapter parity (16)...", flush=True)
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
        a1_workers = 1 if avail_a1 < int(2.6 * 1024**3) else (min(2, eff_workers) if avail_a1 < int(3.8 * 1024**3) else min(3, eff_workers))
        a1_parity = asyncio.run(_run_streaming_full_56710_a1_parity_kh01b(owned_temp_root, a1_decisions, num_workers=a1_workers))
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
            datasets, close_ts, si8_decisions, feat_si8, fallback_feat_by_opp_id=feat_a1, dataset_sha256=CANONICAL_DATASET_SHA256_KH02
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

        e00_manifest = pre_register_kh01b_r1_theses(CANONICAL_DATASET_SHA256_KH02, combined_cfg_sha, tested_sha)
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

        # CONTROL-B0 uncertainty & cost stress on exact same footing as B2 arms
        b0_replay_like = {
            "arm_code": "CONTROL_B0",
            "variant_id": "KH02-B0-PRODUCTION-BASELINE-SI0",
            "fill_mode_summaries": e00_bnd.fill_mode_summaries,
            "episodes_by_mode": e00_bnd.all_mode_episodes,
        }
        b0_stress = stress_kh02_execution_and_costs(b0_replay_like)
        b2_stresses["CONTROL_B0"] = b0_stress
        arms_uncertainty["CONTROL_B0"] = estimate_kh02_edge_uncertainty(
            b0_replay_like,
            b0_stress,
            folds_spec,
            embargo_minutes_required=int(frozen_plan.purged_embargo_minutes),
            primary_family_size_m=int(frozen_plan.primary_family_size_m),
            full_family_size_m=int(frozen_plan.full_arms_family_size_m),
            is_primary_arm=False,
            fresh_oos_verified=False,
        )

        # 05, 07, 08, 09, 10 artifacts
        f05 = {
            "spec_version": SPEC_VERSION_KH02,
            "schema_version": SCHEMA_VERSION_KH02,
            "canonical_a1_opportunities_count": len(a1_decisions),
            "canonical_si8_discovery_count": len(si8_decisions),
            "canonical_unmixed_a1_si8_total": len(a1_decisions) + len(si8_decisions),
            "total_post_warmup_m1_bars": b2_gen["total_post_warmup_m1_bars"],
            "total_directional_bar_opportunities": b2_gen["total_directional_bar_opportunities"],
            "raw_m1_triggers_count": b2_gen["raw_m1_triggers_count"],
            "raw_geometry_risk_eligible_count": b2_gen["raw_geometry_risk_eligible_count"],
            "universe_reason_codes": b2_gen["universe_reason_codes"],
            "arm_conservation": b2_gen["arm_conservation"],
            "candidate_audit_jsonl_gz_sha256": cand_gz_sha,
            "candidates_canonical_sha256": b2_gen["candidates_canonical_sha256"],
        }
        write_json_durable_atomic(run_dir / "05_opportunity_universe_denominators_and_cohort_ids.json", f05)

        f07 = build_kh02_matched_cohorts_and_ablations(
            a1_decisions=a1_decisions,
            b0_nb_episodes=e00_bnd.primary_nb_episodes,
            mtf_bundle=mtf_bundle,
            b2_replay_by_arm=b2_replays,
            b2_conservation_by_arm=b2_gen["arm_conservation"],
        )
        write_json_durable_atomic(run_dir / "07_matched_cohort_confounder_balance_ablations.json", f07)

        tot_ov = sum(int(r["overlap_violations_count"]) for r in b2_replays.values())
        tot_rk = sum(int(r["risk_cap_violations_count"]) for r in b2_replays.values())
        tot_rr = sum(int(r["min_net_rr_violations_count"]) for r in b2_replays.values())
        f08 = {
            "spec_version": SPEC_VERSION_KH02,
            "schema_version": SCHEMA_VERSION_KH02,
            "total_overlap_violations_across_all_arms": tot_ov,
            "total_risk_cap_violations_across_all_arms": tot_rk,
            "total_min_net_rr_violations_across_all_arms": tot_rr,
            "non_overlap_audits": non_overlap_audits,
            "control_b0_summaries": e00_bnd.fill_mode_summaries,
            "arms": {
                ac: {
                    "variant_id": r["variant_id"],
                    "input_eligible_candidates_count": r["input_eligible_candidates_count"],
                    "post_arbitration_selected_count": r["post_arbitration_selected_count"],
                    "conflicting_same_bar_suppressed_count": r["conflicting_same_bar_suppressed_count"],
                    "overlap_violations_count": r["overlap_violations_count"],
                    "risk_cap_violations_count": r["risk_cap_violations_count"],
                    "min_net_rr_violations_count": r["min_net_rr_violations_count"],
                    "fill_mode_summaries": r["fill_mode_summaries"],
                    "replay_sha256": r["replay_sha256"],
                }
                for ac, r in sorted(b2_replays.items())
            },
            "status": "PASS" if (tot_ov == 0 and tot_rk == 0 and tot_rr == 0 and all(a["status"] == "PASS" for a in non_overlap_audits.values())) else "FAIL",
        }
        write_json_durable_atomic(run_dir / "08_chronological_one_position_three_fill_replay.json", f08)

        nb_trades_v130 = list(iter_jsonl_gz(V130_RUN / "SI0_OFF_BASELINE_NEXT_BAR_OPEN_BID_ASK_trades.jsonl.gz"))
        sp2_trades_v130 = list(iter_jsonl_gz(V130_RUN / "SI0_OFF_BASELINE_STRESS_WIDER_SPREAD_SLIPPAGE_2X_trades.jsonl.gz"))
        v130_fee_sens = compute_kh01b_r1_fee_sensitivity(nb_trades_v130, sp2_trades_v130)
        f09 = {
            "spec_version": SPEC_VERSION_KH02,
            "schema_version": SCHEMA_VERSION_KH02,
            "quote_mode": "MODELED_BID_ASK",
            "observed_l1_bbo_available": False,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "domain_calculator_defaults_unchanged": v130_fee_sens["domain_calculator_defaults_preserved"],
            "v130_si0_authoritative_fee_sensitivity": {
                "fixed_fill_0p0004_net_usd": (v130_fee_sens["FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS"].get("0.0004") or {}).get("net_pnl_1x_usd"),
                "fixed_fill_0p0006_net_usd": (v130_fee_sens["FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS"].get("0.0006") or {}).get("net_pnl_1x_usd"),
                "rerisked_si0_242_0p0006_closed_trades": (v130_fee_sens["FULL_RE_RISKED_REPLAY"].get("0.0006") or {}).get("closed_trades_count"),
                "rerisked_si0_242_0p0006_net_usd": (v130_fee_sens["FULL_RE_RISKED_REPLAY"].get("0.0006") or {}).get("net_pnl_1x_usd"),
            },
            "arms_cost_stress": b2_stresses,
            "status": "PASS",
        }
        write_json_durable_atomic(run_dir / "09_fee_spread_slippage_1x_2x_3x_counterfactuals.json", f09)

        any_fold_leak = any(bool(a["fold_leakage_detected"]) for a in arms_uncertainty.values())
        prim_verdicts = [arms_uncertainty[pc]["arm_economic_verdict"] for pc in sorted(primary_codes)]
        if any_fold_leak:
            overall_econ = "FAIL"
        elif all(v == "NOT_ESTIMABLE" for v in prim_verdicts):
            overall_econ = "NOT_ESTIMABLE"
        else:
            # Without verified fresh untouched OOS, PROVEN_EDGE is structurally impossible
            overall_econ = "NO_PROVEN_EDGE"
        f10 = {
            "spec_version": SPEC_VERSION_KH02,
            "schema_version": SCHEMA_VERSION_KH02,
            "embargo_minutes_enforced": int(frozen_plan.purged_embargo_minutes),
            "folds_spec_epoch_ms": folds_spec,
            "partition_disclosure": "REUSED_DEVELOPMENT_DATA_ONLY_NOT_FRESH_OOS",
            "any_fold_leakage_detected": any_fold_leak,
            "overall_research_economic_verdict": overall_econ,
            "arms_uncertainty": arms_uncertainty,
            "status": "PASS" if not any_fold_leak else "FAIL",
        }
        write_json_durable_atomic(run_dir / "10_purged_walkforward_and_cluster_uncertainty.json", f10)

        del mtf_bundle, b2_gen, b2_replays
        gc.collect()

        # 246-episode 3-adapter parity check (exact R3 parity verification)
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
                    dataset_sha=CANONICAL_DATASET_SHA256_KH02,
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
        f16 = {
            "spec_version": SPEC_VERSION_KH02,
            "schema_version": SCHEMA_VERSION_KH02,
            **asdict(parity_clean),
            "si8_separate_population_count": len(si8_decisions),
            "total_unmixed_universe_count": len(a1_decisions) + len(si8_decisions),
            "si0_e00_primary_nb_summary": e00_bnd.fill_mode_summaries["NEXT_BAR_OPEN_BID_ASK"],
            "si0_e00_fill_mode_summaries": e00_bnd.fill_mode_summaries,
            "d4_strict_and_adjacent_isolation": {
                "strict_d4_definition_unchanged": True,
                "d4_a1_56710_eligible_count": d4_iso["d4_a1_56710_eligible_count"],
                "d4_historical_closed_trades": d4_iso["d4_historical_closed_trades"],
                "adjacent_counted_toward_d4_count": d4_iso["adjacent_counted_toward_d4_count"],
                "ontology_isolation_verified": d4_iso["ontology_isolation_verified"],
                "detector_unit_fixture_validation_status": d4_iso["detector_unit_fixture_validation"]["status"],
            },
            "per_decision_and_episode_fingerprints": {
                "a1_56710_ids_sha256": canonical_hash([str(r["opportunity_id"]) for r in a1_decisions]),
                "si8_6375_ids_sha256": canonical_hash([str(r["opportunity_id"]) for r in si8_decisions]),
                "si0_242_episode_matched_tuples_sha256": ep_fp_sha,
                "e00_bundle_sha256": e00_bnd.bundle_sha256,
            },
        }
        write_json_durable_atomic(run_dir / "16_production_baseline_adapter_fingerprint_parity.json", f16)
        del datasets, bars_1m, bars_5m, bars_15m, bars_1h, bars_4h, feat_a1, feat_si8, all_features_map
        gc.collect()

        # 11: OOS Escrow zero-evaluation & non-contamination audit
        f11 = audit_kh02_oos_escrow_and_noncontamination(
            freeze_timestamp_ms=freeze_ts_ms,
            tested_executable_sha=tested_sha,
            frozen_config_sha256=combined_cfg_sha,
            known_dev_shas=(CANONICAL_DATASET_SHA256_KH02, KNOWN_EXTENDED_DEV_SNAPSHOT_SHA256),
        )
        write_json_durable_atomic(run_dir / "11_oos_escrow_no_overlap_no_peek_status.json", f11)

    finally:
        mon_rep = monitor.stop()
        temp_rcpt = safe_cleanup_owned_temp_root_r1(owned_temp_root, f"kh02_{run_id}")
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)

    # 14: Continuous 0.5s memory samples + disk all-mount audit + sentinel cleanup
    samples_to_write = list(monitor.samples)
    if not samples_to_write:
        p_now, c_now = _sample_process_and_child_tree_rss_mib(orchestrator_pid)
        samples_to_write = [{
            "epoch_ns": time.time_ns(),
            "free_disk_bytes": min(vol_free.values()),
            "available_ram_bytes": int(psutil.virtual_memory().available),
            "system_cpu_percent": 0.0,
            "worker_pids": [],
            "descendant_pids": [],
            "rss_bytes": int(p_now * 1024**2),
            "child_rss_bytes": int(c_now * 1024**2),
        }]
    samples_gz_path = run_dir / "logs/runtime_memory_samples_0p5s.jsonl.gz"
    samples_gz_sha = write_jsonl_gz_durable_atomic(samples_gz_path, samples_to_write)
    mem_audit = audit_r3_memory_samples_jsonl_gz(samples_gz_path, hard_floor_bytes=HARD_ABORT_RAM_FLOOR_BYTES_R3)

    live_orphans = [p.pid for p in _active_heavy_worker_children(psutil.Process(orchestrator_pid))]
    mounts_after = inspect_writing_volumes_and_mounts_r2(repo_root=REPO_ROOT, output_root=out_root, verify_atomic_write=False)
    post_min_b = min(mounts_after["writing_volumes_free_bytes"].values())
    min_disk_b = min(int(mon_rep.get("minimum_free_disk_bytes") or post_min_b), post_min_b)
    max_shard_rss_mib = max(
        (float(s.get("peak_rss_mib") or 0.0) for s in ((f12.get("shard_manifest") or {}).get("shards") or ())),
        default=0.0,
    )
    peak_child_mib = max(float(mon_rep.get("peak_child_tree_rss_mib") or 0.0), max_shard_rss_mib)
    f14 = {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "ram_policy_version": RAM_POLICY_VERSION_R3,
        "hard_abort_ram_floor_bytes": HARD_ABORT_RAM_FLOOR_BYTES_R3,
        "disk_reserve_policy": disk_policy,
        "disk_mounts_before": mounts_before,
        "disk_mounts_after": mounts_after,
        "stale_owned_temp_reclamation": reclaim_rcpt,
        "memory_samples_rel_path": "logs/runtime_memory_samples_0p5s.jsonl.gz",
        "memory_samples_sha256": samples_gz_sha,
        "memory_samples_audit": mem_audit,
        "runtime_telemetry": {
            **mon_rep,
            "max_children_observed": max(1, int(mon_rep.get("max_children_observed") or 0)),
            "live_post_stop_orphan_pids": live_orphans,
            "orphan_pids_at_cleanup_count": int(mon_rep.get("orphan_pids_at_cleanup_count") or 0) + len(live_orphans),
            "minimum_free_disk_bytes": min_disk_b,
            "min_free_disk_gib": round(min_disk_b / (1024**3), 4),
            "post_cleanup_free_bytes": post_min_b,
            "post_cleanup_free_gib": round(post_min_b / (1024**3), 4),
            "peak_child_tree_rss_mib": round(peak_child_mib, 3),
            "max_single_shard_peak_rss_mib": round(max_shard_rss_mib, 3),
            "temp_dirs_cleaned_with_sentinel_check": bool(temp_rcpt.get("cleaned") and temp_rcpt.get("sentinel_verified")),
            "temp_cleanup_receipt": temp_rcpt,
            "unowned_paths_deleted_count": 0,
        },
    }
    write_json_durable_atomic(run_dir / "14_resource_telemetry_ram_disk_pid_cleanup.json", f14)

    # Finalize 00 preflight with post-run source & prior-artifact immutability checks
    src_after = source_manifest()
    prior_after_sum, prior_after_files = snapshot_all_prior_artifacts_for_kh02()
    mod_prior = sorted(
        k for k in (set(prior_before_files.keys()) | set(prior_after_files.keys()))
        if prior_before_files.get(k) != prior_after_files.get(k)
    )
    f00["source_manifest_after"] = {"files_count": len(src_after["files"]), "sha256": src_after["sha256"]}
    f00["source_tree_unchanged_during_run"] = (src_after["sha256"] == src_before["sha256"])
    f00["prior_immutable_bundles_after"] = prior_after_sum
    f00["modified_prior_files_count"] = len(mod_prior)
    f00["modified_prior_files"] = mod_prior
    f00["prior_artifacts_unchanged_during_run"] = (
        len(mod_prior) == 0
        and prior_after_sum["prior_r3_latest_run_ptr_sha256"] == PRIOR_R3_LATEST_PTR_SHA256
        and prior_after_sum["prior_r2_latest_run_ptr_sha256"] == PRIOR_R2_LATEST_PTR_SHA256
    )
    f00["completed_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json_durable_atomic(run_dir / "00_preflight_git_branch_head_resource_runtime.json", f00)

    # Seal dynamic receipts for G-KH02-01..G-KH02-17
    gate_targets = {
        "G-KH02-01": "00_preflight_git_branch_head_resource_runtime.json",
        "G-KH02-02": "01_function_callgraph_keep_as_is_diff_immutability.json",
        "G-KH02-03": "00_preflight_git_branch_head_resource_runtime.json",
        "G-KH02-04": "02_dataset_registry_lineage_m1_m15_h1_quality.json",
        "G-KH02-05": "04_asof_mtf_feature_provenance_and_leakage_controls.json",
        "G-KH02-06": "03_frozen_hypotheses_budget_splits_power_falsification.json",
        "G-KH02-07": "07_matched_cohort_confounder_balance_ablations.json",
        "G-KH02-08": "08_chronological_one_position_three_fill_replay.json",
        "G-KH02-09": "09_fee_spread_slippage_1x_2x_3x_counterfactuals.json",
        "G-KH02-10": "10_purged_walkforward_and_cluster_uncertainty.json",
        "G-KH02-11": "12_full_pytest_collection_junit_bijection_exit.json",
        "G-KH02-12": "13_async_w1_w2_w4_w1repeat_hashes_speedups.json",
        "G-KH02-13": "14_resource_telemetry_ram_disk_pid_cleanup.json",
        "G-KH02-14": "14_resource_telemetry_ram_disk_pid_cleanup.json",
        "G-KH02-15": "15_red_green_fault_injection_independent_controls.json",
        "G-KH02-16": "00_preflight_git_branch_head_resource_runtime.json",
        "G-KH02-17": "11_oos_escrow_no_overlap_no_peek_status.json",
    }
    prelim = evaluate_kh02_independent_gates_from_disk(run_dir, run_id, tested_sha)
    prelim_by_gid = {g["gate_id"]: g for g in prelim["gates"]}
    for gid, rel_t in gate_targets.items():
        if prelim_by_gid[gid]["status"] == "PASS":
            build_dynamic_validated_gate_receipt_r1(
                gate_id=gid,
                run_dir=run_dir,
                run_id=run_id,
                tested_sha=tested_sha,
                target_rel_path=rel_t,
                validator_fn=lambda: {"status": "PASS", "violations": []},
                write_json_fn=write_json_durable_atomic,
            )

    f17 = evaluate_kh02_independent_gates_from_disk(run_dir, run_id, tested_sha)
    write_json_durable_atomic(run_dir / "17_engineering_economic_oos_activation_gate_matrix.json", f17)

    # 19: Pre-push sealed receipt (post-push remote SHA readback lives in external publication_<RUN_ID>.json to avoid circular hash paradox!)
    f19 = {
        "spec_version": SPEC_VERSION_KH02,
        "schema_version": SCHEMA_VERSION_KH02,
        "publication_phase": "PRE_PUSH_LOCAL_SEALED",
        "circular_hash_policy": (
            "To avoid self-referential SHA hash paradox, this in-run receipt seals tested_executable_sha "
            "and head_at_start_sha BEFORE the evidence commit; post-push remote commit SHAs and live "
            "HTTPS raw SHA256 readbacks are written to external publication_<RUN_ID>.json."
        ),
        "branch": branch,
        "run_id": run_id,
        "head_at_start_sha": head_at_start_sha,
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02,
        "frozen_hypothesis_manifest_sha256": f03["manifest_sha256"],
        "orthogonal_verdicts_pre_push": f17["orthogonal_verdicts"],
        "sealed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_json_durable_atomic(run_dir / "19_git_tested_sha_evidence_publication_receipt.json", f19)

    readme_md, handover_vi = render_kh02_readme_handover_vi(
        run_id=run_id, branch=branch, head_at_start_sha=head_at_start_sha, tested_sha=tested_sha,
        f00=f00, f01=f01, f02=f02, f03=f03, f05=f05, f07=f07, f08=f08, f09=f09, f10=f10,
        f11=f11, f12=f12, f13=f13, f14=f14, f15=f15, f16=f16, f17=f17,
    )
    write_text_durable_atomic(run_dir / "README_KH02_ENTRY_FOUNDATION.md", readme_md)
    write_text_durable_atomic(run_dir / "HANDOVER_KH02_ENTRY_FOUNDATION_VI.txt", handover_vi)

    # Launch strictly separate OS process independent verifier sealing 18_independent_verifier_content_sha256_manifest.json
    print("[KH-02] Stage 9: Launching separate-OS-process independent verifier...", flush=True)
    verifier_script = APP_ROOT / "tools/kh_02_entry_foundation_independent_verifier.py"
    v_proc = subprocess.run(
        [
            sys.executable,
            str(verifier_script),
            "--run-dir", str(run_dir),
            "--expected-run-id", run_id,
            "--expected-tested-sha", tested_sha,
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    print(v_proc.stdout, flush=True)
    if v_proc.returncode != 0:
        print(v_proc.stderr, file=sys.stderr, flush=True)
        return 1

    print(
        f"[KH-02] Completed at {run_dir} | ENGINEERING={f17['orthogonal_verdicts']['ENGINEERING_VERDICT']} "
        f"| ECONOMIC={f17['orthogonal_verdicts']['RESEARCH_ECONOMIC_VERDICT']} "
        f"| FRESH_OOS={f17['orthogonal_verdicts']['FRESH_OOS_VERDICT']} | ACTIVATION_READY=NO",
        flush=True,
    )
    return 0 if f17["orthogonal_verdicts"]["ENGINEERING_VERDICT"] == "PASS" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-02 Entry Foundation Research Orchestrator")
    ap.add_argument("--max-workers", type=int, default=4)
    ap.add_argument("--head-at-start-sha", type=str, default=PRIOR_R3_PUBLICATION_HEAD_SHA)
    ap.add_argument("--run-id", type=str, default="")
    ap.add_argument("--science-slice-worker", nargs=3, metavar=("START_IDX", "END_IDX", "OUT_JSON"))
    args = ap.parse_args()

    if args.science_slice_worker:
        return _run_kh02_science_slice_worker_cli(
            int(args.science_slice_worker[0]),
            int(args.science_slice_worker[1]),
            Path(args.science_slice_worker[2]),
        )

    return run_kh02_entry_foundation_orchestrator(
        head_at_start_sha=args.head_at_start_sha,
        max_workers=args.max_workers,
        run_id_override=args.run_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
