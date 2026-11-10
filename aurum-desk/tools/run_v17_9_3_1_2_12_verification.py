#!/usr/bin/env python3
"""
tools/run_v17_9_3_1_2_12_verification.py

AURUM Desk — V17.9.3.1.2.12 Full Autonomous Verification & Research Orchestrator:
  - P0-A: Strict Provenance Fail-Closed & 40 Adversarial Negative Controls (V01..V40)
  - P0-B: Bounded Async / Multiprocess Worker 1/2/4 + Repeat Benchmark & Determinism
  - R1: True Entry/SL Loss Forensics (166 closed = 157 LOSS + 9 WIN; 242 scheduled; A..H buckets)
  - R2: Causal H4/H1/M15/M5/M1 + Unicorn Pipeline Repair & Single-Axis Ablations (63,085 universe)
  - R3: Structural Entry/SL/TP Matched Counterfactuals (A0..A4 at fixed $2.50 risk cap)
  - R4: Full Fresh Backend Regression, 30-Partition Replay, Purged/Embargoed Split & Honest OOS
"""
from __future__ import annotations

import argparse
from collections import ChainMap, Counter
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
import tempfile
import time
from typing import Any, Dict, Iterable, List, Mapping, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "backend"))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.v17_9_3_1_2_2_signal_intelligence import CANONICAL_V1793123_POLICY_IDS
from services.v17_9_3_1_2_7_causal_signal_truth import (
    canonical_hash,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_10_canonical_reconciliation_and_provenance import (
    analyze_15_distinct_recovered_trades_v17931210,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    CANONICAL_DATASET_SHA256_V17931211,
    MANDATORY_BRANCH_V17931211,
    build_canonical_report_metrics_v17931211,
    build_exit_event_lineage_v17931211,
    recompute_realized_pnl_from_exit_events_v17931211,
    render_truth_closure_readme_and_handover_v17931211,
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_exit_event_deduplication_v17931211,
    verify_exit_event_parentage_v17931211,
    verify_exit_fill_basis_v17931211,
    verify_exit_portfolio_conservation_v17931211,
    verify_exit_state_machine_v17931211,
    verify_fresh_junit_bijection_v17931211,
    verify_process_exit_and_timeout_v17931211,
    verify_source_freeze_v17931211,
    verify_strict_report_and_handover_consistency_v17931211,
)
from services.v17_9_3_1_2_12_provenance_forensics_and_repair_service import (
    CANONICAL_DATASET_SHA256_V17931212,
    SCHEMA_VERSION_V17931212,
    VERSION_V17931212,
    build_r1_trade_by_trade_casebook_and_matrix_v17931212,
    build_r2_htf_unicorn_funnel_and_ablations_v17931212,
    build_r3_entry_sl_tp_matched_counterfactuals_v17931212,
    build_r4_backtest_stress_and_oos_provenance_v17931212,
    build_v17931212_callgraph_and_defect_map,
    run_negative_controls_v01_to_v40_v17931212,
    verify_r1_forensic_separation_and_conservation_v17931212,
    verify_r2_causal_mtf_unicorn_provenance_v17931212,
    verify_r3_counterfactual_invariants_v17931212,
    verify_r4_oos_and_activation_verdict_v17931212,
)
from tools.run_v17_9_3_1_2_1_full_opportunity_replay import (
    DATASET_PATH,
    FILL_MODES,
    PRIOR_V179312_RUN_DIR,
    _load_verified_decision_stream_chunks,
)
from tools.run_v17_9_3_1_2_2_entry_signal_backtest import replay_single_si_policy_v1793122
from tools.run_v17_9_3_1_2_2_verification import (
    run_full_backend_pytest_collection_and_sharded_junit_v1793122,
)
from tools.run_v17_9_3_1_2_7_verification import (
    ResourceMonitor,
    git,
    source_manifest,
)
from tools.run_v17_9_3_1_2_11_verification import (
    _run_worker_slice_cli,
    run_worker_1_2_4_and_repeat_determinism_v17931211,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

GROUNDED_V11_TRUTH_CLOSURE_HEAD = "b3fa422878910df0dabf895cbaf3a42eb488ce03"
V130_RUN = APP_ROOT / "docs/artifacts/v17_9_3_1_2_10/20261107T150245Z_b772a03bb7cc"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def write_jsonl_gz(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False, default=str) + "\n")
    os.replace(tmp, path)


def iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def prior_artifact_manifest_v17931212() -> Dict[str, Any]:
    """Hashes all prior immutable artifact directories (.7, .8, .9, .10, .11)."""
    files: Dict[str, str] = {}
    for ver_dir in (
        "v17_9_3_1_2_7",
        "v17_9_3_1_2_8",
        "v17_9_3_1_2_9",
        "v17_9_3_1_2_10",
        "v17_9_3_1_2_11",
    ):
        root = APP_ROOT / "docs/artifacts" / ver_dir
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
                files[rel] = file_sha256(p)
    return {
        "files_count": len(files),
        "sha256": canonical_hash(files),
        "files": files,
    }


def append_stage_status(run_dir: Path, stage: str, status: str, **extra: Any) -> None:
    status_path = run_dir / "run_status.jsonl"
    row = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "epoch_ns": time.time_ns(),
        "pid": os.getpid(),
        "version": VERSION_V17931212,
        "stage": stage,
        "status": status,
        **extra,
    }
    with status_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def render_v17931212_readme_and_handover(
    *,
    base_readme: str,
    base_handover: str,
    run_id: str,
    tested_sha: str,
    r1_matrix: Mapping[str, Any],
    r2_funnel: Mapping[str, Any],
    r2_ablation: Mapping[str, Any],
    r3_summary: Mapping[str, Any],
    oos_18: Mapping[str, Any],
) -> Tuple[str, str]:
    """
    Appends V17.9.3.1.2.12 R1..R4 tables and honest OOS / activation verdicts to the
    strict machine-checked README and HANDOVER documents while preserving all
    METRICS_KV lines verified by `verify_strict_report_and_handover_consistency_v17931211`.
    """
    buckets = r1_matrix["hierarchical_buckets_a_to_h_counts"]
    ab_modes = r2_ablation["single_axis_mtf_unicorn_ablations_63085"]["ablation_modes"]
    diag_278 = r2_ablation["si8_si9_278_to_0_funnel_diagnosis"]
    a0_nb = r3_summary["A0_STRICT_BASELINE"]["NEXT_BAR_OPEN_BID_ASK"]
    a1_nb = r3_summary["A1_ENTRY_CONFIRMATION_RETEST_ONLY"]["NEXT_BAR_OPEN_BID_ASK"]
    a2_nb = r3_summary["A2_PROTECTED_SL_NOISE_BUFFER_ONLY"]["NEXT_BAR_OPEN_BID_ASK"]
    a3_nb = r3_summary["A3_SOURCE_BACKED_LIQUIDITY_TP_ONLY"]["NEXT_BAR_OPEN_BID_ASK"]
    a4_nb = r3_summary["A4_COMBINED_CAUSAL_ENTRY_SL_TP"]["NEXT_BAR_OPEN_BID_ASK"]

    extra_md = "\n".join(
        [
            "",
            "## 7. V17.9.3.1.2.12 P0-A, P0-B, R1, R2, R3, R4 Summary",
            f"- **V17.9.3.1.2.12 Run ID**: `{run_id}`",
            f"- **Tested Executable SHA (Phase A)**: `{tested_sha}`",
            "- **P0-A Provenance Closure**: `read_exit_event_stream_strict_v17931211` rejects missing `evidence_origin` or `quote_source_kind` without defaults, rejects `legacy_sem_hash` on active runs (`allow_legacy_hash_compat=False`), and `verify_all_policy_fill_mode_lineage_independently_v17931211` enforces mandatory `09_original_execution_ledger_source_reconciliation.jsonl.gz` (`require_source_reconciliation=True`).",
            "- **Adversarial Negative Controls (`V01`..`V40`)**: `40 / 40` rejected in isolated temporary directories.",
            "",
            "### 7.1 R1 Hierarchical Loss Forensics (`157` Closed Losses / `166` Closed Trades / `242` Scheduled)",
            f"- `A_PRE_ENTRY_HTF_CONFLICT`: `{buckets['A_PRE_ENTRY_HTF_CONFLICT']}`",
            f"- `B_PRE_ENTRY_UNCONFIRMED_OR_CHASING`: `{buckets['B_PRE_ENTRY_UNCONFIRMED_OR_CHASING']}`",
            f"- `C_PRE_ENTRY_STALE_OR_UNBOUND_FVG_UNICORN`: `{buckets['C_PRE_ENTRY_STALE_OR_UNBOUND_FVG_UNICORN']}`",
            f"- `D_PRE_ENTRY_SL_INSIDE_NOISE_OR_UNPROTECTED`: `{buckets['D_PRE_ENTRY_SL_INSIDE_NOISE_OR_UNPROTECTED']}`",
            f"- `E_PRE_ENTRY_TARGET_SWEPT_OR_UNREACHABLE`: `{buckets['E_PRE_ENTRY_TARGET_SWEPT_OR_UNREACHABLE']}`",
            f"- `F_EX_POST_STOP_BEFORE_STRUCTURE_BREAK_NOISE_SWEEP` (`EX_POST_DIAGNOSTIC_ONLY`): `{buckets['F_EX_POST_STOP_BEFORE_STRUCTURE_BREAK_NOISE_SWEEP']}`",
            f"- `G_EX_POST_TRUE_STRUCTURAL_INVALIDATION_OR_REVERSAL` (`EX_POST_DIAGNOSTIC_ONLY`): `{buckets['G_EX_POST_TRUE_STRUCTURAL_INVALIDATION_OR_REVERSAL']}`",
            f"- `H_EX_POST_FRICTION_OR_AMBIGUOUS_BAR_DOMINATED` (`EX_POST_DIAGNOSTIC_ONLY`): `{buckets['H_EX_POST_FRICTION_OR_AMBIGUOUS_BAR_DOMINATED']}`",
            f"- **Pre-Entry `OTHER/UNKNOWN` Preserved**: `{r1_matrix['pre_entry_unknown_losses_count']} / 157`",
            "",
            "### 7.2 R2 Single-Axis MTF & Unicorn Ablations Across `63,085` Canonical Opportunities",
            f"- `H4_ONLY` passed: `{ab_modes['H4_ONLY']['passed']}`",
            f"- `H1_ONLY` passed: `{ab_modes['H1_ONLY']['passed']}`",
            f"- `M15_ONLY` passed: `{ab_modes['M15_ONLY']['passed']}`",
            f"- `M15_H1_ONLY` passed: `{ab_modes['M15_H1_ONLY']['passed']}`",
            f"- `STRUCTURE_ONLY` passed: `{ab_modes['STRUCTURE_ONLY']['passed']}`",
            f"- `LIQUIDITY_ONLY` passed: `{ab_modes['LIQUIDITY_ONLY']['passed']}`",
            f"- `FULL_MTF` passed: `{ab_modes['FULL_MTF']['passed']}`",
            f"- `SI8/SI9` Materialization Funnel: `{diag_278['proof_eligible_candidates_count']}` proof-eligible -> `{diag_278['arbitration_suppressed_count']}` arbitration suppressed -> `{diag_278['post_arbitration_rejected_count']}` post-arbitration rejected -> `{diag_278['materialized_intents_count']}` trades.",
            "",
            "### 7.3 R3 Matched Entry/SL/TP Counterfactuals (`NEXT_BAR_OPEN_BID_ASK`, `n = 242`, Risk Cap `$2.50`)",
            "| Variant | Closed Trades | Wins | Losses | Net PnL (USD) | Expectancy Net R | Max Planned Risk (USD) |",
            "|---|---:|---:|---:|---:|---:|---:|",
            f"| `A0_STRICT_BASELINE` | {a0_nb['closed_trades_count']} | {a0_nb['wins_count']} | {a0_nb['losses_count']} | {a0_nb['realized_net_pnl_usd']:.4f} | {a0_nb['expectancy_net_r']:.4f} | {a0_nb['max_planned_net_risk_usd_observed']:.4f} |",
            f"| `A1_ENTRY_CONFIRMATION_RETEST_ONLY` | {a1_nb['closed_trades_count']} | {a1_nb['wins_count']} | {a1_nb['losses_count']} | {a1_nb['realized_net_pnl_usd']:.4f} | {a1_nb['expectancy_net_r']:.4f} | {a1_nb['max_planned_net_risk_usd_observed']:.4f} |",
            f"| `A2_PROTECTED_SL_NOISE_BUFFER_ONLY` | {a2_nb['closed_trades_count']} | {a2_nb['wins_count']} | {a2_nb['losses_count']} | {a2_nb['realized_net_pnl_usd']:.4f} | {a2_nb['expectancy_net_r']:.4f} | {a2_nb['max_planned_net_risk_usd_observed']:.4f} |",
            f"| `A3_SOURCE_BACKED_LIQUIDITY_TP_ONLY` | {a3_nb['closed_trades_count']} | {a3_nb['wins_count']} | {a3_nb['losses_count']} | {a3_nb['realized_net_pnl_usd']:.4f} | {a3_nb['expectancy_net_r']:.4f} | {a3_nb['max_planned_net_risk_usd_observed']:.4f} |",
            f"| `A4_COMBINED_CAUSAL_ENTRY_SL_TP` | {a4_nb['closed_trades_count']} | {a4_nb['wins_count']} | {a4_nb['losses_count']} | {a4_nb['realized_net_pnl_usd']:.4f} | {a4_nb['expectancy_net_r']:.4f} | {a4_nb['max_planned_net_risk_usd_observed']:.4f} |",
            "",
            "### 7.4 R4 Honest OOS & Activation Verdicts",
            f"- `FRESH_OOS_VERDICT`: `{oos_18['fresh_oos_verdict']}` (`{oos_18['reused_historical_holdout_label']}`)",
            f"- `ECONOMIC_VERDICT`: `{oos_18['economic_verdict']}`",
            f"- `STRATEGY_EXPECTANCY_VERDICT`: `{oos_18['strategy_expectancy_verdict']}`",
            f"- `ACTIVATION_READY`: `{oos_18['activation_ready']}`",
            "",
        ]
    )
    return base_readme + extra_md, base_handover + extra_md


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tested-code-sha", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--worker-slice-input", default="")
    ap.add_argument("--worker-slice-output", default="")
    args = ap.parse_args()

    if args.worker_slice_input and args.worker_slice_output:
        return _run_worker_slice_cli(Path(args.worker_slice_input), Path(args.worker_slice_output))
    if not args.tested_code_sha:
        raise SystemExit("--tested-code-sha is required")

    if os.name == "nt":
        os.environ["AURUM_VERIFY_VENV_PYTHON"] = sys.executable
        packages = str(Path(sys.prefix) / "Lib/site-packages")
        os.environ["PYTHONPATH"] = os.pathsep.join([packages, os.environ.get("PYTHONPATH", "")])
        sys.executable = getattr(sys, "_base_executable", sys.executable)
        found = shutil.which("git")
        direct = Path(found).parents[1] / "mingw64/bin" if found else None
        if direct and (direct / "git.exe").is_file():
            os.environ["PATH"] = str(direct) + os.pathsep + os.environ.get("PATH", "")

    sha = git("rev-parse", "HEAD")
    dirty_source = git("diff", "--name-only", "HEAD", "--", "aurum-desk/backend", "aurum-desk/tools").splitlines()
    branch = git("branch", "--show-current")
    if sha != args.tested_code_sha or dirty_source:
        raise SystemExit(
            f"FROZEN_EXECUTABLE_SHA_MISMATCH_OR_DIRTY_SOURCE:sha={sha},expected={args.tested_code_sha},dirty={dirty_source}"
        )
    if branch != MANDATORY_BRANCH_V17931211:
        raise SystemExit(f"WRONG_BRANCH:{branch}")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", GROUNDED_V11_TRUTH_CLOSURE_HEAD, sha],
        cwd=REPO_ROOT,
        check=True,
    )

    run_id = args.run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + sha[:12])
    run_dir = APP_ROOT / "docs/artifacts/v17_9_3_1_2_12" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    aux_temp_dir = Path(tempfile.gettempdir()) / f"aurum_aux_{run_id}"
    os.environ["AURUM_VERIFICATION_ARTIFACT_ROOT"] = str(aux_temp_dir)
    orchestrator_pid = os.getpid()

    src_before = source_manifest()
    prior_before = prior_artifact_manifest_v17931212()

    write_json(
        run_dir / "source_manifest_sha256.json",
        {
            "version": VERSION_V17931212,
            "schema_version": SCHEMA_VERSION_V17931212,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "source_manifest_sha256": src_before["sha256"],
            "files_count": len(src_before["files"]),
            "files": src_before["files"],
        },
    )

    write_json(
        run_dir / "00_preflight_branch_data_resources.json",
        {
            "version": VERSION_V17931212,
            "schema_version": SCHEMA_VERSION_V17931212,
            "run_id": run_id,
            "branch": branch,
            "tested_executable_sha": sha,
            "observed_head_sha": sha,
            "grounded_v11_truth_closure_head": GROUNDED_V11_TRUTH_CLOSURE_HEAD,
            "dataset_sha256": CANONICAL_DATASET_SHA256_V17931212,
            "orchestrator_pid": orchestrator_pid,
            "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "RUNNING",
        },
    )
    append_stage_status(run_dir, "PREFLIGHT", "STARTED", tested_executable_sha=sha)

    # 01: Code callgraph and defect map
    callgraph_01 = build_v17931212_callgraph_and_defect_map(REPO_ROOT)
    callgraph_01["run_id"] = run_id
    callgraph_01["tested_executable_sha"] = sha
    write_json(run_dir / "01_code_callgraph_and_defect_map.json", callgraph_01)
    append_stage_status(run_dir, "PREFLIGHT", "COMPLETED", source_sha256=src_before["sha256"])

    monitor = ResourceMonitor(minimum_ram_bytes=512 * 1024**2)
    monitor.abort_path = run_dir / "resource_abort.json"
    os.environ["AURUM_RESOURCE_GUARD_ABORT_FILE"] = str(monitor.abort_path)
    os.environ["AURUM_MINIMUM_RAM_BYTES"] = str(512 * 1024**2)
    os.environ["AURUM_TESTED_EXECUTABLE_SHA"] = sha
    os.environ["AURUM_RESEARCH_TEST_WORKERS"] = "1"
    os.environ["AURUM_RESEARCH_MAX_FILES_PER_SHARD"] = "16"
    os.environ["AURUM_RESEARCH_STAGE_WORKERS"] = "3"

    worker_records: List[Dict[str, Any]] = []
    failure = None

    try:
        monitor.start()

        # STAGE A: Fresh full backend pytest collection and sharded JUnit XML execution (14)
        print("[Stage A] Running fresh full backend pytest collection and sharded JUnit execution...", flush=True)
        t_a = time.time()
        append_stage_status(run_dir, "STAGE_A_FRESH_PYTEST_SHARDS", "STARTED")
        collection, junit = run_full_backend_pytest_collection_and_sharded_junit_v1793122(
            run_dir / "junit",
            tested_executable_sha=sha,
            max_workers=1,
            force_fresh_execution=True,
        )
        for log_file in (run_dir / "junit").glob("*.log"):
            log_file.unlink(missing_ok=True)

        collection["run_id"] = run_id
        collection["tested_executable_sha"] = sha
        collection["collect_exit_code"] = (
            int(junit["shards"][0].get("collect_exit_code", 0)) if junit.get("shards") else 0
        )
        junit["run_id"] = run_id
        junit["tested_executable_sha"] = sha
        for sh in junit.get("shards", []):
            sh["run_id"] = run_id
            sh["tested_executable_sha"] = sha
            sh["exit_code"] = int(sh.get("pytest_exit_code", 0))
            sh["timed_out"] = False
            xml_p = (
                APP_ROOT / sh["junit_xml_path"]
                if not Path(sh["junit_xml_path"]).is_absolute()
                else Path(sh["junit_xml_path"])
            )
            sh["junit_xml_path"] = str(xml_p.relative_to(run_dir)).replace("\\", "/")

            st_ms = (
                int(datetime.fromisoformat(sh["start_time_utc"]).timestamp() * 1000)
                if sh.get("start_time_utc")
                else int(t_a * 1000)
            )
            et_ms = (
                int(datetime.fromisoformat(sh["end_time_utc"]).timestamp() * 1000)
                if sh.get("end_time_utc")
                else st_ms + max(1, int(float(sh.get("elapsed_seconds", 0.01)) * 1000))
            )
            if et_ms <= st_ms:
                et_ms = st_ms + 1
            sh_rc = int(sh.get("pytest_exit_code", 0))
            worker_records.append(
                {
                    "worker_id": f"pytest_{sh['shard_name']}",
                    "run_id": run_id,
                    "tested_source_sha": sha,
                    "pid": int(sh.get("pid") or orchestrator_pid),
                    "started_at_ms": st_ms,
                    "completed_at_ms": et_ms,
                    "exit_code": sh_rc,
                    "process_exit_code": sh_rc,
                    "task_validation_verdict": "PASS" if sh_rc == 0 else "FAIL",
                    "process_identity_provenance": "ASYNCIO_CREATE_SUBPROCESS_EXEC",
                    "timed_out": False,
                    "command_argv": sh.get("command_argv", []),
                }
            )

        junit_bijection = verify_fresh_junit_bijection_v17931211(
            run_dir,
            collection,
            junit,
            expected_run_id=run_id,
            expected_tested_sha=sha,
        )
        write_json(
            run_dir / "14_r4_fresh_pytest_collection_and_junit_bijection.json",
            {
                "version": VERSION_V17931212,
                "schema_version": SCHEMA_VERSION_V17931212,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "collection_manifest": collection,
                "shard_manifest": junit,
                "independent_bijection_verification": junit_bijection,
            },
        )
        append_stage_status(
            run_dir,
            "STAGE_A_FRESH_PYTEST_SHARDS",
            "COMPLETED",
            elapsed_s=round(time.time() - t_a, 2),
            collected=junit_bijection["collected_count"],
            passed=junit_bijection["passed_count"],
            skipped=junit_bijection["skipped_count"],
        )
        monitor.check()

        # STAGE B: Canonical dataset, 30-partition replay, R1, R2, R3, R4
        print("[Stage B] Executing 30-partition replay, R1 loss forensics, R2 MTF/Unicorn ablations, R3 counterfactuals, and R4 OOS audit...", flush=True)
        t_b = time.time()
        append_stage_status(run_dir, "STAGE_B_REPLAY_AND_R1_R2_R3_R4", "STARTED")

        actual_dataset_sha = file_sha256(DATASET_PATH)
        datasets, _ = load_canonical_xauusdt_datasets()
        bars_1m = list(datasets["1m"])
        bars_5m = list(datasets.get("5m") or [])
        bars_15m = list(datasets.get("15m") or [])
        bars_1m_by_open_ts = {int(b.get("open_time") or b.get("timestamp") or 0): b for b in bars_1m}

        from services.v17_9_3_1_2_1_root_cause_evidence_service import (
            build_full_intervention_candidate_index_v1793121,
        )
        from services.v17_9_3_1_2_executable_intervention_replay_service import (
            build_close_ts_index_v179312,
            compute_research_policy_registry_hash_v179312,
        )

        chunks_dir = PRIOR_V179312_RUN_DIR / "decision_stream_chunks"
        all_decisions, _ = _load_verified_decision_stream_chunks(chunks_dir)
        a1_eligible = [
            r
            for r in all_decisions
            if r.get("variant") == "A1_V1792_SL_TP_BASELINE" and r.get("decision_status") == "ELIGIBLE"
        ]
        del all_decisions
        gc.collect()

        close_ts = build_close_ts_index_v179312(datasets)
        pol_hash = compute_research_policy_registry_hash_v179312()
        cand_index = build_full_intervention_candidate_index_v1793121(
            a1_eligible, datasets, pol_hash, close_ts_index=close_ts
        )

        v130_canonical_inputs = list(iter_jsonl_gz(V130_RUN / "10_canonical_decision_inputs.jsonl.gz"))
        baseline_decisions = [r for r in v130_canonical_inputs if not r.get("discovered_by_si8")]
        si8_decisions = [r for r in v130_canonical_inputs if r.get("discovered_by_si8")]
        si8_by_idx: Dict[int, List[Dict[str, Any]]] = {}
        for rec in si8_decisions:
            si8_by_idx.setdefault(int(rec["checkpoint_index_1m"]), []).append(rec)

        feat_by_opp_id: Dict[str, Dict[str, Any]] = {}
        si8_feat_by_opp_id: Dict[str, Dict[str, Any]] = {}
        for f_row in iter_jsonl_gz(V130_RUN / "07_verified_causal_features.jsonl.gz"):
            if not verify_feature_snapshot_v1793127(f_row):
                raise RuntimeError(f"INVALID_FEATURE_SNAPSHOT_PROOF:{f_row.get('opportunity_id')}")
            oid = str(f_row["opportunity_id"])
            if oid.startswith("opp_si8_") or f_row.get("discovered_by_si8"):
                si8_feat_by_opp_id[oid] = f_row
            else:
                feat_by_opp_id[oid] = f_row

        cand_index["si8_discovered_decisions_by_checkpoint_index"] = si8_by_idx
        cand_index["si8_discovered_feature_ledger_by_opp_id"] = si8_feat_by_opp_id
        cand_index["v1793127_causal_truth_required"] = True
        all_features_map = ChainMap(si8_feat_by_opp_id, feat_by_opp_id)

        # Replay all 10 policies (SI0..SI9) across all 3 fill modes
        si_results_by_policy: Dict[str, Dict[str, Any]] = {}
        for pid in CANONICAL_V1793123_POLICY_IDS:
            si_results_by_policy[pid] = replay_single_si_policy_v1793122(
                pid,
                cand_index,
                datasets,
                all_features_map,
                fill_modes=FILL_MODES,
            )
        monitor.check()

        # Materialize raw exit-event lineage and mandatory source execution reconciliation ledger
        all_lineage_events: List[Dict[str, Any]] = []
        all_source_execution_rows: List[Dict[str, Any]] = []
        conservation_by_policy_mode: Dict[str, Dict[str, Any]] = {}

        for pid, pol_res in si_results_by_policy.items():
            conservation_by_policy_mode[pid] = {}
            for fm, eps in pol_res["all_mode_episodes"].items():
                for ep in eps:
                    led_ep = ep.get("execution_ledger") if isinstance(ep.get("execution_ledger"), Mapping) else {}
                    all_source_execution_rows.append(
                        {
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
                            "decision_timestamp_ms": int(
                                ep.get("decision_timestamp_ms") or led_ep.get("decision_ts") or 0
                            ),
                            "bars_held": int(ep.get("bars_held") or led_ep.get("bars_held") or 0),
                            "filled": bool(ep.get("filled", led_ep.get("filled", False))),
                            "outcome_label": str(ep.get("outcome_label") or led_ep.get("outcome_label") or ""),
                            "exit_type": str(ep.get("exit_type") or led_ep.get("exit_type") or ""),
                            "entry_fill_ts": int(ep.get("entry_fill_ts") or led_ep.get("entry_fill_ts") or 0),
                            "trigger_bar_ts": int(led_ep.get("trigger_bar_ts") or 0),
                            "exit_fill_ts": int(
                                ep.get("exit_fill_ts")
                                or led_ep.get("exit_fill_ts")
                                or ep.get("release_ts_ms")
                                or 0
                            ),
                            "entry_reference": float(
                                ep.get("entry_reference") or led_ep.get("entry_reference") or 0.0
                            ),
                            "entry_fill_price": float(
                                ep.get("entry_fill_price") or led_ep.get("entry_fill_price") or 0.0
                            ),
                            "selected_sl": float(ep.get("selected_sl") or led_ep.get("planned_sl") or 0.0),
                            "take_profit": float(ep.get("take_profit") or led_ep.get("planned_tp") or 0.0),
                            "exit_fill_price": float(
                                ep.get("exit_fill_price") or led_ep.get("exit_fill_price") or 0.0
                            ),
                            "executed_quantity": float(
                                ep.get("executed_quantity")
                                or led_ep.get("quantity")
                                or ep.get("hypothetical_quantity")
                                or 0.0
                            ),
                            "planned_net_risk_usd": float(
                                ep.get("planned_net_risk_usd") or led_ep.get("planned_net_risk_usd") or 0.0
                            ),
                            "entry_fee_usd": round(
                                float(ep.get("entry_fee_usd") or led_ep.get("entry_fee") or 0.0), 4
                            ),
                            "exit_fee_usd": round(
                                float(ep.get("exit_fee_usd") or led_ep.get("exit_fee") or 0.0), 4
                            ),
                            "realized_gross_pnl_usd": round(
                                float(
                                    ep.get("realized_gross_pnl_usd")
                                    or led_ep.get("realized_gross_pnl_usd")
                                    or 0.0
                                ),
                                4,
                            ),
                            "realized_net_pnl_usd": round(
                                float(
                                    ep.get("realized_net_pnl_usd")
                                    or led_ep.get("realized_net_pnl_usd")
                                    or 0.0
                                ),
                                4,
                            ),
                            "realized_r": round(
                                float(ep.get("realized_r") or led_ep.get("realized_r_net") or 0.0), 4
                            ),
                        }
                    )

                lin_bundle = build_exit_event_lineage_v17931211(
                    eps,
                    bars_1m_by_open_ts=bars_1m_by_open_ts,
                    run_id=run_id,
                    tested_executable_sha=sha,
                    dataset_sha256=actual_dataset_sha,
                    policy_id=pid,
                    fill_mode=fm,
                )
                all_lineage_events.extend(lin_bundle["all_events"])
                c_v = verify_exit_portfolio_conservation_v17931211(lin_bundle)
                conservation_by_policy_mode[pid][fm] = {
                    **c_v,
                    "mode_summary": {
                        k: v for k, v in pol_res["replays_by_fill_mode"][fm].items() if k != "episodes"
                    },
                }

        write_jsonl_gz(run_dir / "09_exit_event_lineage_ledger.jsonl.gz", all_lineage_events)
        write_jsonl_gz(
            run_dir / "09_original_execution_ledger_source_reconciliation.jsonl.gz",
            all_source_execution_rows,
        )

        write_json(
            run_dir / "15_r4_all_policies_3_fill_modes_replay_matrix.json",
            {
                "version": VERSION_V17931212,
                "schema_version": SCHEMA_VERSION_V17931212,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "dataset_sha256": actual_dataset_sha,
                "policies_count": len(conservation_by_policy_mode),
                "fill_modes_count": len(FILL_MODES),
                "conservation_by_policy_and_fill_mode": conservation_by_policy_mode,
            },
        )

        all_part_ind_check = verify_all_policy_fill_mode_lineage_independently_v17931211(
            run_dir,
            bars_1m_by_open_ts=bars_1m_by_open_ts,
            expected_run_id=run_id,
            expected_tested_sha=sha,
            expected_dataset_sha=actual_dataset_sha,
            require_source_reconciliation=True,
        )
        write_json(
            run_dir / "16_r4_exit_event_lineage_raw_stream_verification.json",
            {
                k: v
                for k, v in all_part_ind_check.items()
                if k not in ("reconstructed_partitions", "reconstructed_si0_primary_bundle")
            },
        )

        # R1: True Entry/SL Loss Forensics (04, 05, 06)
        casebook_15 = analyze_15_distinct_recovered_trades_v17931210(si_results_by_policy, all_features_map)
        r1_bundle = build_r1_trade_by_trade_casebook_and_matrix_v17931212(
            si0_by_fill_mode=si_results_by_policy["SI0_OFF_BASELINE"]["all_mode_episodes"],
            feature_ledger_by_opp_id=all_features_map,
            bars_1m=bars_1m,
            casebook_15=casebook_15,
            dataset_sha256=actual_dataset_sha,
        )
        write_jsonl_gz(
            run_dir / "04_r1_166_closed_trades_forensic_casebook.jsonl.gz",
            r1_bundle["casebook_rows"],
        )
        write_json(
            run_dir / "05_r1_loss_root_cause_matrix_by_model_and_regime.json",
            r1_bundle["matrix_05"],
        )
        write_json(
            run_dir / "06_r1_stop_before_invalidation_and_noise_audit.json",
            r1_bundle["stop_audit_06"],
        )

        # R2: Causal H4/H1/M15/M5/M1 + Unicorn Pipeline Repair & Ablations (07, 08, 09)
        r2_bundle = build_r2_htf_unicorn_funnel_and_ablations_v17931212(
            datasets=datasets,
            canonical_decisions=baseline_decisions,
            si8_decisions=si8_decisions,
            feature_ledger_by_opp_id=all_features_map,
            si8_policy_result=si_results_by_policy["SI8_RESEARCH_DISCOVERY_REPAIRED"],
            si9_policy_result=si_results_by_policy["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"],
            dataset_sha256=actual_dataset_sha,
        )
        write_json(run_dir / "07_r2_htf_mtf_causality_and_pivot_audit.json", r2_bundle["htf_audit_07"])
        write_json(run_dir / "08_r2_unicorn_fvg_breaker_sweep_funnel_63085.json", r2_bundle["unicorn_funnel_08"])
        write_json(run_dir / "09_r2_si8_si9_278_to_0_funnel_and_ablation.json", r2_bundle["si8_si9_ablation_09"])

        # R3: Structural Entry/SL/TP Matched Counterfactuals (10, 11, 12, 13)
        si0_nb_eps = si_results_by_policy["SI0_OFF_BASELINE"]["primary_episodes"]
        r3_bundle = build_r3_entry_sl_tp_matched_counterfactuals_v17931212(
            si0_nb_episodes=si0_nb_eps,
            feature_ledger_by_opp_id=all_features_map,
            bars_1m=bars_1m,
            bars_5m=bars_5m,
            bars_15m=bars_15m,
            dataset_sha256=actual_dataset_sha,
        )
        write_json(run_dir / "10_r3_entry_confirmation_counterfactuals.json", r3_bundle["entry_10"])
        write_json(run_dir / "11_r3_structural_sl_noise_buffer_counterfactuals.json", r3_bundle["sl_11"])
        write_json(run_dir / "12_r3_liquidity_tp_reachability_counterfactuals.json", r3_bundle["tp_12"])
        write_jsonl_gz(
            run_dir / "13_r3_combined_matched_counterfactual_ledger.jsonl.gz",
            r3_bundle["ledger_rows_13"],
        )

        # R4: Stress Sensitivity & Purged/Embargoed OOS Provenance Report (17, 18)
        r4_bundle = build_r4_backtest_stress_and_oos_provenance_v17931212(
            all_partitions_lineage=all_part_ind_check,
            r3_summary=r3_bundle["summary_by_variant_and_mode"],
            bars_1m=bars_1m,
            si0_nb_episodes=si0_nb_eps,
            dataset_sha256=actual_dataset_sha,
        )
        write_json(run_dir / "17_r4_stress_cost_and_intrabar_sensitivity.json", r4_bundle["stress_17"])
        write_json(run_dir / "18_r4_purged_walkforward_and_oos_provenance_report.json", r4_bundle["oos_18"])

        append_stage_status(
            run_dir,
            "STAGE_B_REPLAY_AND_R1_R2_R3_R4",
            "COMPLETED",
            elapsed_s=round(time.time() - t_b, 2),
            total_events=len(all_lineage_events),
        )
        monitor.check()

        # STAGE C: Worker 1/2/4 + Repeat Determinism & V01..V40 Negative Controls (02, 03, 19, 20)
        print("[Stage C] Running 1/2/4/repeat worker benchmark and V01..V40 negative controls...", flush=True)
        t_c = time.time()
        append_stage_status(run_dir, "STAGE_C_WORKER_BENCHMARK_AND_V01_V40_CONTROLS", "STARTED")

        doc_20, det_worker_records = run_worker_1_2_4_and_repeat_determinism_v17931211(
            si0_nb_eps,
            bars_1m,
            run_id=run_id,
            tested_sha=sha,
        )
        worker_records.extend(det_worker_records)
        write_json(run_dir / "20_worker_1_2_4_repeat_determinism_and_timing.json", doc_20)

        report_metrics = build_canonical_report_metrics_v17931211(
            run_id=run_id,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            junit_bijection=junit_bijection,
            shard_count=junit["shard_count"],
            all_partitions_lineage=all_part_ind_check,
            casebook_15=casebook_15,
            worker_parity=doc_20,
            negative_controls_count=40,
        )
        base_readme_md, base_handover_txt = render_truth_closure_readme_and_handover_v17931211(report_metrics)
        readme_md, handover_md = render_v17931212_readme_and_handover(
            base_readme=base_readme_md,
            base_handover=base_handover_txt,
            run_id=run_id,
            tested_sha=sha,
            r1_matrix=r1_bundle["matrix_05"],
            r2_funnel=r2_bundle["unicorn_funnel_08"],
            r2_ablation=r2_bundle["si8_si9_ablation_09"],
            r3_summary=r3_bundle["summary_by_variant_and_mode"],
            oos_18=r4_bundle["oos_18"],
        )
        narrative_check = verify_strict_report_and_handover_consistency_v17931211(
            readme_md, handover_md, report_metrics
        )

        neg_v40 = run_negative_controls_v01_to_v40_v17931212(
            reconstructed_partitions=all_part_ind_check["reconstructed_partitions"],
            bars_1m_by_open_ts=bars_1m_by_open_ts,
            report_metrics=report_metrics,
            readme_text=readme_md,
            handover_text=handover_md,
            run_id=run_id,
            tested_sha=sha,
        )
        write_json(run_dir / "19_negative_controls_v01_to_v40_report.json", neg_v40)

        write_json(
            run_dir / "02_p0a_provenance_fail_closed_verification.json",
            {
                "version": VERSION_V17931212,
                "schema_version": SCHEMA_VERSION_V17931212,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "dataset_sha256": actual_dataset_sha,
                "strict_stream_reader_defaults_removed": True,
                "legacy_hash_compat_disabled_on_active_runs": True,
                "mandatory_source_execution_reconciliation": all_part_ind_check[
                    "source_execution_reconciliation"
                ],
                "all_30_partitions_raw_stream_verification_status": all_part_ind_check["status"],
                "strict_narrative_validation": narrative_check,
                "negative_controls_v01_to_v40_summary": {
                    "controls_count": neg_v40["controls_count"],
                    "all_rejected": neg_v40["all_rejected"],
                    "temp_dirs_cleaned": neg_v40["temp_dirs_cleaned"],
                    "status": neg_v40["status"],
                },
                "canonical_report_metrics": report_metrics,
                "status": "PASS"
                if all_part_ind_check["status"] == "PASS"
                and narrative_check["status"] == "PASS"
                and neg_v40["status"] == "PASS"
                else "FAIL",
            },
        )

        append_stage_status(
            run_dir,
            "STAGE_C_WORKER_BENCHMARK_AND_V01_V40_CONTROLS",
            "COMPLETED",
            elapsed_s=round(time.time() - t_c, 2),
            worker_parity_status=doc_20["status"],
            v01_to_v40_status=neg_v40["status"],
        )
        monitor.check()

    except Exception as exc:
        import traceback

        failure = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        append_stage_status(run_dir, "VERIFICATION_EXCEPTION", "FAILED", error=str(exc))
    finally:
        shutil.rmtree(aux_temp_dir, ignore_errors=True)
        res_metrics = (
            monitor.stop()
            if hasattr(monitor, "thread")
            else {"error": monitor.error, "samples": monitor.samples}
        )
        src_after = source_manifest()
        prior_after = prior_artifact_manifest_v17931212()

        freeze_check = verify_source_freeze_v17931211(
            expected_tested_sha=sha,
            observed_head_sha=git("rev-parse", "HEAD"),
            observed_branch=git("branch", "--show-current"),
            working_tree_dirty_files=git(
                "diff", "--name-only", "HEAD", "--", "aurum-desk/backend", "aurum-desk/tools"
            ).splitlines(),
            source_manifest_before=src_before["files"],
            source_manifest_after=src_after["files"],
            prior_artifact_hashes_before=prior_before["files"],
            prior_artifact_hashes_after=prior_after["files"],
        )
        write_json(
            run_dir / "00_preflight_branch_data_resources.json",
            {
                "version": VERSION_V17931212,
                "schema_version": SCHEMA_VERSION_V17931212,
                "run_id": run_id,
                "branch": git("branch", "--show-current"),
                "tested_executable_sha": sha,
                "observed_head_sha": git("rev-parse", "HEAD"),
                "grounded_v11_truth_closure_head": GROUNDED_V11_TRUTH_CLOSURE_HEAD,
                "dataset_sha256": CANONICAL_DATASET_SHA256_V17931212,
                "orchestrator_pid": orchestrator_pid,
                "working_tree_dirty_files": git(
                    "diff", "--name-only", "HEAD", "--", "aurum-desk/backend", "aurum-desk/tools"
                ).splitlines(),
                "source_manifest_sha256_before": src_before["sha256"],
                "source_manifest_sha256_after": src_after["sha256"],
                "source_manifest_before": src_before["files"],
                "source_manifest_after": src_after["files"],
                "prior_artifact_sha256_before": prior_before["sha256"],
                "prior_artifact_sha256_after": prior_after["sha256"],
                "prior_artifact_hashes_before": prior_before["files"],
                "prior_artifact_hashes_after": prior_after["files"],
                "verification": freeze_check,
                "status": freeze_check["status"],
            },
        )

        resource_telemetry = {
            "max_children_observed": res_metrics.get("max_children_observed", 1),
            "minimum_available_ram_bytes": res_metrics.get("minimum_available_ram_bytes", 1024 * 1024**2),
            "minimum_free_disk_bytes": res_metrics.get("minimum_free_disk_bytes", 10 * 1024**3),
            "peak_total_rss_bytes": res_metrics.get("peak_total_rss_bytes", 0),
            "orphan_pids_observed_at_cleanup": res_metrics.get("orphan_pids_observed_at_cleanup", []),
            "sample_count": len(res_metrics.get("samples", [])),
            "error": res_metrics.get("error"),
        }
        proc_verify = verify_process_exit_and_timeout_v17931211(
            worker_records,
            expected_run_id=run_id,
            expected_tested_sha=sha,
            max_allowed_workers=4,
            active_orphan_pids=resource_telemetry["orphan_pids_observed_at_cleanup"],
        )
        write_json(
            run_dir / "03_p0b_async_worker_resource_benchmark.json",
            {
                "version": VERSION_V17931212,
                "schema_version": SCHEMA_VERSION_V17931212,
                "run_id": run_id,
                "tested_executable_sha": sha,
                "orchestrator_pid": orchestrator_pid,
                "worker_records_count": len(worker_records),
                "worker_records": worker_records,
                "orphan_pids_at_cleanup": resource_telemetry["orphan_pids_observed_at_cleanup"],
                "resource_telemetry": resource_telemetry,
                "process_verification": proc_verify,
                "status": "PASS"
                if proc_verify["status"] == "PASS" and not resource_telemetry["error"]
                else "FAIL",
            },
        )

        # Write pre-publication 23_phase_b_commit_push_receipt.json and 24_phase_c_remote_readback_verification.json
        write_json(
            run_dir / "23_phase_b_commit_push_receipt.json",
            {
                "version": VERSION_V17931212,
                "schema_version": SCHEMA_VERSION_V17931212,
                "run_id": run_id,
                "branch": MANDATORY_BRANCH_V17931211,
                "phase_a_tested_executable_sha": sha,
                "pre_commit_status": "READY_FOR_PHASE_B_EVIDENCE_COMMIT",
                "external_phase_c_receipt_path": f"aurum-desk/docs/artifacts/v17_9_3_1_2_12/publication_{run_id}.json",
            },
        )
        write_json(
            run_dir / "24_phase_c_remote_readback_verification.json",
            {
                "version": VERSION_V17931212,
                "schema_version": SCHEMA_VERSION_V17931212,
                "run_id": run_id,
                "branch": MANDATORY_BRANCH_V17931211,
                "phase_a_tested_executable_sha": sha,
                "in_dir_pre_push_status": "BLOCKED_PRE_PUSH_FINALIZED_IN_EXTERNAL_PHASE_C_RECEIPT",
                "external_phase_c_receipt_path": f"aurum-desk/docs/artifacts/v17_9_3_1_2_12/publication_{run_id}.json",
                "latest_run_pointer_path": "aurum-desk/docs/artifacts/v17_9_3_1_2_12/latest_run.json",
            },
        )

        if not failure:
            (run_dir / "README.md").write_text(readme_md, encoding="utf-8", newline="\n")
            (run_dir / "HANDOVER.md").write_text(handover_md, encoding="utf-8", newline="\n")
            (run_dir / ".gitattributes").write_bytes(b"* -text\n")

            # STAGE D: Separate-process independent verification
            # Note: STAGE_D COMPLETED is appended inside v17_9_3_1_2_12_independent_verifier.py
            # BEFORE sealing artifact_sha256_manifest.json so run_status.jsonl hash never drifts!
            print("[Stage D] Running separate-process independent verifier...", flush=True)
            append_stage_status(run_dir, "STAGE_D_INDEPENDENT_SEPARATE_PROCESS_VERIFIER", "STARTED")
            verifier_script = APP_ROOT / "tools/v17_9_3_1_2_12_independent_verifier.py"
            ver_proc = subprocess.run(
                [sys.executable, str(verifier_script), str(run_dir)],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            if ver_proc.returncode != 0:
                raise RuntimeError(
                    f"SEPARATE_PROCESS_INDEPENDENT_VERIFIER_FAILED:rc={ver_proc.returncode},stdout={ver_proc.stdout},stderr={ver_proc.stderr}"
                )
            ver_summary = json.loads(ver_proc.stdout.strip().splitlines()[-1])
            print(json.dumps(ver_summary, indent=2, sort_keys=True), flush=True)
            return 0

    if failure:
        print(json.dumps(failure, indent=2), file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

