#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_01a_r1_verification.py

AURUM Desk — KH-01A.R1 Forensics Truth Closure Autonomous Orchestrator
VERSION: KH-01A.R1

Reuses bounded async orchestration, resource guards, and atomic fsync+readback writers
from `tools/run_kh_01a_verification.py`, writes into a fresh collision-safe directory
`aurum-desk/docs/artifacts/kh_01a_r1/<UTC_RUN_ID>_<TESTED_SHA12>/` (never touching prior
`docs/artifacts/kh_01a/`), executes dynamic validator-sealed gate receipts (zero pre-labeled
`"PASS"`), and runs `tools/kh_01a_r1_independent_verifier.py` in a separate OS process.
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
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from domain_calculator import CostAssumptions
from services.kh_01a_foundation_service import (
    CANONICAL_DATASET_SHA256_KH01A,
    MANDATORY_BRANCH_KH01A,
    PRIOR_KH01A_AUDITED_HEAD_SHA,
    PRIOR_KH01A_TESTED_SHA,
    RECONSTRUCTION_ALGO_SHA256_KH01A_R1,
    RECONSTRUCTION_ALGO_VERSION_KH01A_R1,
    SCHEMA_VERSION_KH01A_R1,
    SPEC_VERSION_KH01A_R1,
    build_dataset_registry_and_fresh_oos_audit_kh01a,
    build_kh_01a_causal_loss_and_win_control_forensics,
)
from services.kh_01a_r1_forensics_closure_service import (
    PRIOR_KH01A_RUN_REL_DIR,
    build_dynamic_validated_gate_receipt_r1,
    build_kh_01a_r1_callgraph_matrix,
    build_macro_reversed_args_red_green_and_dst_audit,
    build_previous_vs_r1_casebook_diff,
    evaluate_kh_01a_r1_14_gates,
    render_kh_01a_r1_readme_and_handover_vi,
    run_kh_01a_r1_adversarial_fault_suite,
    validate_kh_01a_r1_forensics_bundle_strict,
    verify_kh_01a_r1_cross_view_consistency,
)
from services.scalp_m1_types import get_scalp_config
from services.shared_strategy_evaluator import (
    SharedStrategyEvaluator,
    build_market_decision_context,
)
from services.v17_9_1_decision_funnel import classify_historical_dataset_gaps
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_V1793123_POLICY_IDS,
    POLICY_ID_ALIASES_V1793122,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    CompressedFeatureLedger,
    canonical_hash,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    build_exit_event_lineage_v17931211,
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_execution_not_synthetic_v17931211,
    verify_fresh_junit_bijection_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_12_provenance_forensics_and_repair_service import (
    verify_r4_oos_and_activation_verdict_v17931212,
)
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    build_delayed_entry_contract_and_ledgers_v17931213,
    enrich_si8_aligned_decisions_and_features_v17931213,
    evaluate_shared_delayed_entry_adapter_v17931213,
    verify_bounded_async_workers_v17931213,
    verify_delayed_entry_and_structural_sl_tp_v17931213,
)
from services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service import (
    build_267_target_failure_and_control_audit_v17931214,
    build_cost_stress_and_purged_walkforward_oos_v17931214,
    build_d4_full_63085_and_matched_funnel_v17931214,
    build_p0a_accounting_causal_diff_and_reconciliation_v17931214,
    verify_267_target_audit_v17931214,
    verify_d4_funnel_and_execution_truth_v17931214,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import EvidenceReceiptV17931219
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
    ESTIMATED_STAGE_PEAK_DISK_BYTES,
    MIN_FREE_DISK_RESERVE_BYTES,
    PREPATCH_BASELINE_REFERENCE,
    V130_RUN,
    V19_REF_RUN,
    ResourceMonitorKH01A,
    _read_v12_funnel_09_doc,
    cleanup_owned_temp_root,
    create_owned_temp_root,
    file_sha256,
    iter_jsonl_gz,
    run_async_pytest_shards_kh01a,
    run_controlled_worker_1_2_4_subset_benchmark_kh01a,
)
from tools.run_v17_9_3_1_2_1_full_opportunity_replay import (
    DATASET_PATH,
    FILL_MODES,
    PRIOR_V179312_RUN_DIR,
    _load_verified_decision_stream_chunks,
)
from tools.run_v17_9_3_1_2_2_entry_signal_backtest import replay_single_si_policy_v1793122
from tools.run_v17_9_3_1_2_7_verification import git, source_manifest
from tools.run_v17_9_3_1_2_11_verification import (
    _run_worker_slice_cli,
    run_worker_1_2_4_and_repeat_determinism_v17931211,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

ARTIFACTS_R1_ROOT = APP_ROOT / "docs/artifacts/kh_01a_r1"


def prior_all_immutable_bundles_hash() -> Dict[str, Any]:
    files: Dict[str, str] = {}
    for ver_dir in (
        "v17_9_3_1_2_10",
        "v17_9_3_1_2_11",
        "v17_9_3_1_2_12",
        "v17_9_3_1_2_13",
        "v17_9_3_1_2_14",
        "v17_9_3_1_2_16",
        "v17_9_3_1_2_17",
        "v17_9_3_1_2_18",
        "v17_9_3_1_2_19",
        "kh_01a",
    ):
        root = APP_ROOT / "docs/artifacts" / ver_dir
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
                files[rel] = file_sha256(p)
    return {"files_count": len(files), "sha256": canonical_hash(files)}


# ==============================================================================
# SUBPROCESS WORKER SLICES: 170-ROW FORENSICS & 56,710 FULL-UNIVERSE A1 PARITY
# ==============================================================================

def _run_forensics_slice_cli(in_gz_path: Path, out_json_path: Path) -> int:
    with gzip.open(in_gz_path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    datasets, prov = load_canonical_xauusdt_datasets()
    close_ts = build_close_ts_index_v179312(datasets)

    target_oids = set(payload["target_oids"])
    d10_map: Dict[str, Dict[str, Any]] = {}
    for row in iter_jsonl_gz(V130_RUN / "10_canonical_decision_inputs.jsonl.gz"):
        oid = str(row.get("opportunity_id") or "")
        if oid in target_oids:
            d10_map[oid] = row

    f07_map: Dict[str, Dict[str, Any]] = {}
    for f_row in iter_jsonl_gz(V130_RUN / "07_verified_causal_features.jsonl.gz"):
        oid = str(f_row.get("opportunity_id") or "")
        if oid in target_oids:
            f07_map[oid] = f_row

    out_bundle = build_kh_01a_causal_loss_and_win_control_forensics(
        si0_all_primary_episodes=payload["si0_slice"],
        si8_loss_rows=payload["si8_slice"],
        si9_loss_rows=payload["si9_slice"],
        decision_inputs_by_opp_id=d10_map,
        feature_ledger_by_opp_id=f07_map,
        datasets=datasets,
        close_ts_index=close_ts,
        dataset_sha256=str(prov["dataset_sha256"]),
    )
    row_hashes = [
        {
            "population_role": str(r["population_role"]),
            "opportunity_id": str(r["opportunity_id"]),
            "row_sha256": str(r["row_sha256"]),
            "reason_code": str(r["pre_entry_known"]["reason_code"]),
            "ny_session_group": str((r["pre_entry_features"].get("ny_session_group") or {}).get("value")),
            "in_ict_macro_window": (r["pre_entry_features"].get("in_ict_macro_window") or {}).get("value"),
        }
        for r in out_bundle["casebook_rows"]
    ]
    write_json_durable_atomic(
        out_json_path,
        {
            "worker_pid": os.getpid(),
            "rows_count": len(row_hashes),
            "row_hashes": row_hashes,
        },
    )
    return 0


def _run_parity_a1_slice_cli(in_json_path: Path, out_json_path: Path) -> int:
    from tools.run_v17_9_3_macro_unicorn_backtest import (
        V1793_VARIANT_POLICIES,
        build_v1793_variant_market_context,
    )

    spec = json.loads(in_json_path.read_text(encoding="utf-8"))
    min_ts = int(spec["min_ts_inclusive"])
    max_ts = int(spec["max_ts_inclusive"])

    datasets, _ = load_canonical_xauusdt_datasets()
    close_ts = build_close_ts_index_v179312(datasets)
    bars_1m = datasets["1m"]
    bars_5m = datasets["5m"]
    bars_15m = datasets["15m"]
    bars_1h = datasets["1H"]
    bars_4h = datasets["4H"]
    bars_1d = datasets["1D"]

    pol_a1_fast = dict(V1793_VARIANT_POLICIES["A1_V1792_SL_TP_BASELINE"])
    pol_a1_fast["mtf_structure_observe_only"] = False

    by_ts: Dict[int, List[Dict[str, Any]]] = {}
    for row in iter_jsonl_gz(V130_RUN / "10_canonical_decision_inputs.jsonl.gz"):
        if row.get("discovered_by_si8"):
            continue
        ts = int(row["decision_timestamp_ms"])
        if min_ts <= ts <= max_ts:
            by_ts.setdefault(ts, []).append(row)

    evaluated_count = 0
    mismatches: List[Dict[str, Any]] = []
    compact_digests: List[Tuple[str, str, str]] = []

    for ts in sorted(by_ts.keys()):
        i1 = bisect.bisect_right(close_ts["1m"], ts)
        i5 = bisect.bisect_right(close_ts["5m"], ts)
        i15 = bisect.bisect_right(close_ts["15m"], ts)
        ih1 = bisect.bisect_right(close_ts["1H"], ts)
        ih4 = bisect.bisect_right(close_ts["4H"], ts)
        id1 = bisect.bisect_right(close_ts["1D"], ts)
        w1 = bars_1m[max(0, i1 - 120) : i1]
        lc = float(w1[-1]["close"])
        base_in = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": ts,
            "m1_candles": w1,
            "m5_candles": bars_5m[max(0, i5 - 96) : i5],
            "m15_candles": bars_15m[max(0, i15 - 96) : i15],
            "h1_candles": bars_1h[max(0, ih1 - 96) : ih1],
            "h4_candles": bars_4h[max(0, ih4 - 64) : ih4],
            "d1_candles": bars_1d[max(0, id1 - 30) : id1],
            "bid": round(lc - 0.10, 2),
            "ask": round(lc + 0.10, 2),
        }
        ctx = build_v1793_variant_market_context(base_in, pol_a1_fast, decision_time=ts)
        for rec in by_ts[ts]:
            oid = str(rec["opportunity_id"])
            sn = SharedStrategyEvaluator.evaluate_model_direction(
                ctx, str(rec["model_id"]), str(rec["direction"])
            )
            evaluated_count += 1
            compact_digests.append((oid, sn.decision_fingerprint, sn.trade_action_fingerprint))
            if (
                sn.decision_fingerprint != rec.get("decision_fingerprint")
                or sn.trade_action_fingerprint != rec.get("trade_action_fingerprint")
                or str(sn.decision_status) != str(rec.get("decision_status"))
                or float(rec.get("hypothetical_quantity") or 0.0) <= 0.0
            ):
                if len(mismatches) < 25:
                    mismatches.append({
                        "opportunity_id": oid,
                        "decision_timestamp_ms": ts,
                        "stored_decision_fp": rec.get("decision_fingerprint"),
                        "recomp_decision_fp": sn.decision_fingerprint,
                        "stored_action_fp": rec.get("trade_action_fingerprint"),
                        "recomp_action_fp": sn.trade_action_fingerprint,
                    })

    compact_digests.sort(key=lambda t: t[0])
    part_sha = canonical_hash(compact_digests)
    write_json_durable_atomic(
        out_json_path,
        {
            "worker_pid": os.getpid(),
            "unique_timestamps_evaluated": len(by_ts),
            "evaluated_opportunities_count": evaluated_count,
            "mismatches_count": len(mismatches),
            "mismatches_sample": mismatches,
            "partition_sorted_fingerprints_sha256": part_sha,
        },
    )
    return 0


async def run_forensics_170_w1_w2_w4_determinism_r1(
    owned_temp_root: Path,
    *,
    si0_closed_eps: Sequence[Mapping[str, Any]],
    si8_losses: Sequence[Mapping[str, Any]],
    si9_losses: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    KH-01A.R1 Phase 5 (H3): Runs the 170-row forensic casebook (`166` SI0 closed +
    `4` unique SI8/SI9) across `w=1`, `w=2`, `w=4`, and `w=1_repeat` partitioned
    worker subprocesses (`max_children <= 4`), proving all semantic row hashes
    match 100% independent of shard count or scheduling order.
    """
    py_exe = sys.executable
    script_path = str(Path(__file__).resolve())
    bench_dir = owned_temp_root / "forensics_170_w124"
    bench_dir.mkdir(parents=True, exist_ok=True)

    si8_by_id = {str(r["opportunity_id"]): r for r in si8_losses}
    si9_by_id = {str(r["opportunity_id"]): r for r in si9_losses}
    unique_si89_ids = sorted(set(si8_by_id.keys()).union(si9_by_id.keys()))

    modes: Tuple[Tuple[str, int], ...] = (
        ("w=1", 1),
        ("w=2", 2),
        ("w=4", 4),
        ("w=1_repeat", 1),
    )
    runs: List[Dict[str, Any]] = []

    for mode_lbl, n_parts in modes:
        sem = asyncio.Semaphore(n_parts)
        pids: List[int] = []
        t0 = time.perf_counter()

        si0_parts: List[List[Mapping[str, Any]]] = [[] for _ in range(n_parts)]
        for i, ep in enumerate(si0_closed_eps):
            si0_parts[i % n_parts].append(ep)

        si8_parts: List[List[Mapping[str, Any]]] = [[] for _ in range(n_parts)]
        si9_parts: List[List[Mapping[str, Any]]] = [[] for _ in range(n_parts)]
        for i, uid in enumerate(unique_si89_ids):
            p_idx = i % n_parts
            if uid in si8_by_id:
                si8_parts[p_idx].append(si8_by_id[uid])
            if uid in si9_by_id:
                si9_parts[p_idx].append(si9_by_id[uid])

        async def _exec_part(p_idx: int) -> Dict[str, Any]:
            async with sem:
                tag = f"{mode_lbl.replace('=', '')}_p{p_idx}"
                in_gz = bench_dir / f"{tag}_in.json.gz"
                out_json = bench_dir / f"{tag}_out.json"
                oids = {str(e["opportunity_id"]) for e in si0_parts[p_idx]} | {
                    str(e["opportunity_id"]) for e in si8_parts[p_idx]
                } | {str(e["opportunity_id"]) for e in si9_parts[p_idx]}
                with gzip.open(in_gz, "wt", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "si0_slice": si0_parts[p_idx],
                            "si8_slice": si8_parts[p_idx],
                            "si9_slice": si9_parts[p_idx],
                            "target_oids": sorted(oids),
                        },
                        fh,
                    )
                env = dict(os.environ)
                env["PYTHONHASHSEED"] = "0"
                proc = await asyncio.create_subprocess_exec(
                    py_exe,
                    script_path,
                    f"--forensics-slice-input={in_gz}",
                    f"--forensics-slice-output={out_json}",
                    cwd=str(REPO_ROOT),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                pids.append(int(proc.pid))
                _, err_b = await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"FORENSICS_SLICE_FAILED:{tag}:rc={proc.returncode}:{err_b.decode('utf-8', errors='replace')[-600:]}"
                    )
                return json.loads(out_json.read_text(encoding="utf-8"))

        part_outputs = await asyncio.gather(*(_exec_part(idx) for idx in range(n_parts)))
        wall_s = round(time.perf_counter() - t0, 3)
        merged_rows: List[Dict[str, Any]] = []
        for po in part_outputs:
            merged_rows.extend(po["row_hashes"])
        merged_rows.sort(key=lambda r: (r["population_role"], r["opportunity_id"]))
        canon_sha = canonical_hash(merged_rows)
        runs.append({
            "mode_label": mode_lbl,
            "worker_partitions": n_parts,
            "wall_clock_seconds": wall_s,
            "worker_pids": pids,
            "rows_evaluated": len(merged_rows),
            "canonical_sorted_170_row_hashes_sha256": canon_sha,
        })

    shas = {r["canonical_sorted_170_row_hashes_sha256"] for r in runs}
    return {
        "runs": runs,
        "all_4_runs_row_hashes_identical": len(shas) == 1 and all(r["rows_evaluated"] == 170 for r in runs),
        "canonical_170_rows_sha256": next(iter(shas)) if len(shas) == 1 else None,
    }


async def run_streaming_full_56710_a1_parity_r1(
    owned_temp_root: Path,
    baseline_decisions: Sequence[Mapping[str, Any]],
    *,
    num_workers: int = 3,
) -> Dict[str, Any]:
    """
    KH-01A.R1 Phase 4 (G4): Bounded multi-process streaming verification of
    `SharedStrategyEvaluator.evaluate_model_direction` across ALL `56,710` canonical
    `A1_V1792_SL_TP_BASELINE` opportunities (`18,744` unique timestamps) with `< 300 MB`
    worker RSS and zero RAM/disk spikes.
    """
    py_exe = sys.executable
    script_path = str(Path(__file__).resolve())
    p_dir = owned_temp_root / "parity_a1_56710"
    p_dir.mkdir(parents=True, exist_ok=True)

    unique_ts = sorted({int(r["decision_timestamp_ms"]) for r in baseline_decisions})
    eff_w = max(1, min(3, int(num_workers)))
    chunk_len = (len(unique_ts) + eff_w - 1) // eff_w
    ts_ranges: List[Tuple[int, int]] = []
    for i in range(0, len(unique_ts), chunk_len):
        sub = unique_ts[i : i + chunk_len]
        ts_ranges.append((sub[0], sub[-1]))

    sem = asyncio.Semaphore(eff_w)
    t0 = time.perf_counter()

    async def _run_part(idx: int, lo_ts: int, hi_ts: int) -> Dict[str, Any]:
        async with sem:
            in_p = p_dir / f"part_{idx}_in.json"
            out_p = p_dir / f"part_{idx}_out.json"
            write_json_durable_atomic(in_p, {"min_ts_inclusive": lo_ts, "max_ts_inclusive": hi_ts})
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = "0"
            proc = await asyncio.create_subprocess_exec(
                py_exe,
                script_path,
                f"--parity-a1-slice-input={in_p}",
                f"--parity-a1-slice-output={out_p}",
                cwd=str(REPO_ROOT),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err_b = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"PARITY_A1_SLICE_FAILED:idx={idx}:rc={proc.returncode}:{err_b.decode('utf-8', errors='replace')[-600:]}"
                )
            return json.loads(out_p.read_text(encoding="utf-8"))

    parts_res = await asyncio.gather(*(_run_part(i, lo, hi) for i, (lo, hi) in enumerate(ts_ranges)))
    elapsed = round(time.perf_counter() - t0, 3)

    tot_eval = sum(int(p["evaluated_opportunities_count"]) for p in parts_res)
    tot_ts = sum(int(p["unique_timestamps_evaluated"]) for p in parts_res)
    tot_mismatches = sum(int(p["mismatches_count"]) for p in parts_res)
    mismatch_samples: List[Dict[str, Any]] = []
    for p in parts_res:
        mismatch_samples.extend(p.get("mismatches_sample") or [])

    return {
        "population_name": "CANONICAL_A1_V1792_SL_TP_BASELINE_ELIGIBLE_OPPORTUNITIES",
        "population_denominator": len(baseline_decisions),
        "unique_timestamps_denominator": len(unique_ts),
        "evaluated_count": tot_eval,
        "unique_timestamps_evaluated": tot_ts,
        "mismatches_count": tot_mismatches,
        "mismatches": mismatch_samples[:25],
        "partition_digests": [p["partition_sorted_fingerprints_sha256"] for p in parts_res],
        "aggregate_partitions_sha256": canonical_hash([p["partition_sorted_fingerprints_sha256"] for p in parts_res]),
        "worker_partitions_used": len(parts_res),
        "elapsed_seconds": elapsed,
        "status": "PASS" if (tot_eval == len(baseline_decisions) and tot_mismatches == 0) else "FAIL",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="KH-01A.R1 Forensics Truth Closure Orchestrator")
    ap.add_argument("--tested-code-sha", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--output-root", default="")
    ap.add_argument("--worker-slice-input", default="")
    ap.add_argument("--worker-slice-output", default="")
    ap.add_argument("--forensics-slice-input", default="")
    ap.add_argument("--forensics-slice-output", default="")
    ap.add_argument("--parity-a1-slice-input", default="")
    ap.add_argument("--parity-a1-slice-output", default="")
    args = ap.parse_args()

    if args.worker_slice_input and args.worker_slice_output:
        return _run_worker_slice_cli(Path(args.worker_slice_input), Path(args.worker_slice_output))
    if args.forensics_slice_input and args.forensics_slice_output:
        return _run_forensics_slice_cli(Path(args.forensics_slice_input), Path(args.forensics_slice_output))
    if args.parity_a1_slice_input and args.parity_a1_slice_output:
        return _run_parity_a1_slice_cli(Path(args.parity_a1_slice_input), Path(args.parity_a1_slice_output))

    if not args.tested_code_sha:
        raise SystemExit("--tested-code-sha <40-hex SHA> is required")

    if os.name == "nt":
        os.environ["AURUM_VERIFY_VENV_PYTHON"] = sys.executable
        packages = str(Path(sys.prefix) / "Lib/site-packages")
        os.environ["PYTHONPATH"] = os.pathsep.join(
            [str(BACKEND_ROOT), str(APP_ROOT), packages, os.environ.get("PYTHONPATH", "")]
        )
        sys.executable = getattr(sys, "_base_executable", sys.executable)
        found = shutil.which("git")
        direct = Path(found).parents[1] / "mingw64/bin" if found else None
        if direct and (direct / "git.exe").is_file():
            os.environ["PATH"] = str(direct) + os.pathsep + os.environ.get("PATH", "")

    import psutil

    branch = git("branch", "--show-current")
    sha = git("rev-parse", "HEAD")
    dirty_exec = git("status", "--porcelain=v1", "--", "aurum-desk/backend", "aurum-desk/tools").splitlines()
    if branch != MANDATORY_BRANCH_KH01A:
        raise SystemExit(f"BLOCKED_WRONG_BRANCH:{branch}!={MANDATORY_BRANCH_KH01A}")
    if sha != args.tested_code_sha or dirty_exec:
        raise SystemExit(f"BLOCKED_UNFROZEN_OR_MISMATCHED_SHA:head={sha},expected={args.tested_code_sha},dirty={dirty_exec}")

    subprocess.run(
        ["git", "merge-base", "--is-ancestor", PRIOR_KH01A_AUDITED_HEAD_SHA, sha],
        cwd=str(REPO_ROOT),
        check=True,
    )

    disk_u = shutil.disk_usage(REPO_ROOT)
    if disk_u.free < (MIN_FREE_DISK_RESERVE_BYTES + ESTIMATED_STAGE_PEAK_DISK_BYTES):
        raise SystemExit(
            f"BLOCKED_LOW_DISK:free={disk_u.free} < required={MIN_FREE_DISK_RESERVE_BYTES + ESTIMATED_STAGE_PEAK_DISK_BYTES}"
        )

    actual_dataset_sha = file_sha256(DATASET_PATH)
    if actual_dataset_sha != CANONICAL_DATASET_SHA256_KH01A:
        raise SystemExit(f"BLOCKED_DATASET_SHA_MISMATCH:{actual_dataset_sha}")

    out_root = Path(args.output_root).resolve() if args.output_root else ARTIFACTS_R1_ROOT
    base_ts_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = args.run_id or f"{base_ts_id}_{sha[:12]}"
    if (out_root / run_id).exists():
        run_id = f"{base_ts_id}_{os.getpid()}_{sha[:12]}"
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "ledgers").mkdir(parents=True, exist_ok=True)
    (run_dir / "receipts").mkdir(parents=True, exist_ok=True)
    orchestrator_pid = os.getpid()

    owned_temp_root = create_owned_temp_root(f"r1_{run_id}")
    os.environ["AURUM_VERIFICATION_ARTIFACT_ROOT"] = str(owned_temp_root)
    os.environ["AURUM_TESTED_EXECUTABLE_SHA"] = sha
    os.environ["AURUM_MINIMUM_RAM_BYTES"] = str(512 * 1024**2)

    monitor = ResourceMonitorKH01A(abort_path=run_dir / "resource_abort.json")
    os.environ["AURUM_RESOURCE_GUARD_ABORT_FILE"] = str(monitor.abort_path)

    src_before = source_manifest()
    prior_before = prior_all_immutable_bundles_hash()
    backend_tree_sha = git("rev-parse", f"{sha}:aurum-desk/backend")
    tools_tree_sha = git("rev-parse", f"{sha}:aurum-desk/tools")
    recent_commits = git("log", "-n", "6", "--oneline").splitlines()

    scalp_cfg = get_scalp_config()
    scalp_dict = scalp_cfg.model_dump() if hasattr(scalp_cfg, "model_dump") else vars(scalp_cfg)
    cost_inst = CostAssumptions()
    cost_dict = cost_inst.model_dump() if hasattr(cost_inst, "model_dump") else vars(cost_inst)
    config_hashes = {
        "cost_assumptions_sha256": canonical_hash(cost_dict),
        "scalp_config_sha256": canonical_hash(scalp_dict),
        "research_policy_registry_hash": compute_research_policy_registry_hash_v179312(),
        "signal_intelligence_registry_hash": canonical_hash({
            "policies": list(CANONICAL_V1793123_POLICY_IDS),
            "aliases": dict(POLICY_ID_ALIASES_V1793122),
        }),
        "r1_reconstruction_algo_version": RECONSTRUCTION_ALGO_VERSION_KH01A_R1,
        "r1_reconstruction_algo_sha256": RECONSTRUCTION_ALGO_SHA256_KH01A_R1,
    }

    vm = psutil.virtual_memory()
    preflight_00: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH01A_R1,
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "branch": branch,
        "prior_audited_head_sha": PRIOR_KH01A_AUDITED_HEAD_SHA,
        "prior_kh01a_tested_executable_sha": PRIOR_KH01A_TESTED_SHA,
        "prior_kh01a_run_dir": PRIOR_KH01A_RUN_REL_DIR,
        "tested_executable_sha": sha,
        "recent_git_log_oneline": recent_commits,
        "backend_git_tree_sha": backend_tree_sha,
        "tools_git_tree_sha": tools_tree_sha,
        "dirty_executable_files": dirty_exec,
        "orchestrator_pid": orchestrator_pid,
        "canonical_dataset_path": "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz",
        "canonical_dataset_sha256": actual_dataset_sha,
        "source_manifest": {
            "files_count": len(src_before["files"]),
            "sha256": src_before["sha256"],
        },
        "prior_immutable_artifacts_snapshot": prior_before,
        "config_versions_and_hashes": config_hashes,
        "prepatch_baseline_reference_hashes": PREPATCH_BASELINE_REFERENCE,
        "preflight_host_resources": {
            "os_platform": sys.platform,
            "python_version": sys.version,
            "free_disk_bytes": int(disk_u.free),
            "free_disk_gb": round(disk_u.free / (1024**3), 2),
            "available_ram_bytes": int(vm.available),
            "available_ram_gb": round(vm.available / (1024**3), 2),
            "total_ram_bytes": int(vm.total),
            "physical_cpus": psutil.cpu_count(logical=False),
            "logical_cpus": psutil.cpu_count(logical=True),
            "minimum_free_disk_reserve_bytes": MIN_FREE_DISK_RESERVE_BYTES,
            "global_nested_child_process_cap": 4,
            "owned_temp_root": str(owned_temp_root),
        },
        "runtime_safety_locks": {
            "PRODUCTION": "OFF",
            "LIVE": "OFF",
            "PAPER": "OFF",
            "AUTO": "OFF",
            "ACTIVATION_READY": "NO",
        },
        "source_tree_unchanged_during_run": False,
        "prior_artifacts_unchanged_during_run": False,
    }
    write_json_durable_atomic(run_dir / "00_branch_sha_config_dataset_preflight.json", preflight_00)

    callgraph_01 = build_kh_01a_r1_callgraph_matrix(REPO_ROOT)
    callgraph_01["run_id"] = run_id
    callgraph_01["tested_executable_sha"] = sha
    write_json_durable_atomic(run_dir / "01_function_callgraph_keep_as_is_changed_matrix.json", callgraph_01)

    try:
        monitor.start()

        # STAGE 1: Full Async Sharded Backend + KH-01A + KH-01A.R1 Pytest Suite (11)
        print(f"[KH-01A.R1 Stage 1] Running async bounded sharded pytest suite on {sha[:12]}...", flush=True)
        junit_res = asyncio.run(
            run_async_pytest_shards_kh01a(
                run_dir,
                owned_temp_root,
                run_id=run_id,
                tested_executable_sha=sha,
                max_parallel_workers=args.workers,
            )
        )
        junit_11_disk = {k: v for k, v in junit_res.items() if k != "worker_records"}
        junit_11_disk["spec_version"] = SPEC_VERSION_KH01A_R1
        junit_11_disk["schema_version"] = SCHEMA_VERSION_KH01A_R1
        write_json_durable_atomic(run_dir / "11_pytest_collection_junit_closed_shard_exit_bijection.json", junit_11_disk)
        monitor.check()

        # STAGE 2: Controlled w=1, w=2, w=4, w=1_repeat Pytest Subset Benchmark
        print("[KH-01A.R1 Stage 2] Running controlled w=1, w=2, w=4, w=1_repeat pytest subset benchmark...", flush=True)
        subset_bench = asyncio.run(
            run_controlled_worker_1_2_4_subset_benchmark_kh01a(
                owned_temp_root,
                run_id=run_id,
                tested_sha=sha,
            )
        )
        monitor.check()

        # STAGE 3: Full Historical Replay SI0..SI9 x 3 Fill Modes + A0..D4 + 180m Purged WF + 267+40 Target Audit (09)
        print("[KH-01A.R1 Stage 3] Loading canonical dataset & replaying 30 SI partitions + A0..D4...", flush=True)
        datasets, dataset_prov = load_canonical_xauusdt_datasets()
        gap_report = classify_historical_dataset_gaps(datasets)
        bars_1m = list(datasets["1m"])
        bars_1m_by_open_ts = {int(b.get("open_time") or b.get("timestamp") or 0): b for b in bars_1m}
        close_ts = build_close_ts_index_v179312(datasets)
        pol_hash = compute_research_policy_registry_hash_v179312()

        bar_close_by_tf_open: Dict[Tuple[str, int], int] = {}
        for tf_u, tf_k, def_span in (
            ("1M", "1m", 60_000),
            ("5M", "5m", 300_000),
            ("15M", "15m", 900_000),
            ("1H", "1H", 3_600_000),
            ("4H", "4H", 14_400_000),
            ("1D", "1D", 86_400_000),
        ):
            for b in datasets[tf_k]:
                o_ms = int(b.get("open_time") or b.get("timestamp") or 0)
                c_ms = int(b.get("close_time") or (o_ms + def_span))
                if o_ms > 0:
                    bar_close_by_tf_open[(tf_u, o_ms)] = c_ms

        from services.v17_9_3_1_2_1_root_cause_evidence_service import (
            build_full_intervention_candidate_index_v1793121,
        )

        all_decisions, _ = _load_verified_decision_stream_chunks(PRIOR_V179312_RUN_DIR / "decision_stream_chunks")
        a1_eligible = [
            r for r in all_decisions
            if r.get("variant") == "A1_V1792_SL_TP_BASELINE" and r.get("decision_status") == "ELIGIBLE"
        ]
        del all_decisions
        gc.collect()

        cand_index = build_full_intervention_candidate_index_v1793121(
            a1_eligible, datasets, pol_hash, close_ts_index=close_ts
        )
        v130_canonical_inputs = list(iter_jsonl_gz(V130_RUN / "10_canonical_decision_inputs.jsonl.gz"))
        baseline_decisions = [r for r in v130_canonical_inputs if not r.get("discovered_by_si8")]
        si8_decisions = [r for r in v130_canonical_inputs if r.get("discovered_by_si8")]
        si8_id_set = {str(r["opportunity_id"]) for r in si8_decisions}
        si8_by_idx: Dict[int, List[Dict[str, Any]]] = {}
        for rec in si8_decisions:
            si8_by_idx.setdefault(int(rec["checkpoint_index_1m"]), []).append(rec)

        feat_by_opp_id = CompressedFeatureLedger()
        si8_feat_by_opp_id = CompressedFeatureLedger()
        for idx_f, f_row in enumerate(iter_jsonl_gz(V130_RUN / "07_verified_causal_features.jsonl.gz")):
            if not verify_feature_snapshot_v1793127(f_row):
                raise RuntimeError(f"INVALID_FEATURE_SNAPSHOT_PROOF:{f_row.get('opportunity_id')}")
            oid = str(f_row["opportunity_id"])
            if oid in si8_id_set or f_row.get("discovered_by_si8"):
                si8_feat_by_opp_id[oid] = f_row
            else:
                feat_by_opp_id[oid] = f_row
            if idx_f > 0 and idx_f % 8000 == 0:
                gc.collect()
        gc.collect()

        enrich_si8_aligned_decisions_and_features_v17931213(
            datasets,
            close_ts,
            si8_decisions,
            si8_feat_by_opp_id,
            fallback_feat_by_opp_id=feat_by_opp_id,
            dataset_sha256=actual_dataset_sha,
        )
        cand_index["si8_discovered_decisions_by_checkpoint_index"] = si8_by_idx
        cand_index["si8_discovered_feature_ledger_by_opp_id"] = si8_feat_by_opp_id
        cand_index["v1793127_causal_truth_required"] = True
        all_features_map = ChainMap(si8_feat_by_opp_id, feat_by_opp_id)

        si_results_by_policy: Dict[str, Dict[str, Any]] = {}
        for pid in CANONICAL_V1793123_POLICY_IDS:
            pol_out = replay_single_si_policy_v1793122(
                pid, cand_index, datasets, all_features_map, fill_modes=FILL_MODES
            )
            if pid not in ("SI8_RESEARCH_DISCOVERY_REPAIRED", "SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"):
                pol_out.pop("policy_decision_ledger", None)
                pol_out.pop("decision_ledger", None)
            si_results_by_policy[pid] = pol_out
            gc.collect()
        monitor.check()

        all_lineage_events: List[Dict[str, Any]] = []
        all_source_execution_rows: List[Dict[str, Any]] = []
        for pid, pol_res in si_results_by_policy.items():
            for fm, eps in pol_res["all_mode_episodes"].items():
                for ep in eps:
                    led_ep = ep.get("execution_ledger") if isinstance(ep.get("execution_ledger"), Mapping) else {}
                    all_source_execution_rows.append({
                        "run_id": run_id,
                        "tested_executable_sha": sha,
                        "dataset_sha256": actual_dataset_sha,
                        "policy_id": pid,
                        "fill_mode": fm,
                        "opportunity_id": str(ep.get("opportunity_id") or ""),
                        "episode_key": str(ep.get("episode_key") or ""),
                        "setup_episode_id": str(ep.get("setup_episode_id") or ""),
                        "model_id": str(ep.get("model_id") or ""),
                        "direction": str(ep.get("direction") or ""),
                        "decision_timestamp_ms": int(ep.get("decision_timestamp_ms") or led_ep.get("decision_ts") or 0),
                        "bars_held": int(ep.get("bars_held") or led_ep.get("bars_held") or 0),
                        "filled": bool(ep.get("filled", led_ep.get("filled", False))),
                        "outcome_label": str(ep.get("outcome_label") or led_ep.get("outcome_label") or ""),
                        "exit_type": str(ep.get("exit_type") or led_ep.get("exit_type") or ""),
                        "entry_fill_ts": int(ep.get("entry_fill_ts") or led_ep.get("entry_fill_ts") or 0),
                        "trigger_bar_ts": int(led_ep.get("trigger_bar_ts") or 0),
                        "exit_fill_ts": int(ep.get("exit_fill_ts") or led_ep.get("exit_fill_ts") or ep.get("release_ts_ms") or 0),
                        "entry_reference": float(ep.get("entry_reference") or led_ep.get("entry_reference") or 0.0),
                        "entry_fill_price": float(ep.get("entry_fill_price") or led_ep.get("entry_fill_price") or 0.0),
                        "selected_sl": float(ep.get("selected_sl") or led_ep.get("planned_sl") or 0.0),
                        "take_profit": float(ep.get("take_profit") or led_ep.get("planned_tp") or 0.0),
                        "exit_fill_price": float(ep.get("exit_fill_price") or led_ep.get("exit_fill_price") or 0.0),
                        "executed_quantity": float(ep.get("executed_quantity") or led_ep.get("quantity") or ep.get("hypothetical_quantity") or 0.0),
                        "planned_net_risk_usd": float(ep.get("planned_net_risk_usd") or led_ep.get("planned_net_risk_usd") or 0.0),
                        "entry_fee_usd": round(float(ep.get("entry_fee_usd") or led_ep.get("entry_fee") or 0.0), 4),
                        "exit_fee_usd": round(float(ep.get("exit_fee_usd") or led_ep.get("exit_fee") or 0.0), 4),
                        "realized_gross_pnl_usd": round(float(ep.get("realized_gross_pnl_usd") or led_ep.get("realized_gross_pnl_usd") or 0.0), 4),
                        "realized_net_pnl_usd": round(float(ep.get("realized_net_pnl_usd") or led_ep.get("realized_net_pnl_usd") or 0.0), 4),
                        "realized_r": round(float(ep.get("realized_r") or led_ep.get("realized_r_net") or 0.0), 4),
                    })
                lin_b = build_exit_event_lineage_v17931211(
                    eps,
                    bars_1m_by_open_ts=bars_1m_by_open_ts,
                    run_id=run_id,
                    tested_executable_sha=sha,
                    dataset_sha256=actual_dataset_sha,
                    policy_id=pid,
                    fill_mode=fm,
                )
                all_lineage_events.extend(lin_b["all_events"])

        lin_dir = run_dir / "ledgers"
        write_jsonl_gz_durable_atomic(lin_dir / "09_exit_event_lineage_ledger.jsonl.gz", all_lineage_events)
        write_jsonl_gz_durable_atomic(lin_dir / "09_original_execution_ledger_source_reconciliation.jsonl.gz", all_source_execution_rows)
        del all_lineage_events, all_source_execution_rows
        gc.collect()

        all_part_ind_check = verify_all_policy_fill_mode_lineage_independently_v17931211(
            lin_dir,
            bars_1m_by_open_ts=bars_1m_by_open_ts,
            expected_run_id=run_id,
            expected_tested_sha=sha,
            expected_dataset_sha=actual_dataset_sha,
            require_source_reconciliation=True,
        )
        all_part_ind_check.pop("reconstructed_partitions", None)
        all_part_ind_check.pop("reconstructed_si0_primary_bundle", None)
        if all_part_ind_check["status"] != "PASS":
            raise RuntimeError(f"EXIT_LINEAGE_30_PARTITIONS_FAILED:{all_part_ind_check['violations']}")

        si0_nb_eps = si_results_by_policy["SI0_OFF_BASELINE"]["primary_episodes"]
        si8_nb_eps = si_results_by_policy["SI8_RESEARCH_DISCOVERY_REPAIRED"]["primary_episodes"]
        si9_nb_eps = si_results_by_policy["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["primary_episodes"]

        delayed_bundle = build_delayed_entry_contract_and_ledgers_v17931213(
            si0_nb_episodes=si0_nb_eps,
            feature_ledger_by_opp_id=all_features_map,
            datasets=datasets,
            si_results_by_policy=si_results_by_policy,
            dataset_sha256=actual_dataset_sha,
        )
        delayed_check = verify_delayed_entry_and_structural_sl_tp_v17931213(
            delayed_bundle["contract_02"],
            delayed_bundle["summary_by_variant_and_mode"],
            delayed_bundle["delay_ledger_03"],
            delayed_bundle["portfolio_09"],
        )
        if delayed_check["status"] != "PASS":
            raise RuntimeError(f"DELAYED_ENTRY_CONTRACT_FAILED:{delayed_check['violations']}")

        p0a_bundle = build_p0a_accounting_causal_diff_and_reconciliation_v17931214(
            run_id=run_id,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            si_results_by_policy=si_results_by_policy,
            all_partitions_lineage=all_part_ind_check,
            summary_by_variant_and_mode=delayed_bundle["summary_by_variant_and_mode"],
            portfolio_by_variant_and_mode=delayed_bundle["portfolio_by_variant_and_mode"],
            rows_by_variant_and_mode=delayed_bundle["rows_by_variant_and_mode"],
        )
        if p0a_bundle["status"] != "PASS":
            raise RuntimeError(f"P0A_ACCOUNTING_FAILED:{p0a_bundle['violations']}")

        d4_bundle = build_d4_full_63085_and_matched_funnel_v17931214(
            run_id=run_id,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            baseline_decisions=baseline_decisions,
            si8_decisions=si8_decisions,
            feature_ledger_by_opp_id=all_features_map,
            datasets=datasets,
            matched_242_d4_rows_by_mode=delayed_bundle["rows_by_variant_and_mode"]["D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE"],
        )
        d4_check = verify_d4_funnel_and_execution_truth_v17931214(
            d4_bundle["d4_summary"], d4_bundle["provenance_rows_08"]
        )
        if d4_check["status"] != "PASS":
            raise RuntimeError(f"D4_EXECUTION_TRUTH_FAILED:{d4_check['violations']}")

        stress_12, oos_13 = build_cost_stress_and_purged_walkforward_oos_v17931214(
            run_id=run_id,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            policy_dtos=p0a_bundle["policy_dtos"],
            variant_1pos_dtos=p0a_bundle["variant_1pos_dtos"],
            si0_nb_episodes=si0_nb_eps,
            bars_1m=bars_1m,
        )
        oos_lock_chk = verify_r4_oos_and_activation_verdict_v17931212(oos_13)
        if oos_lock_chk["status"] != "PASS":
            raise RuntimeError(f"OOS_LOCK_FAILED:{oos_lock_chk['violations']}")

        # Target 267 + 40 audit (preserved per G6)
        v12_funnel_09_doc = _read_v12_funnel_09_doc()
        p0b_bundle = build_267_target_failure_and_control_audit_v17931214(
            run_id=run_id,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            v12_funnel_09_doc=v12_funnel_09_doc,
            all_63085_decisions=v130_canonical_inputs,
            feature_ledger_by_opp_id=all_features_map,
            candidate_index=cand_index,
            datasets=datasets,
            close_ts_index=close_ts,
            si9_policy_result=si_results_by_policy["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"],
        )
        p0b_check = verify_267_target_audit_v17931214(p0b_bundle["audit_rows_05"], p0b_bundle["summary_06"])
        if p0b_check["status"] != "PASS":
            raise RuntimeError(f"TARGET_267_AUDIT_FAILED:{p0b_check['violations']}")
        target_ledger_sha = write_jsonl_gz_durable_atomic(
            lin_dir / "07_target_307_rows_source_tf_atr_audit.jsonl.gz",
            p0b_bundle["audit_rows_05"],
        )

        si_nb_summary_normalized: Dict[str, Dict[str, Any]] = {}
        for pid, fm_map in p0a_bundle["policy_dtos"].items():
            d_nb = fm_map["NEXT_BAR_OPEN_BID_ASK"]
            si_nb_summary_normalized[pid] = {
                "scheduled": d_nb["scheduled_episodes_count"],
                "closed": d_nb["closed_trades_count"],
                "wins": d_nb["wins_count"],
                "losses": d_nb["losses_count"],
                "open_at_horizon": d_nb["open_at_horizon_count"],
                "data_gap_unresolved": d_nb["data_gap_unresolved_count"],
                "unfilled": d_nb["unfilled_or_no_fill_count"],
                "blocked_by_open_position": d_nb["blocked_by_open_position_count"],
                "gross_pnl_usd": d_nb["realized_gross_pnl_usd"],
                "total_fees_usd": d_nb["total_fees_usd"],
                "net_pnl_usd": d_nb["realized_net_pnl_usd"],
                "expectancy_r": d_nb["expectancy_net_r"],
                "expectancy_net_r": d_nb["expectancy_net_r"],
                "max_drawdown_usd": d_nb["max_drawdown_usd"],
            }

        replay_09 = {
            "spec_version": SPEC_VERSION_KH01A_R1,
            "schema_version": SCHEMA_VERSION_KH01A_R1,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "dataset_sha256": actual_dataset_sha,
            "quote_source_mode": "MODELED_0_20_USD_SPREAD_NOT_CAPTURED_L1_BBO",
            "p0a_accounting_status": p0a_bundle["status"],
            "lineage_30_partitions_status": all_part_ind_check["status"],
            "d4_execution_truth_status": d4_check["status"],
            "si_policies_nb_summary": si_nb_summary_normalized,
            "si_policies_all_3_fill_modes": p0a_bundle["policy_dtos"],
            "variants_1pos_all_3_fill_modes": p0a_bundle["variant_1pos_dtos"],
            "variants_matched_242_all_3_fill_modes": p0a_bundle["variant_matched_dtos"],
            "d4_summary": d4_bundle["d4_summary"],
            "cost_stress_and_intrabar_tie_analysis": stress_12,
            "purged_walkforward_180m_embargo_and_oos": oos_13,
            "target_267_plus_40_audit": {
                "executable_tp_selection_modified": False,
                "active_executable_selector_counterfactual_status": "NOT_EVALUATED_READ_ONLY_AUDIT_ONLY_TP_SELECTION_UNCHANGED",
                "row_level_ledger_path": "ledgers/07_target_307_rows_source_tf_atr_audit.jsonl.gz",
                "row_level_ledger_sha256": target_ledger_sha,
                "summary": p0b_bundle["summary_06"],
                "verification_status": p0b_check["status"],
            },
            "row_level_ledgers": {
                "exit_event_lineage": "ledgers/09_exit_event_lineage_ledger.jsonl.gz",
                "source_execution_reconciliation": "ledgers/09_original_execution_ledger_source_reconciliation.jsonl.gz",
                "target_307_audit": "ledgers/07_target_307_rows_source_tf_atr_audit.jsonl.gz",
            },
        }
        write_json_durable_atomic(
            run_dir / "09_all_3_fill_modes_si0_to_si9_one_position_backtest_oos_cost_stress.json",
            replay_09,
        )
        monitor.check()

        # STAGE 4: R1 Causal Loss & Win-Control Forensics + 2-Run Reproducibility + Prior Diff (02, 03, 04, 05, 06, 07)
        print("[KH-01A.R1 Stage 4] Computing R1 170-row causal forensics, 2-run reproducibility & macro/casebook diffs...", flush=True)
        decisions_by_oid = {str(r["opportunity_id"]): r for r in v130_canonical_inputs if not r.get("discovered_by_si8")}
        for r_s8 in si8_decisions:
            decisions_by_oid.setdefault(str(r_s8["opportunity_id"]), r_s8)

        si8_losses = [ep for ep in si8_nb_eps if str(ep.get("outcome_label") or "").upper() == "LOSS"]
        si9_losses = [ep for ep in si9_nb_eps if str(ep.get("outcome_label") or "").upper() == "LOSS"]

        forensics_run_1 = build_kh_01a_causal_loss_and_win_control_forensics(
            si0_all_primary_episodes=si0_nb_eps,
            si8_loss_rows=si8_losses,
            si9_loss_rows=si9_losses,
            decision_inputs_by_opp_id=decisions_by_oid,
            feature_ledger_by_opp_id=all_features_map,
            datasets=datasets,
            close_ts_index=close_ts,
            dataset_sha256=actual_dataset_sha,
        )
        forensics_run_2 = build_kh_01a_causal_loss_and_win_control_forensics(
            si0_all_primary_episodes=si0_nb_eps,
            si8_loss_rows=si8_losses,
            si9_loss_rows=si9_losses,
            decision_inputs_by_opp_id=decisions_by_oid,
            feature_ledger_by_opp_id=all_features_map,
            datasets=datasets,
            close_ts_index=close_ts,
            dataset_sha256=actual_dataset_sha,
        )

        rows_1 = forensics_run_1["casebook_rows"]
        rows_2 = forensics_run_2["casebook_rows"]
        run1_shas = [r["row_sha256"] for r in rows_1]
        run2_shas = [r["row_sha256"] for r in rows_2]
        two_run_identical = (run1_shas == run2_shas and len(rows_1) == 170)

        strict_val_06 = validate_kh_01a_r1_forensics_bundle_strict(
            rows_1,
            forensics_run_1["census_summary_06"],
            canonical_bar_close_by_tf_open=bar_close_by_tf_open,
            persisted_f07_by_oid=all_features_map,
            expected_dataset_sha=actual_dataset_sha,
        )
        if strict_val_06["status"] != "PASS":
            raise RuntimeError(f"R1_FORENSICS_STRICT_VALIDATION_FAILED:{strict_val_06['violations']}")

        casebook_05_sha = write_jsonl_gz_durable_atomic(
            run_dir / "05_r1_preentry_170_rows.jsonl.gz",
            rows_1,
        )
        forensics_06 = {
            **forensics_run_1["census_summary_06"],
            "run_id": run_id,
            "tested_executable_sha": sha,
            "casebook_05_sha256": casebook_05_sha,
            "strict_bundle_validation": strict_val_06,
        }
        write_json_durable_atomic(
            run_dir / "06_feature_evidence_origin_missing_invalid_source_hash_census.json",
            forensics_06,
        )

        macro_diff_02, macro_dst_03 = build_macro_reversed_args_red_green_and_dst_audit(rows_1)
        macro_diff_02["run_id"] = run_id
        macro_diff_02["tested_executable_sha"] = sha
        macro_dst_03["run_id"] = run_id
        macro_dst_03["tested_executable_sha"] = sha
        write_json_durable_atomic(run_dir / "02_macro_reversed_args_red_green_diff.json", macro_diff_02)
        write_json_durable_atomic(run_dir / "03_macro_dst_schedule_semantics_and_invalid_samples.json", macro_dst_03)

        prior_kh01a_dir = REPO_ROOT / PRIOR_KH01A_RUN_REL_DIR
        casebook_diff_04 = build_previous_vs_r1_casebook_diff(prior_kh01a_dir, forensics_run_1)
        casebook_diff_04["run_id"] = run_id
        casebook_diff_04["tested_executable_sha"] = sha
        write_json_durable_atomic(run_dir / "04_previous_vs_r1_170_casebook_distribution_diff.json", casebook_diff_04)

        not_comp_rows = [
            {
                "opportunity_id": r["opportunity_id"],
                "population_role": r["population_role"],
                "decision_timestamp_ms": r["decision_timestamp_ms"],
                "persisted_decision_timestamp_ms": (r.get("snapshot_lineage") or {}).get("persisted_decision_timestamp_ms"),
                "candidate_ts_a1_fingerprint_match": (r.get("snapshot_lineage") or {}).get("candidate_ts_a1_fingerprint_match"),
                "reason": (r.get("provenance_summary") or {}).get("original_vs_recomputed_fingerprint_reason"),
            }
            for r in rows_1
            if (r.get("provenance_summary") or {}).get("original_vs_recomputed_fingerprint_status") == "NOT_COMPARABLE"
        ]
        lineage_07 = {
            "spec_version": SPEC_VERSION_KH01A_R1,
            "schema_version": SCHEMA_VERSION_KH01A_R1,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "dataset_sha256": actual_dataset_sha,
            "reconstruction_algo_version": RECONSTRUCTION_ALGO_VERSION_KH01A_R1,
            "reconstruction_algo_sha256": RECONSTRUCTION_ALGO_SHA256_KH01A_R1,
            "full_original_market_decision_context_persisted": False,
            "full_original_runtime_context_completeness_status": "NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS",
            "persisted_artifacts_used": {
                "decision_records": "aurum-desk/docs/artifacts/v17_9_3_1_2_10/20261107T150245Z_b772a03bb7cc/10_canonical_decision_inputs.jsonl.gz",
                "verified_feature_snapshots": "aurum-desk/docs/artifacts/v17_9_3_1_2_10/20261107T150245Z_b772a03bb7cc/07_verified_causal_features.jsonl.gz",
            },
            "fingerprint_comparison_counts_all_170": forensics_06["original_vs_recomputed_fingerprint_census_all_170"],
            "not_comparable_rows_detail": not_comp_rows,
            "two_independent_runs_semantic_hash_identical": two_run_identical,
            "run_1_aggregate_row_sha256": canonical_hash(run1_shas),
            "run_2_aggregate_row_sha256": canonical_hash(run2_shas),
        }
        write_json_durable_atomic(
            run_dir / "07_original_snapshot_vs_asof_reconstruction_fingerprint_truth.json",
            lineage_07,
        )
        monitor.check()

        # STAGE 5: Multi-Worker Determinism across Forensics 170 Rows + Replay 242 Episodes + Pytest Subset (12)
        print("[KH-01A.R1 Stage 5] Running w=1, w=2, w=4, w=1_repeat determinism on 170 forensic rows & 242 replay episodes...", flush=True)
        si0_closed_166 = [ep for ep in si0_nb_eps if str(ep.get("outcome_label") or "").upper() in ("WIN", "LOSS")]
        forensics_w124 = asyncio.run(
            run_forensics_170_w1_w2_w4_determinism_r1(
                owned_temp_root,
                si0_closed_eps=si0_closed_166,
                si8_losses=si8_losses,
                si9_losses=si9_losses,
            )
        )
        replay_w124_doc, replay_det_workers = run_worker_1_2_4_and_repeat_determinism_v17931211(
            si0_nb_eps, bars_1m, run_id=run_id, tested_sha=sha
        )
        replay_parity_chk = verify_execution_not_synthetic_v17931211(
            collection_manifest=junit_11_disk["collection_manifest"],
            shard_manifest=junit_11_disk["shard_manifest"],
            parity_manifest=replay_w124_doc,
        )
        replay_proc_chk = verify_bounded_async_workers_v17931213(
            replay_det_workers,
            expected_run_id=run_id,
            expected_tested_sha=sha,
            max_allowed_workers=4,
            active_orphan_pids=[],
        )
        replay_w124_status = (
            "PASS"
            if replay_w124_doc.get("status") == "PASS"
            and replay_parity_chk.get("status") == "PASS"
            and replay_proc_chk.get("status") == "PASS"
            else "FAIL"
        )
        worker_12 = {
            "spec_version": SPEC_VERSION_KH01A_R1,
            "schema_version": SCHEMA_VERSION_KH01A_R1,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "pytest_controlled_subset_w1_w2_w4_repeat": subset_bench,
            "pytest_subset_w1_w2_w4_repeat_outcome_hash_identical": subset_bench["all_4_runs_outcome_sha256_identical"],
            "measured_speedup_w2_vs_w1": subset_bench["w2_speedup_vs_w1"],
            "measured_speedup_w4_vs_w1": subset_bench["w4_speedup_vs_w1"],
            "measured_speedup_w_parallel_vs_w1": subset_bench["best_parallel_speedup_vs_w1"],
            "forensics_170_w1_w2_w4_repeat": forensics_w124,
            "forensics_170_w1_w2_w4_repeat_row_hash_identical": forensics_w124["all_4_runs_row_hashes_identical"],
            "replay_slice_w1_w2_w4_repeat_parity": replay_w124_doc,
            "replay_slice_parity_verification": replay_parity_chk,
            "replay_slice_worker_process_verification": replay_proc_chk,
            "replay_slice_w1_w2_w4_repeat_parity_status": replay_w124_status,
        }
        write_json_durable_atomic(run_dir / "12_worker_w1_w2_w4_repeat_determinism_and_speed.json", worker_12)
        monitor.check()

        # STAGE 6: Full-Population 56,710 A1 Streaming Evaluator Parity + 246-Episode 3-Adapter Parity (10)
        print("[KH-01A.R1 Stage 6] Running bounded streaming 56,710 A1 evaluator parity + 246-episode 3-adapter parity...", flush=True)
        full_a1_parity = asyncio.run(
            run_streaming_full_56710_a1_parity_r1(
                owned_temp_root,
                baseline_decisions,
                num_workers=min(3, args.workers),
            )
        )

        # Verify 6,375 SI8 incremental discovery fingerprints & risk caps
        si8_risk_violations = sum(
            1 for r in si8_decisions if float(r.get("planned_net_risk_usd") or 0.0) > 2.500001
        )
        si8_stream_sha = canonical_hash([
            (str(r["opportunity_id"]), str(r.get("decision_fingerprint")), str(r.get("trade_action_fingerprint")))
            for r in sorted(si8_decisions, key=lambda x: str(x["opportunity_id"]))
        ])

        # Verify ALL 242 SI0 scheduled + 4 unique SI8/SI9 = 246 episodes across all 3 execution-context adapters!
        bars_5m = list(datasets["5m"])
        bars_15m = list(datasets["15m"])
        bars_1h = list(datasets["1H"])
        bars_4h = list(datasets["4H"])
        bars_1d = list(datasets["1D"])
        open_1m = [int(b.get("open_time") or b.get("timestamp") or 0) for b in bars_1m]

        unique_si89_eps_map: Dict[str, Mapping[str, Any]] = {}
        for ep in list(si8_losses) + list(si9_losses):
            unique_si89_eps_map.setdefault(str(ep["opportunity_id"]), ep)
        cohort_246 = list(si0_nb_eps) + [unique_si89_eps_map[k] for k in sorted(unique_si89_eps_map.keys())]

        adapter_246_mismatches: List[Dict[str, Any]] = []
        adapter_246_digests: List[Tuple[str, str]] = []
        max_risk_observed_246 = 0.0

        for ep in cohort_246:
            oid = str(ep["opportunity_id"])
            ts_ms = int(ep["decision_timestamp_ms"])
            m_id = str(ep["model_id"])
            direction = str(ep["direction"]).upper()
            e_ref = float(ep.get("entry_reference") or ep.get("entry_fill_price") or 4000.0)
            s_sl = float(ep.get("selected_sl") or 0.0)
            t_tp = float(ep.get("take_profit") or 0.0)
            max_risk_observed_246 = max(max_risk_observed_246, float(ep.get("planned_net_risk_usd") or 0.0))

            i1 = bisect.bisect_right(close_ts["1m"], ts_ms)
            i5 = bisect.bisect_right(close_ts["5m"], ts_ms)
            i15 = bisect.bisect_right(close_ts["15m"], ts_ms)
            ih1 = bisect.bisect_right(close_ts["1H"], ts_ms)
            ih4 = bisect.bisect_right(close_ts["4H"], ts_ms)
            i_open = bisect.bisect_left(open_1m, ts_ms)
            m1_sl = bars_1m[max(0, i1 - 180) : i1]

            ctx_hashes: Dict[str, str] = {}
            for ctx_m in ("REPLAY_BACKTEST", "MARKET_SHADOW", "PRODUCTION_ORCHESTRATOR_DRY_RUN"):
                out_m = evaluate_shared_delayed_entry_adapter_v17931213(
                    execution_context_mode=ctx_m,
                    activation_flags={"LIVE": False, "PAPER": False, "AUTO_LIVE": False, "AUTO_PAPER": False, "enabled_in_production": False},
                    variant_id="D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
                    opportunity_id=oid,
                    episode_key=str(ep.get("episode_key") or f"pre_{oid}"),
                    model_id=m_id,
                    direction=direction,
                    decision_timestamp_ms=ts_ms,
                    baseline_entry=e_ref,
                    baseline_sl=s_sl,
                    baseline_tp=t_tp,
                    closed_m1=m1_sl,
                    closed_m5=bars_5m[max(0, i5 - 60) : i5],
                    closed_m15=bars_15m[max(0, i15 - 60) : i15],
                    closed_h1=bars_1h[max(0, ih1 - 60) : ih1],
                    closed_h4=bars_4h[max(0, ih4 - 60) : ih4],
                    forward_bars=bars_1m[i_open : min(len(bars_1m), i_open + 195)],
                    pre_entry_features={"require_true_unicorn": True},
                    fill_mode="NEXT_BAR_OPEN_BID_ASK",
                    dataset_sha=actual_dataset_sha,
                )
                stripped = {k: v for k, v in out_m.items() if k != "adapter_execution_context_mode"}
                ctx_hashes[ctx_m] = canonical_hash(stripped)
            if len(set(ctx_hashes.values())) != 1:
                adapter_246_mismatches.append({"opportunity_id": oid, "hashes_by_context": ctx_hashes})
            adapter_246_digests.append((oid, ctx_hashes["REPLAY_BACKTEST"]))

        # Also preserve the 12-sample continuity check vs PREPATCH_BASELINE_REFERENCE
        v19_loss_rows = list(iter_jsonl_gz(V19_REF_RUN / "11_all_157_si0_loss_preentry_expost_rows.jsonl.gz"))
        sample_target_oids = {r["opportunity_id"] for r in v19_loss_rows[:12]}
        sample_opps = [d for d in v130_canonical_inputs if d.get("opportunity_id") in sample_target_oids][:12]
        evaluator = SharedStrategyEvaluator()
        sample_12_fps: List[Dict[str, Any]] = []
        for d in sample_opps:
            ts_ms = int(d["decision_timestamp_ms"])
            m_id = str(d["model_id"])
            direction = str(d["direction"]).upper()
            i1 = bisect.bisect_right(close_ts["1m"], ts_ms)
            i5 = bisect.bisect_right(close_ts["5m"], ts_ms)
            i15 = bisect.bisect_right(close_ts["15m"], ts_ms)
            ih1 = bisect.bisect_right(close_ts["1H"], ts_ms)
            ih4 = bisect.bisect_right(close_ts["4H"], ts_ms)
            id1 = bisect.bisect_right(close_ts["1D"], ts_ms)
            m1_sl240 = bars_1m[max(0, i1 - 240) : i1]
            cur_px = float(m1_sl240[-1]["close"])
            ctx12 = build_market_decision_context(
                symbol="XAUUSDT",
                decision_timestamp_ms=ts_ms,
                m1_candles=m1_sl240,
                m5_candles=bars_5m[max(0, i5 - 120) : i5],
                m15_candles=bars_15m[max(0, i15 - 120) : i15],
                h1_candles=bars_1h[max(0, ih1 - 120) : ih1],
                h4_candles=bars_4h[max(0, ih4 - 120) : ih4],
                d1_candles=bars_1d[max(0, id1 - 60) : id1],
                bid=round(cur_px - 0.10, 2),
                ask=round(cur_px + 0.10, 2),
                quote_timestamp_ms=ts_ms,
                quote_source_kind="SYNTHETIC_RESEARCH_QUOTE",
            )
            sn12 = evaluator.evaluate_model_direction(context=ctx12, model_id=m_id, direction=direction)
            sample_12_fps.append({
                "opportunity_id": d["opportunity_id"],
                "decision_timestamp_ms": ts_ms,
                "model_id": m_id,
                "direction": direction,
                "decision_status": sn12.decision_status,
                "rejection_reason_codes": list(sn12.rejection_reason_codes),
                "decision_fingerprint": sn12.decision_fingerprint,
                "trade_action_fingerprint": sn12.trade_action_fingerprint,
                "entry_reference": sn12.entry_reference,
                "selected_sl": sn12.selected_sl,
                "take_profit": sn12.take_profit,
                "net_rr": sn12.net_rr,
                "hypothetical_quantity": sn12.hypothetical_quantity,
            })
        sample_12_sha = canonical_hash(sample_12_fps)

        si0_now = si_results_by_policy["SI0_OFF_BASELINE"]["replays_by_fill_mode"]["NEXT_BAR_OPEN_BID_ASK"]
        si8_now = si_results_by_policy["SI8_RESEARCH_DISCOVERY_REPAIRED"]["replays_by_fill_mode"]["NEXT_BAR_OPEN_BID_ASK"]
        si9_now = si_results_by_policy["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["replays_by_fill_mode"]["NEXT_BAR_OPEN_BID_ASK"]
        acct_match = (
            si0_now["ledger_sha256"] == PREPATCH_BASELINE_REFERENCE["prepatch_si0_nb_accounting"]["ledger_sha256"]
            and si8_now["ledger_sha256"] == PREPATCH_BASELINE_REFERENCE["prepatch_si8_nb_accounting"]["ledger_sha256"]
            and si9_now["ledger_sha256"] == PREPATCH_BASELINE_REFERENCE["prepatch_si9_nb_accounting"]["ledger_sha256"]
        )

        trading_parity_ok = (
            full_a1_parity["status"] == "PASS"
            and len(adapter_246_mismatches) == 0
            and si8_risk_violations == 0
            and max_risk_observed_246 <= 2.500001
            and acct_match
            and sample_12_sha == PREPATCH_BASELINE_REFERENCE["prepatch_evaluator_fingerprints_sha256"]
        )
        parity_10 = {
            "spec_version": SPEC_VERSION_KH01A_R1,
            "schema_version": SCHEMA_VERSION_KH01A_R1,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "TRADING_PARITY": "PASS" if trading_parity_ok else "FAIL",
            "FORENSIC_RECOMPUTATION_STATUS": "FORENSIC_RECOMPUTATION_CHANGED_AS_EXPECTED",
            "full_universe_denominator_breakdown": {
                "canonical_universe_total": len(v130_canonical_inputs),
                "a1_v1792_baseline_opportunities": len(baseline_decisions),
                "si8_incremental_opportunities": len(si8_decisions),
                "si0_scheduled_plus_si89_unique_3_adapter_cohort": len(cohort_246),
            },
            "full_a1_56710_streaming_evaluator_parity": full_a1_parity,
            "full_a1_56710_evaluated_count": full_a1_parity["evaluated_count"],
            "full_a1_56710_mismatches_count": full_a1_parity["mismatches_count"],
            "si8_6375_incremental_universe_check": {
                "count": len(si8_decisions),
                "risk_cap_gt_2_50_violations": si8_risk_violations,
                "sorted_fingerprints_sha256": si8_stream_sha,
            },
            "three_adapter_246_cohort_parity": {
                "evaluated_count": len(cohort_246),
                "mismatches_count": len(adapter_246_mismatches),
                "mismatches": adapter_246_mismatches,
                "max_planned_net_risk_usd_observed": round(max_risk_observed_246, 4),
                "risk_cap_le_2_50_usd_verified": max_risk_observed_246 <= 2.500001,
                "sorted_246_adapter_sha256": canonical_hash(sorted(adapter_246_digests)),
            },
            "three_adapter_246_evaluated_count": len(cohort_246),
            "three_adapter_246_mismatches_count": len(adapter_246_mismatches),
            "continuity_12_sample_evaluator_sha256_match": (
                sample_12_sha == PREPATCH_BASELINE_REFERENCE["prepatch_evaluator_fingerprints_sha256"]
            ),
            "si0_si8_si9_ledger_sha256_exact_match": acct_match,
        }
        write_json_durable_atomic(run_dir / "10_full_trade_action_and_3_adapter_parity.json", parity_10)
        monitor.check()

        # STAGE 7: Real Adversarial Fault-Injection Suite (08) & Fresh-OOS Registry (14)
        print("[KH-01A.R1 Stage 7] Running R1 adversarial fault-injection suite & Fresh-OOS escrow audit...", flush=True)
        fault_suite_08 = run_kh_01a_r1_adversarial_fault_suite(
            tested_sha=sha,
            run_id=run_id,
            r1_forensics_bundle=forensics_run_1,
            si0_all_primary_episodes=si0_nb_eps,
            si8_loss_rows=si8_losses,
            si9_loss_rows=si9_losses,
            decision_inputs_by_opp_id=decisions_by_oid,
            feature_ledger_by_opp_id=all_features_map,
            datasets=datasets,
            close_ts_index=close_ts,
            canonical_bar_close_by_tf_open=bar_close_by_tf_open,
            verifier_script_path=APP_ROOT / "tools/kh_01a_r1_independent_verifier.py",
        )
        if fault_suite_08["status"] != "PASS":
            raise RuntimeError(f"R1_ADVERSARIAL_FAULT_SUITE_FAILED:{fault_suite_08['failed_fault_injections']}")
        write_json_durable_atomic(run_dir / "08_adversarial_fault_injection_red_green_suite.json", fault_suite_08)

        fresh_oos_14 = build_dataset_registry_and_fresh_oos_audit_kh01a(REPO_ROOT, datasets, gap_report)
        fresh_oos_14["spec_version"] = SPEC_VERSION_KH01A_R1
        fresh_oos_14["schema_version"] = SCHEMA_VERSION_KH01A_R1
        fresh_oos_14["run_id"] = run_id
        fresh_oos_14["tested_executable_sha"] = sha
        fresh_oos_14["STRATEGY_ECONOMIC_EDGE_VERDICT"] = "NEGATIVE_NO_PROVEN_EDGE"
        fresh_oos_14["ACTIVATION_READY"] = "NO"
        write_json_durable_atomic(run_dir / "14_fresh_oos_registry_untouched_and_economic_verdict.json", fresh_oos_14)

        # STAGE 8: Cleanup Owned Temp Root, Stop Resource Monitor, Seal Preflight (13, 00)
        temp_cleanup_info = cleanup_owned_temp_root(owned_temp_root, f"r1_{run_id}")
        res_snap = monitor.stop()
        resource_13 = {
            "spec_version": SPEC_VERSION_KH01A_R1,
            "schema_version": SCHEMA_VERSION_KH01A_R1,
            "run_id": run_id,
            "tested_executable_sha": sha,
            **res_snap,
            "temp_dirs_cleaned_with_sentinel_check": bool(
                temp_cleanup_info.get("cleaned") and temp_cleanup_info.get("sentinel_verified")
            ),
            "temp_cleanup_receipt": temp_cleanup_info,
        }
        write_json_durable_atomic(run_dir / "13_disk_ram_cpu_child_process_temp_cleanup.json", resource_13)

        src_after = source_manifest()
        prior_after = prior_all_immutable_bundles_hash()
        preflight_00["source_tree_unchanged_during_run"] = src_before["sha256"] == src_after["sha256"]
        preflight_00["prior_artifacts_unchanged_during_run"] = prior_before["sha256"] == prior_after["sha256"]
        preflight_00["completed_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_json_durable_atomic(run_dir / "00_branch_sha_config_dataset_preflight.json", preflight_00)

        # STAGE 9: Dynamic Validator-Driven Gate Receipts (F4: zero hardcoded PASS!)
        print("[KH-01A.R1 Stage 9] Executing real semantic validators to mint dynamic gate receipts...", flush=True)

        def _val_r1_01() -> Dict[str, Any]:
            sf = verify_source_freeze_v17931211(REPO_ROOT, sha)
            viols = list(sf.get("violations") or [])
            if not preflight_00["source_tree_unchanged_during_run"] or not preflight_00["prior_artifacts_unchanged_during_run"]:
                viols.append("SOURCE_OR_PRIOR_ARTIFACTS_CHANGED")
            return {"status": "PASS" if not viols else "FAIL", "violations": viols}

        def _val_r1_02() -> Dict[str, Any]:
            ok = (
                callgraph_01["macro_call_site_ast_audit"]["invalid_production_call_sites_count"] == 0
                and macro_diff_02["red_reproduction_confirmed"] is True
                and macro_diff_02["green_zero_invalid_confirmed"] is True
            )
            return {"status": "PASS" if ok else "FAIL", "violations": [] if ok else ["MACRO_RED_GREEN_OR_AST_FAILED"]}

        def _val_r1_03() -> Dict[str, Any]:
            ok = macro_dst_03["status"] == "PASS"
            return {"status": "PASS" if ok else "FAIL", "violations": [] if ok else ["MACRO_DST_BOUNDARY_FAILED"]}

        def _val_r1_04() -> Dict[str, Any]:
            disk_rows = list(iter_jsonl_gz(run_dir / "05_r1_preentry_170_rows.jsonl.gz"))
            disk_06 = json.loads((run_dir / "06_feature_evidence_origin_missing_invalid_source_hash_census.json").read_text(encoding="utf-8"))
            return validate_kh_01a_r1_forensics_bundle_strict(
                disk_rows, disk_06, canonical_bar_close_by_tf_open=bar_close_by_tf_open, persisted_f07_by_oid=all_features_map
            )

        def _val_r1_05() -> Dict[str, Any]:
            return _val_r1_04()

        def _val_r1_06() -> Dict[str, Any]:
            ok = (
                lineage_07["two_independent_runs_semantic_hash_identical"] is True
                and int(lineage_07["fingerprint_comparison_counts_all_170"].get("MISMATCH", 1)) == 0
                and int(forensics_06["anti_hindsight_contract"]["max_known_at_minus_decision_ts_ms"]) <= 0
                and int(forensics_06["anti_hindsight_contract"]["max_closed_bar_close_minus_decision_ts_ms"]) <= 0
            )
            return {"status": "PASS" if ok else "FAIL", "violations": [] if ok else ["LINEAGE_OR_CLOCK_FAILED"]}

        def _val_r1_07() -> Dict[str, Any]:
            lc_d = forensics_06["ledger_conservation"]
            ok = (
                lc_d["si0_scheduled_total"] == 242
                and lc_d["si0_closed_total"] == 166
                and lc_d["si0_wins_count"] == 9
                and lc_d["si0_losses_count"] == 157
                and lc_d["si8_si9_unique_losses_count"] == 4
            )
            return {"status": "PASS" if ok else "FAIL", "violations": [] if ok else ["COHORT_ACCOUNTING_MISMATCH"]}

        def _val_r1_08() -> Dict[str, Any]:
            jb_chk = verify_fresh_junit_bijection_v17931211(
                run_dir,
                junit_11_disk["collection_manifest"],
                junit_11_disk["shard_manifest"],
                expected_run_id=run_id,
                expected_tested_sha=sha,
                require_shard_schedule=True,
            )
            viols = list(jb_chk.get("violations") or [])
            if fault_suite_08["status"] != "PASS":
                viols.append("FAULT_SUITE_08_FAILED")
            return {"status": "PASS" if not viols else "FAIL", "violations": viols}

        def _val_r1_09() -> Dict[str, Any]:
            ok = (
                replay_09["p0a_accounting_status"] == "PASS"
                and replay_09["lineage_30_partitions_status"] == "PASS"
                and replay_09["d4_execution_truth_status"] == "PASS"
                and replay_09["target_267_plus_40_audit"]["verification_status"] == "PASS"
            )
            return {"status": "PASS" if ok else "FAIL", "violations": [] if ok else ["BACKTEST_RECONCILIATION_FAILED"]}

        def _val_r1_10() -> Dict[str, Any]:
            ok = parity_10["TRADING_PARITY"] == "PASS"
            return {"status": "PASS" if ok else "FAIL", "violations": [] if ok else ["TRADING_OR_ADAPTER_PARITY_FAILED"]}

        def _val_r1_11() -> Dict[str, Any]:
            ok = (
                worker_12["pytest_subset_w1_w2_w4_repeat_outcome_hash_identical"] is True
                and worker_12["forensics_170_w1_w2_w4_repeat_row_hash_identical"] is True
                and worker_12["replay_slice_w1_w2_w4_repeat_parity_status"] == "PASS"
            )
            return {"status": "PASS" if ok else "FAIL", "violations": [] if ok else ["WORKER_DETERMINISM_FAILED"]}

        def _val_r1_12() -> Dict[str, Any]:
            ok = (
                1 <= int(resource_13["max_children_observed"]) <= 4
                and int(resource_13["orphan_pids_at_cleanup_count"]) == 0
                and resource_13["disk_reserve_breached"] is False
                and resource_13["temp_dirs_cleaned_with_sentinel_check"] is True
            )
            return {"status": "PASS" if ok else "FAIL", "violations": [] if ok else ["RESOURCE_OR_CLEANUP_SAFETY_FAILED"]}

        def _val_r1_13() -> Dict[str, Any]:
            st = str(fresh_oos_14.get("FRESH_OOS_VERDICT") or "FAIL")
            return {
                "status": st,
                "violations": ["NO_UNTOUCHED_POST_FREEZE_DATASET_AVAILABLE"] if st == "BLOCKED_NO_UNTOUCHED_DATA" else [],
            }

        def _val_r1_14() -> Dict[str, Any]:
            return {
                "status": "PENDING_REMOTE_PUSH",
                "violations": ["AWAITING_PHASE_B_GIT_PUSH_AND_PHASE_C_REMOTE_BLOB_READBACK"],
            }

        dynamic_specs: List[Tuple[str, str, Any]] = [
            ("G-KH01A-R1-01", "00_branch_sha_config_dataset_preflight.json", _val_r1_01),
            ("G-KH01A-R1-02", "02_macro_reversed_args_red_green_diff.json", _val_r1_02),
            ("G-KH01A-R1-03", "03_macro_dst_schedule_semantics_and_invalid_samples.json", _val_r1_03),
            ("G-KH01A-R1-04", "04_previous_vs_r1_170_casebook_distribution_diff.json", _val_r1_04),
            ("G-KH01A-R1-05", "06_feature_evidence_origin_missing_invalid_source_hash_census.json", _val_r1_05),
            ("G-KH01A-R1-06", "07_original_snapshot_vs_asof_reconstruction_fingerprint_truth.json", _val_r1_06),
            ("G-KH01A-R1-07", "06_feature_evidence_origin_missing_invalid_source_hash_census.json", _val_r1_07),
            ("G-KH01A-R1-08", "11_pytest_collection_junit_closed_shard_exit_bijection.json", _val_r1_08),
            ("G-KH01A-R1-09", "09_all_3_fill_modes_si0_to_si9_one_position_backtest_oos_cost_stress.json", _val_r1_09),
            ("G-KH01A-R1-10", "10_full_trade_action_and_3_adapter_parity.json", _val_r1_10),
            ("G-KH01A-R1-11", "12_worker_w1_w2_w4_repeat_determinism_and_speed.json", _val_r1_11),
            ("G-KH01A-R1-12", "13_disk_ram_cpu_child_process_temp_cleanup.json", _val_r1_12),
            ("G-KH01A-R1-13", "14_fresh_oos_registry_untouched_and_economic_verdict.json", _val_r1_13),
            ("G-KH01A-R1-14", "00_branch_sha_config_dataset_preflight.json", _val_r1_14),
        ]

        receipts_by_gate: Dict[str, EvidenceReceiptV17931219] = {}
        for gid, rel_target, v_fn in dynamic_specs:
            rcpt_obj, _ = build_dynamic_validated_gate_receipt_r1(
                gate_id=gid,
                run_dir=run_dir,
                run_id=run_id,
                tested_sha=sha,
                target_rel_path=rel_target,
                validator_fn=v_fn,
                write_json_fn=write_json_durable_atomic,
            )
            receipts_by_gate[gid] = rcpt_obj

        verdicts_16 = evaluate_kh_01a_r1_14_gates(
            run_dir=run_dir,
            run_id=run_id,
            branch=branch,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            preflight_00=preflight_00,
            callgraph_01=callgraph_01,
            macro_diff_02=macro_diff_02,
            macro_dst_03=macro_dst_03,
            casebook_diff_04=casebook_diff_04,
            forensics_06=forensics_06,
            lineage_07=lineage_07,
            fault_suite_08=fault_suite_08,
            replay_09=replay_09,
            parity_10=parity_10,
            junit_11=junit_11_disk,
            worker_12=worker_12,
            resource_13=resource_13,
            fresh_oos_14=fresh_oos_14,
            receipts_by_gate=receipts_by_gate,
            publication_status="PENDING_REMOTE_PUSH",
        )
        write_json_durable_atomic(run_dir / "16_final_orthogonal_verdicts_open_blockers.json", verdicts_16)

        readme_md, handover_vi = render_kh_01a_r1_readme_and_handover_vi(
            run_id=run_id,
            branch=branch,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            macro_diff_02=macro_diff_02,
            casebook_diff_04=casebook_diff_04,
            forensics_06=forensics_06,
            lineage_07=lineage_07,
            fault_suite_08=fault_suite_08,
            replay_09=replay_09,
            parity_10=parity_10,
            junit_11=junit_11_disk,
            worker_12=worker_12,
            resource_13=resource_13,
            verdicts_16=verdicts_16,
        )
        write_text_durable_atomic(run_dir / "README_KH01A_R1.md", readme_md)
        write_text_durable_atomic(run_dir / "HANDOVER_KH01A_R1.txt", handover_vi)

        cv_chk = verify_kh_01a_r1_cross_view_consistency(
            readme_text=readme_md,
            handover_text=handover_vi,
            verdicts_16=verdicts_16,
            junit_11=junit_11_disk,
            forensics_06=forensics_06,
            parity_10=parity_10,
        )
        if cv_chk["status"] != "PASS":
            raise RuntimeError(f"R1_CROSS_VIEW_CONSISTENCY_FAILED:{cv_chk['violations']}")

        # STAGE 10: Separate-OS-Process Independent Disk Verifier Sealing 15_independent_verifier...
        print("[KH-01A.R1 Stage 10] Launching separate-process kh_01a_r1_independent_verifier.py...", flush=True)
        ind_cmd = [
            sys.executable,
            str(APP_ROOT / "tools/kh_01a_r1_independent_verifier.py"),
            "--run-dir",
            str(run_dir),
            "--expected-run-id",
            run_id,
            "--expected-tested-sha",
            sha,
        ]
        ind_proc = subprocess.run(ind_cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
        print(ind_proc.stdout, flush=True)
        if ind_proc.returncode != 0:
            print(ind_proc.stderr, file=sys.stderr, flush=True)
            raise RuntimeError(f"R1_INDEPENDENT_DISK_VERIFIER_FAILED:rc={ind_proc.returncode}")

        print(f"KH-01A.R1 VERIFICATION SUCCEEDED: run_dir={run_dir}", flush=True)
        return 0

    except BaseException:
        monitor.reap_all_descendants()
        monitor.stop()
        if owned_temp_root.exists():
            try:
                cleanup_owned_temp_root(owned_temp_root, f"r1_{run_id}")
            except Exception:
                pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
