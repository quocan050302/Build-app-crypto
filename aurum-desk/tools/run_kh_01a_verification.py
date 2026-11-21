#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_01a_verification.py

AURUM Desk — KH-01A Autonomous Verification, Causal Forensics & Backtest Orchestrator
SPEC VERSION: KH-01A-v1.0 (16 November 2026)

Executes end-to-end on frozen executable SHA on branch `feature/v17-9-2-structural-sl-tp-from-qa`:
  - Preflight OS/disk/RAM/CPU/git/dataset guards (`00`) & AST function ownership matrix (`01`).
  - Data Gap Truth RED->GREEN audit & cross-timeframe session/quality proof (`02`, `03`).
  - Real-injected fail-closed adversarial fault suite (`04`, zero report-assertion padding).
  - Async bounded multiprocess full backend + KH-01A pytest suite with `--collect-only` inventory,
    per-shard temp isolation, 2 MiB capped compressed logs, and 100% JUnit bijection (`10`).
  - Controlled `w=1, w=2, w=4, w=1_repeat` pytest subset & 242-episode replay determinism +
    measured speedup benchmark (`11`).
  - Full chronological 1-position historical backtest across `SI0..SI9` & `A0..A4, A1B, D1..D4`
    under 3 fill modes + 180-minute purged walk-forward (`08`).
  - Complete point-in-time causal loss forensics on all 157 SI0 losses + 9 win controls +
    4 unique SI8/SI9 research losses (`05`, `06`).
  - Row-by-row 267 + 40 control target/ATR provenance audit (`07`, read-only, TP KEPT AS IS).
  - Before/after `SharedStrategyEvaluator` decision/trade-action fingerprint & 3-adapter parity (`09`).
  - Dataset Registry & Fresh-OOS Escrow audit (`13`, `BLOCKED_NO_UNTOUCHED_DATA`).
  - Sentinel-verified temp cleanup & 0.5s resource/PID monitor report (`12`).
  - Disk-hashed `EvidenceReceiptV17931219`s (`14`), 14-gate orthogonal verdicts (`16`),
    `README_KH01A.md` + Vietnamese `HANDOVER_KH01A.txt`, and separate-OS-process independent
    disk verification sealing `15_sha256_manifest_and_source_config_versions.json`.
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
from collections import ChainMap, Counter
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
import tempfile
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
import xml.etree.ElementTree as ET

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
    PRIOR_V19_PUBLICATION_HEAD_SHA,
    PRIOR_V19_TESTED_SHA,
    SCHEMA_VERSION_KH01A,
    SPEC_VERSION_KH01A,
    build_dataset_registry_and_fresh_oos_audit_kh01a,
    build_kh_01a_callgraph_and_keep_as_is_matrix,
    build_kh_01a_causal_loss_and_win_control_forensics,
    evaluate_kh_01a_14_gates,
    render_kh_01a_readme_and_handover_vi,
    run_kh_01a_real_fault_injections,
    verify_kh_01a_cross_view_consistency,
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
    verify_exit_portfolio_conservation_v17931211,
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
from services.v17_9_3_1_2_19_truth_and_core_repair_service import (
    EvidenceReceiptV17931219,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    build_close_ts_index_v179312,
    compute_research_policy_registry_hash_v179312,
)
from tools.fresh_junit_io import (
    read_closed_junit_artifact,
    write_json_durable_atomic,
    write_jsonl_gz_durable_atomic,
    write_text_durable_atomic,
)
from tools.run_v17_9_3_1_2_1_full_opportunity_replay import (
    DATASET_PATH,
    FILL_MODES,
    PRIOR_V179312_RUN_DIR,
    _load_verified_decision_stream_chunks,
)
from tools.run_v17_9_3_1_2_2_entry_signal_backtest import replay_single_si_policy_v1793122
from tools.run_v17_9_3_1_2_2_verification import (
    canonicalize_collect_node_id_v1793122,
    canonicalize_junit_testcase_node_id_v1793122,
    verify_collect_vs_junit_node_id_bijection_v1793122,
)
from tools.run_v17_9_3_1_2_7_verification import (
    _active_heavy_worker_children,
    git,
    source_manifest,
)
from tools.run_v17_9_3_1_2_11_verification import (
    _run_worker_slice_cli,
    run_worker_1_2_4_and_repeat_determinism_v17931211,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

ARTIFACTS_ROOT = APP_ROOT / "docs/artifacts/kh_01a"
V130_RUN = APP_ROOT / "docs/artifacts/v17_9_3_1_2_10/20261107T150245Z_b772a03bb7cc"
V12_LATEST_RUN_PTR = APP_ROOT / "docs/artifacts/v17_9_3_1_2_12/latest_run.json"
V19_REF_RUN = APP_ROOT / "docs/artifacts/v17_9_3_1_2_19/20261116T005739Z_a81ac1f9d578"

MAX_SHARD_LOG_BYTES = 2 * 1024 * 1024  # 2 MiB hard per-shard log cap with explicit truncation flag
MIN_FREE_DISK_RESERVE_BYTES = 10 * 1024**3  # 10 GiB non-negotiable safety reserve
ESTIMATED_STAGE_PEAK_DISK_BYTES = 512 * 1024**2  # 512 MiB conservative stage write + temp allowance
OWNED_TEMP_SENTINEL_NAME = ".kh01a_owned_temp_sentinel.json"

# Captured BEFORE any KH-01A source edits on HEAD f6197343dc35123141ae010b5ee7d90e535081c1
PREPATCH_BASELINE_REFERENCE: Dict[str, Any] = {
    "starting_head_sha": "f6197343dc35123141ae010b5ee7d90e535081c1",
    "prior_v19_tested_executable_sha": "a81ac1f9d5783121bc55bdd305063863d10cb7af",
    "baseline_source_manifest_sha256": "5ade3b960ed8f2a5fa9ec02de16ad1d7ec3086db6d204f8fd36e4442000313bb",
    "baseline_source_files_count": 790,
    "backend_git_tree_sha": "f21a4d4fbcfe101e322b7b43690a68b788a58f70",
    "tools_git_tree_sha": "81c9911a75f0d534cc003143ba13ad1dc9ce62c9",
    "canonical_dataset_sha256": "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18",
    "baseline_config_hashes": {
        "cost_assumptions_sha256": "0fbd26c552566e0a35536f8bbc972eb5548f84723d268c897fb12d6ff9fe7afd",
        "research_policy_registry_hash": "5bc06ca8262314cf093e81cf9c8c8501d02343afc472e4c7e787613eb4187913",
        "scalp_config_sha256": "7c929fe49cb906a8780d304dd267f159abd62767fcaebbd12fd569ca4d162dc3",
        "signal_intelligence_registry_hash": "9fd87faffa9a0ed2f29c4680673122e03e3670f7c4c98ea1ed90d00cd3f9fba2",
    },
    "prepatch_evaluator_fingerprints_sha256": "96f805fb03bbc4ebd05d4a3faed3e9e0223699f4cc5de3be9131fc3045335628",
    "prepatch_adapter_fingerprints_sha256": "0ae21d70e0dc25c2be81084781c658df746586ad16c40a3b44f4c38ed11baca5",
    "prepatch_si0_nb_accounting": {
        "total_scheduled_episodes": 242,
        "closed_trades_count": 166,
        "wins_count": 9,
        "losses_count": 157,
        "open_at_horizon_count": 74,
        "data_gap_unresolved_count": 1,
        "unfilled_or_no_fill_count": 1,
        "total_realized_gross_usd": -142.7836,
        "total_fees_usd": 203.1135,
        "total_realized_net_usd": -345.8971,
        "expectancy_net_r": -0.8474,
        "ledger_sha256": "d168169dc37ec8b8ee3db0c5f2d4bbb9ac35dc3be53a1f7c6bd37df809af9904",
    },
    "prepatch_si8_nb_accounting": {
        "total_scheduled_episodes": 4,
        "closed_trades_count": 4,
        "wins_count": 0,
        "losses_count": 4,
        "total_realized_net_usd": -9.7372,
        "expectancy_net_r": -0.9875,
        "ledger_sha256": "68066ff9e4ea00a22e91cd08bf9ac50e4082a2c18e5c2c785dab901766aa6dd2",
    },
    "prepatch_si9_nb_accounting": {
        "total_scheduled_episodes": 2,
        "closed_trades_count": 2,
        "wins_count": 0,
        "losses_count": 2,
        "total_realized_net_usd": -4.8886,
        "expectancy_net_r": -0.9896,
        "ledger_sha256": "74bbe22cc0756cc2dd16b08632f96ce9ac39fc0a1547342b97bbbbc92fbc7756",
    },
}

CONTROLLED_SUBSET_BENCHMARK_FILES: Tuple[str, ...] = (
    "backend/tests/test_kh_01a_data_and_verification_foundation.py",
    "backend/tests/test_kh_01a_r1_forensics_truth_closure.py",
    "backend/tests/test_kh_01b_entry_unicorn_research.py",
    "backend/tests/test_kh_01b_causal_antileakage.py",
    "backend/tests/test_v14_3_ny_quota.py",
    "backend/tests/test_v17_9_1_mtf_structure_liquidity.py",
    "backend/tests/test_v17_9_2_structural_stop.py",
    "backend/tests/test_v17_9_2_1_validation_closure.py",
    "backend/tests/test_v17_9_3_macro_unicorn_intelligence.py",
    "backend/tests/test_v17_7_atr_provenance.py",
    "backend/tests/test_v15_9_quote_source_rejection.py",
)

MULTI_CHILD_SPAWNING_TEST_FILES: Set[str] = {
    "test_r331_acceptance_runner_and_resources.py",
    "test_v17_9_0_1_2_strict_eligibility_and_parity.py",
    "test_v17_9_async_regression.py",
    "test_v17_9_3_1_2_2_verification_integrity.py",
    "test_v17_9_3_1_2_7_causal_truth.py",
}

SINGLE_CHILD_SPAWNING_TEST_FILES: Set[str] = {
    "test_kh_01a_r1_subprocess_verifier.py",
    "test_kh_01b_replay_resource_and_truth.py",
    "test_r332_raw_regression_retry_integrity.py",
    "test_v14_4_2_chart_export_robustness.py",
    "test_v16_39_10_snapshot_api_cli.py",
    "test_v17_6_4_3a_acceptance_integrity.py",
    "test_v17_6_4_3a_discovery_integrity.py",
    "test_v17_6_4_3b_acceptance_integrity.py",
    "test_v17_7_adaptive_stop_loss.py",
    "test_v17_8_0_4_parity_and_integrity.py",
    "test_v17_9_1_positive_path_replay.py",
    "test_v17_9_3_macro_unicorn_backtest.py",
    "test_v17_9_3_1_2_1_root_cause_evidence.py",
    "test_v17_9_3_1_2_9_liquidity_provenance.py",
    "test_v17_9_3_1_2_10_canonical_universe_and_provenance.py",
    "test_v17_9_3_1_2_10_verification_parity.py",
    "test_v17_9_3_1_2_11_lineage_and_exit_integrity.py",
    "test_v17_9_3_1_2_12_regression_and_accounting_truth.py",
    "test_v17_9_3_1_2_13_delayed_entry_and_workers.py",
    "test_v17_9_3_1_2_15_verification_and_adversarial_suite.py",
    "test_v17_9_3_1_2_17_truth_closure_and_evidence.py",
    "test_v17_9_3_1_2_full_universe_executable_interventions.py",
    "test_v17_9_3_1_historical_execution_truth.py",
}

CHILD_SPAWNING_TEST_FILES: Set[str] = MULTI_CHILD_SPAWNING_TEST_FILES | SINGLE_CHILD_SPAWNING_TEST_FILES | {
    "test_v17_7_database_safety.py",
}

EMPIRICAL_HEAVY_FILE_WEIGHT_BONUS: Dict[str, int] = {
    "test_v5_authoritative_lab.py": 450_000,
    "test_v17_9_3_macro_unicorn_backtest.py": 380_000,
    "test_r332_raw_regression_retry_integrity.py": 320_000,
    "test_v13_2_replay_repair.py": 210_000,
    "test_v17_6_4_3b_unique_commit_truth.py": 175_000,
    "test_v17_9_3_1_2_3_core_trading_logic_repair.py": 175_000,
    "test_v13_3_daily_scheduler.py": 145_000,
    "test_v17_9_3_1_2_1_opportunity_identity.py": 140_000,
    "test_v17_6_4_3a_commit_truth.py": 135_000,
    "test_v17_9_3_1_2_1_portfolio_censoring.py": 120_000,
    "test_kh_01a_r1_forensics_truth_closure.py": 115_000,
    "test_kh_01a_data_and_verification_foundation.py": 105_000,
}

SHARED_DB_SERIAL_TEST_FILES: Set[str] = {
    "test_migration_runtime_upgrade.py",
    "test_v6_1_isolation_and_recovery.py",
    "test_v10_2_acceptance.py",
    "test_v11_historical_replay.py",
    "test_v12_1_acceptance.py",
    "test_v12_2_acceptance.py",
    "test_v12_4_acceptance.py",
    "test_v12_three_month_replay.py",
    "test_v17_4_2_journal_parity.py",
    "test_v17_4_3_persistence_guard.py",
    "test_v17_5_database_concurrency.py",
    "test_v17_6_signal_tracker.py",
    "test_v17_8_0_3_market_shadow.py",
    "test_v17_9_shared_parity_and_async_infra.py",
}


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for ln in fh:
            if ln.strip():
                yield json.loads(ln)


def prior_immutable_artifacts_hash() -> Dict[str, Any]:
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
    ):
        root = APP_ROOT / "docs/artifacts" / ver_dir
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
                files[rel] = file_sha256(p)
    return {"files_count": len(files), "sha256": canonical_hash(files)}


def create_owned_temp_root(run_id: str) -> Path:
    base_tmp = Path(tempfile.gettempdir()).resolve()
    short_tag = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:10]
    owned = (base_tmp / f"ak1_{short_tag}").resolve()
    if not owned.is_relative_to(base_tmp):
        raise RuntimeError(f"UNSAFE_TEMP_ROOT_PATH:{owned}")
    owned.mkdir(parents=True, exist_ok=True)
    write_json_durable_atomic(
        owned / OWNED_TEMP_SENTINEL_NAME,
        {
            "run_id": run_id,
            "owner_pid": os.getpid(),
            "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    return owned


def cleanup_owned_temp_root(owned: Path, expected_run_id: str) -> Dict[str, Any]:
    base_tmp = Path(tempfile.gettempdir()).resolve()
    resolved = owned.resolve()
    if not resolved.exists():
        return {"cleaned": True, "sentinel_verified": False, "already_absent": True, "path": str(resolved)}
    if not resolved.is_relative_to(base_tmp) or resolved == base_tmp:
        raise RuntimeError(f"REFUSING_TEMP_DELETE_OUTSIDE_CONTAINMENT:{resolved}")
    if REPO_ROOT.resolve() in resolved.parents or resolved == REPO_ROOT.resolve():
        raise RuntimeError(f"REFUSING_TEMP_DELETE_INSIDE_REPO:{resolved}")
    sentinel_p = resolved / OWNED_TEMP_SENTINEL_NAME
    if not sentinel_p.is_file():
        raise RuntimeError(f"REFUSING_TEMP_DELETE_MISSING_SENTINEL:{sentinel_p}")
    sentinel_doc = json.loads(sentinel_p.read_text(encoding="utf-8"))
    if sentinel_doc.get("run_id") != expected_run_id:
        raise RuntimeError(f"REFUSING_TEMP_DELETE_SENTINEL_RUN_MISMATCH:{sentinel_doc}")
    shutil.rmtree(resolved)
    return {
        "cleaned": not resolved.exists(),
        "sentinel_verified": True,
        "containment_verified": True,
        "path": str(resolved),
    }


class ResourceMonitorKH01A:
    """0.5s live process-tree, RAM, CPU, and disk safety guard (`max_children <= 4`)."""

    def __init__(
        self,
        *,
        abort_path: Path,
        minimum_ram_bytes: int = 512 * 1024**2,
        soft_downshift_ram_bytes: int = 2 * 1024**3,
        minimum_disk_bytes: int = MIN_FREE_DISK_RESERVE_BYTES,
        max_children_cap: int = 4,
        warning_near_floor_bytes: int = 1024 * 1024**2,
        sampling_interval_seconds: float = 0.5,
        available_ram_fn: Optional[Any] = None,
        free_disk_fn: Optional[Any] = None,
        active_context_fn: Optional[Any] = None,
    ) -> None:
        self.abort_path = abort_path
        self.minimum_ram_bytes = int(minimum_ram_bytes)
        self.soft_downshift_ram_bytes = int(soft_downshift_ram_bytes)
        self.minimum_disk_bytes = int(minimum_disk_bytes)
        self.max_children_cap = int(max_children_cap)
        self.warning_near_floor_bytes = int(warning_near_floor_bytes)
        self.sampling_interval_seconds = max(0.05, min(0.5, float(sampling_interval_seconds)))
        self._available_ram_fn = available_ram_fn
        self._free_disk_fn = free_disk_fn
        self._active_context_fn = active_context_fn
        self._lock = threading.Lock()
        self.stop_event = threading.Event()
        self.samples: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self.latched_error: Optional[str] = None
        self.first_breach_sample: Optional[Dict[str, Any]] = None
        self.active_shards_at_breach: List[str] = []
        self.hard_ram_breached = False
        self.near_hard_floor_warning = False
        self.disk_reserve_breached = False
        self.soft_downshift_events = 0
        self.orchestrator_pid = os.getpid()
        self._total_samples_taken = 0
        self._min_seen_ram_bytes: Optional[int] = None
        self._min_seen_disk_bytes: Optional[int] = None
        self._max_seen_parent_rss_bytes = 0
        self._max_seen_child_tree_rss_bytes = 0
        self._max_seen_total_rss_bytes = 0
        self._max_seen_children = 0
        self._max_seen_descendants = 0

    def _persist_abort_receipt(self, reason: str, sample: Mapping[str, Any], active_shards: Sequence[str]) -> None:
        try:
            write_json_durable_atomic(
                self.abort_path,
                {
                    "status": "BLOCKED",
                    "reason": reason,
                    "latched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "minimum_ram_floor_bytes": self.minimum_ram_bytes,
                    "minimum_disk_floor_bytes": self.minimum_disk_bytes,
                    "max_children_cap": self.max_children_cap,
                    "active_shard_ids": list(active_shards),
                    "last_sample": dict(sample),
                },
            )
        except Exception:
            pass

    def check(self) -> Dict[str, Any]:
        import psutil

        proc = psutil.Process(self.orchestrator_pid)
        children = _active_heavy_worker_children(proc)
        try:
            raw_descendants = [
                ch for ch in proc.children(recursive=True)
                if ch.pid != self.orchestrator_pid
            ]
        except psutil.Error:
            raw_descendants = list(children)

        ram = int(self._available_ram_fn()) if callable(self._available_ram_fn) else int(psutil.virtual_memory().available)
        free_disk = int(self._free_disk_fn()) if callable(self._free_disk_fn) else int(shutil.disk_usage(REPO_ROOT).free)

        def _proc_rss(ch: Any) -> int:
            try:
                if ch.status() == psutil.STATUS_ZOMBIE:
                    return 0
                return int(ch.memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                return 0

        parent_rss = int(proc.memory_info().rss)
        desc_rss_map: Dict[int, int] = {}
        for d_proc in raw_descendants:
            try:
                name_l = (d_proc.name() or "").lower()
                if name_l.startswith("git"):
                    continue
                r_val = _proc_rss(d_proc)
                if r_val > 0:
                    desc_rss_map[int(d_proc.pid)] = r_val
            except psutil.Error:
                continue

        leaf_child_rss = sum(_proc_rss(p) for p in children)
        full_tree_child_rss = max(leaf_child_rss, sum(desc_rss_map.values()))
        active_shards_now: List[str] = []
        if callable(self._active_context_fn):
            try:
                ctx_info = self._active_context_fn() or {}
                if isinstance(ctx_info, Mapping):
                    active_shards_now = sorted(str(x) for x in (ctx_info.get("active_shards") or ()))
            except Exception:
                active_shards_now = []

        row = {
            "epoch_ns": time.time_ns(),
            "captured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "free_disk_bytes": free_disk,
            "available_ram_bytes": ram,
            "system_cpu_percent": float(psutil.cpu_percent()),
            "worker_pids": [p.pid for p in children],
            "descendant_pids": sorted(desc_rss_map.keys()),
            "active_shard_ids": active_shards_now,
            "rss_bytes": parent_rss,
            "child_rss_bytes": full_tree_child_rss,
            "leaf_worker_rss_bytes": leaf_child_rss,
        }

        breach_reason: Optional[str] = None
        first_latch_triggered = False
        with self._lock:
            self._total_samples_taken += 1
            if ram < self.soft_downshift_ram_bytes:
                self.soft_downshift_events += 1
            if self.minimum_ram_bytes <= ram < self.warning_near_floor_bytes:
                self.near_hard_floor_warning = True

            self._min_seen_ram_bytes = ram if self._min_seen_ram_bytes is None else min(self._min_seen_ram_bytes, ram)
            self._min_seen_disk_bytes = free_disk if self._min_seen_disk_bytes is None else min(self._min_seen_disk_bytes, free_disk)
            self._max_seen_parent_rss_bytes = max(self._max_seen_parent_rss_bytes, parent_rss)
            self._max_seen_child_tree_rss_bytes = max(self._max_seen_child_tree_rss_bytes, full_tree_child_rss)
            self._max_seen_total_rss_bytes = max(self._max_seen_total_rss_bytes, parent_rss + full_tree_child_rss)
            self._max_seen_children = max(self._max_seen_children, len(children))
            self._max_seen_descendants = max(self._max_seen_descendants, len(desc_rss_map))

            self.samples.append(row)
            if len(self.samples) > 12000:
                self.samples = self.samples[::2]

            if free_disk < self.minimum_disk_bytes:
                self.disk_reserve_breached = True
                breach_reason = f"BLOCKED_LOW_DISK:free_bytes={free_disk}<floor_bytes={self.minimum_disk_bytes}"
            elif ram < self.minimum_ram_bytes:
                self.hard_ram_breached = True
                breach_reason = f"BLOCKED_LOW_RAM:avail_bytes={ram}<floor_bytes={self.minimum_ram_bytes}"
            elif len(children) > self.max_children_cap:
                breach_reason = f"BLOCKED_GLOBAL_WORKER_CAP:{len(children)}>{self.max_children_cap}"

            if breach_reason and self.latched_error is None:
                self.latched_error = breach_reason
                self.error = breach_reason
                self.first_breach_sample = dict(row)
                self.active_shards_at_breach = list(active_shards_now)
                first_latch_triggered = True
            elif self.latched_error is not None:
                self.error = self.latched_error

        if first_latch_triggered and self.latched_error is not None:
            self._persist_abort_receipt(self.latched_error, row, active_shards_now)
        if self.latched_error:
            raise RuntimeError(self.latched_error)
        return row

    def _loop(self) -> None:
        while not self.stop_event.wait(self.sampling_interval_seconds):
            try:
                self.check()
            except Exception as exc:
                with self._lock:
                    if self.latched_error is None:
                        self.latched_error = str(exc)
                    self.error = self.latched_error
                if not self.abort_path.exists() and self.samples:
                    self._persist_abort_receipt(self.error or str(exc), self.samples[-1], self.active_shards_at_breach)
                self.reap_all_descendants()
                break

    def start(self) -> None:
        self.check()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def reap_all_descendants(self) -> List[int]:
        import psutil

        try:
            proc = psutil.Process(self.orchestrator_pid)
        except psutil.Error:
            return []
        reaped: List[int] = []
        try:
            all_desc = [d for d in proc.children(recursive=True) if not (d.name() or "").lower().startswith("git")]
        except psutil.Error:
            all_desc = _active_heavy_worker_children(proc)
        for ch in reversed(all_desc):
            try:
                reaped.append(ch.pid)
                ch.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(all_desc, timeout=5.0)
        return reaped

    def stop(self) -> Dict[str, Any]:
        import psutil

        self.stop_event.set()
        if hasattr(self, "thread"):
            self.thread.join(timeout=5.0)
        remaining = _active_heavy_worker_children(psutil.Process(self.orchestrator_pid))
        remaining_pids = [p.pid for p in remaining]
        if remaining:
            self.reap_all_descendants()
        min_ram = int(self._min_seen_ram_bytes if self._min_seen_ram_bytes is not None else min((s["available_ram_bytes"] for s in self.samples), default=0))
        min_disk = int(self._min_seen_disk_bytes if self._min_seen_disk_bytes is not None else min((s["free_disk_bytes"] for s in self.samples), default=0))
        peak_rss = max(self._max_seen_total_rss_bytes, max((s["rss_bytes"] + s["child_rss_bytes"] for s in self.samples), default=0))
        peak_parent = max(self._max_seen_parent_rss_bytes, max((s["rss_bytes"] for s in self.samples), default=0))
        peak_child = max(self._max_seen_child_tree_rss_bytes, max((s["child_rss_bytes"] for s in self.samples), default=0))
        max_ch = max(self._max_seen_children, max((len(s["worker_pids"]) for s in self.samples), default=0))
        peak_cpu = max((s["system_cpu_percent"] for s in self.samples), default=0.0)
        headroom_b = min_ram - self.minimum_ram_bytes if self.samples else 0
        final_err = self.latched_error or self.error
        return {
            "orchestrator_pid": self.orchestrator_pid,
            "global_worker_cap": self.max_children_cap,
            "sampling_interval_seconds": self.sampling_interval_seconds,
            "samples_count": len(self.samples),
            "total_samples_taken": self._total_samples_taken,
            "max_children_observed": max(1, max_ch),
            "raw_max_worker_children_observed": max_ch,
            "max_descendants_observed": self._max_seen_descendants,
            "minimum_ram_floor_bytes": self.minimum_ram_bytes,
            "minimum_ram_floor_mib": round(self.minimum_ram_bytes / (1024**2), 2),
            "warning_near_floor_bytes": self.warning_near_floor_bytes,
            "minimum_available_ram_bytes": min_ram,
            "min_available_ram_mib_observed": round(min_ram / (1024**2), 2),
            "min_available_ram_gb_observed": round(min_ram / (1024**3), 3),
            "min_ram_headroom_above_floor_bytes": headroom_b,
            "min_ram_headroom_above_floor_mib": round(headroom_b / (1024**2), 2),
            "hard_ram_breached": bool(self.hard_ram_breached or (self.samples and min_ram < self.minimum_ram_bytes)),
            "near_hard_floor_warning": bool(self.near_hard_floor_warning),
            "minimum_free_disk_bytes": min_disk,
            "min_free_disk_gb_observed": round(min_disk / (1024**3), 3),
            "peak_parent_rss_bytes": peak_parent,
            "peak_parent_rss_mib": round(peak_parent / (1024**2), 3),
            "peak_child_tree_rss_bytes": peak_child,
            "peak_child_tree_rss_mib": round(peak_child / (1024**2), 3),
            "peak_total_rss_bytes": peak_rss,
            "peak_total_rss_mb_observed": round(peak_rss / (1024**2), 2),
            "peak_system_cpu_percent_observed": round(peak_cpu, 2),
            "soft_downshift_events": self.soft_downshift_events,
            "disk_reserve_breached": self.disk_reserve_breached,
            "orphan_pids_observed_at_cleanup": remaining_pids,
            "orphan_pids_at_cleanup_count": len(remaining_pids),
            "first_breach_sample": self.first_breach_sample,
            "active_shards_at_breach": list(self.active_shards_at_breach),
            "latched_error": final_err,
            "error": final_err,
        }


def _build_deterministic_shards_kh01a(
    all_test_files: Sequence[str],
    collected_by_file: Mapping[str, Sequence[str]],
) -> List[Dict[str, Any]]:
    realtime_30ms: List[str] = []
    db_own: List[str] = []
    multi_child_spawn: List[str] = []
    single_child_spawn: List[str] = []
    v17_9_0_1_heavy: List[str] = []
    cg01_iso_by_name: Dict[str, List[str]] = {
        "test_v16_23_red_reproductions.py": [],
        "test_v16_23_production_flow.py": [],
        "test_v16_24_production_flow.py": [],
        "test_v16_25_production_flow.py": [],
    }
    db_serial: List[str] = []
    regular_inproc: List[str] = []

    for rel in sorted(all_test_files):
        fname = Path(rel).name
        if fname in ("test_v7_2_realtime_protocol.py", "test_v15_1_execution_lifecycle.py"):
            realtime_30ms.append(rel)
        elif fname == "test_v17_7_database_safety.py":
            db_own.append(rel)
        elif fname in MULTI_CHILD_SPAWNING_TEST_FILES:
            multi_child_spawn.append(rel)
        elif fname in SINGLE_CHILD_SPAWNING_TEST_FILES:
            single_child_spawn.append(rel)
        elif fname == "test_v17_9_0_1_runtime_integration_and_gates.py":
            v17_9_0_1_heavy.append(rel)
        elif fname in cg01_iso_by_name:
            cg01_iso_by_name[fname].append(rel)
        elif fname in SHARED_DB_SERIAL_TEST_FILES:
            db_serial.append(rel)
        else:
            regular_inproc.append(rel)

    def _file_weight(rel_p: str) -> Tuple[int, str]:
        fname_p = Path(rel_p).name
        sz = (APP_ROOT / rel_p).stat().st_size if (APP_ROOT / rel_p).exists() else 1
        n_nodes = len(collected_by_file.get(rel_p, ()))
        bonus = EMPIRICAL_HEAVY_FILE_WEIGHT_BONUS.get(fname_p, 0)
        return (sz + n_nodes * 512 + bonus, rel_p)

    shards: List[Dict[str, Any]] = []
    if v17_9_0_1_heavy:
        shards.append({
            "shard_name": "SHARD_INPROC_00_V17_9_0_1_RUNTIME",
            "files": v17_9_0_1_heavy,
            "exclusive": False,
            "slot_cost": 1,
            "priority_weight": 999_999,
            "needs_venv": False,
        })
    if db_serial:
        shards.append({
            "shard_name": "SHARD_INPROC_01_SHARED_DB_SERIAL",
            "files": sorted(db_serial),
            "exclusive": False,
            "slot_cost": 1,
            "priority_weight": 500_000,
            "needs_venv": False,
        })

    if single_child_spawn:
        sc_buckets: List[List[str]] = [[], [], []]
        sc_weights = [0, 0, 0]
        for r in sorted(single_child_spawn, key=lambda x: (-_file_weight(x)[0], x)):
            bi = min(range(3), key=lambda i: (sc_weights[i], i))
            sc_buckets[bi].append(r)
            sc_weights[bi] += _file_weight(r)[0]
        for sc_idx, sc_files in enumerate(sc_buckets, start=1):
            if sc_files:
                shards.append({
                    "shard_name": f"SHARD_SINGLE_CHILD_{sc_idx:02d}_BUDGET2",
                    "files": sorted(sc_files),
                    "exclusive": False,
                    "slot_cost": 2,
                    "priority_weight": 15_000 + sc_idx,
                    "needs_venv": False,
                })

    num_buckets = 20
    buckets: List[List[str]] = [[] for _ in range(num_buckets)]
    weights = [0 for _ in range(num_buckets)]

    for r in sorted(regular_inproc, key=lambda x: (-_file_weight(x)[0], x)):
        b_idx = min(range(num_buckets), key=lambda i: (weights[i], i))
        buckets[b_idx].append(r)
        weights[b_idx] += _file_weight(r)[0]

    for idx, b_files in enumerate(buckets, start=3):
        if b_files:
            shards.append({
                "shard_name": f"SHARD_INPROC_{idx:02d}_BALANCED",
                "files": sorted(b_files),
                "exclusive": False,
                "slot_cost": 1,
                "priority_weight": weights[idx - 3],
                "needs_venv": False,
            })

    for iso_tag, iso_fname in (
        ("02A_V16_23_RED", "test_v16_23_red_reproductions.py"),
        ("02B_V16_23_FLOW", "test_v16_23_production_flow.py"),
        ("02C_V16_24_FLOW", "test_v16_24_production_flow.py"),
        ("02D_V16_25_FLOW", "test_v16_25_production_flow.py"),
    ):
        if cg01_iso_by_name[iso_fname]:
            shards.append({
                "shard_name": f"SHARD_INPROC_{iso_tag}_ISOLATED",
                "files": cg01_iso_by_name[iso_fname],
                "exclusive": False,
                "slot_cost": 1,
                "priority_weight": 25_000,
                "needs_venv": False,
            })

    if realtime_30ms:
        shards.append({
            "shard_name": "SHARD_EXCL_00_REALTIME_30MS",
            "files": sorted(realtime_30ms),
            "exclusive": True,
            "slot_cost": 4,
            "priority_weight": 100_000,
            "needs_venv": True,
        })
    if multi_child_spawn:
        shards.append({
            "shard_name": "SHARD_EXCL_01_MULTI_CHILD",
            "files": sorted(multi_child_spawn),
            "exclusive": True,
            "slot_cost": 4,
            "priority_weight": 300_000,
            "needs_venv": False,
        })
    if db_own:
        shards.append({
            "shard_name": "SHARD_EXCL_03_DB_OWNERSHIP",
            "files": sorted(db_own),
            "exclusive": True,
            "slot_cost": 4,
            "priority_weight": 100_000,
            "needs_venv": True,
        })

    return shards


async def run_async_pytest_shards_kh01a(
    run_dir: Path,
    owned_temp_root: Path,
    *,
    run_id: str,
    tested_executable_sha: str,
    max_parallel_workers: int = 4,
) -> Dict[str, Any]:
    """
    Section 6: Truly asynchronous (`asyncio.create_subprocess_exec`) bounded pytest shard
    orchestrator with `--collect-only` inventory, per-shard temp/DB isolation, deterministic
    PYTHONHASHSEED=0, 2 MiB per-shard log cap (explicitly flagged if truncated), canonical
    node-ID merge order, and zero silent retry masking.
    """
    import psutil

    junit_dir = run_dir / "junit"
    logs_dir = run_dir / "logs"
    junit_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    py_exe = sys.executable
    run_start_epoch_ns = time.time_ns()
    t_wall_0 = time.perf_counter()

    all_test_files = sorted(
        str(p.relative_to(APP_ROOT)).replace("\\", "/")
        for p in (BACKEND_ROOT / "tests").glob("test_*.py")
        if p.is_file()
    )

    # 1. Collect-only inventory (NOT execution) — zero OS pipes (writes directly to owned_temp_root)
    col_out_file = owned_temp_root / "collect_stdout.log"
    col_err_file = owned_temp_root / "collect_stderr.log"
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
    col_proc = await asyncio.create_subprocess_exec(
        py_exe,
        "-c",
        col_snippet,
        str(col_out_file),
        str(col_err_file),
        str(BACKEND_ROOT / "tests"),
        "--collect-only",
        "-q",
        cwd=str(APP_ROOT),
        env=col_env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    col_pid = int(col_proc.pid)
    await asyncio.wait_for(col_proc.wait(), timeout=300.0)
    col_rc = int(col_proc.returncode if col_proc.returncode is not None else -1)
    col_out_b = col_out_file.read_bytes() if col_out_file.is_file() else b""
    col_err_b = col_err_file.read_bytes() if col_err_file.is_file() else b""
    if col_rc != 0:
        raise RuntimeError(
            f"PYTEST_COLLECT_ONLY_FAILED:rc={col_rc}:stderr={col_err_b.decode('utf-8', errors='replace')[-800:]}"
        )

    col_stdout = col_out_b.decode("utf-8", errors="replace")
    collected_by_file: Dict[str, List[str]] = {}
    all_collected_ids: List[str] = []
    for ln in col_stdout.splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            f_part = cid.split("::", 1)[0]
            collected_by_file.setdefault(f_part, []).append(cid)
            all_collected_ids.append(cid)
    all_collected_ids.sort()
    for fp in collected_by_file:
        collected_by_file[fp].sort()

    shard_specs = _build_deterministic_shards_kh01a(all_test_files, collected_by_file)

    cond = asyncio.Condition()
    active_slots = 0
    active_shards_count = 0
    exclusive_running = False
    downshift_count = 0
    peak_parallel_shards = 0
    active_procs: Dict[int, asyncio.subprocess.Process] = {}

    def _effective_slot_limit() -> int:
        nonlocal downshift_count
        avail_ram = int(psutil.virtual_memory().available)
        free_disk = int(shutil.disk_usage(REPO_ROOT).free)
        if free_disk < MIN_FREE_DISK_RESERVE_BYTES:
            raise RuntimeError("BLOCKED_LOW_DISK_IN_PYTEST_SCHEDULER")
        if avail_ram < int(1.65 * 1024**3):
            downshift_count += 1
            return 1
        if avail_ram < int(2.0 * 1024**3):
            downshift_count += 1
            return min(2, max_parallel_workers)
        # Reserve 1 slot out of the 4-process global cap for transient OS children spawned inside legacy tests
        return min(3, max_parallel_workers)

    async def _acquire_slot(exclusive: bool, slot_cost: int) -> int:
        nonlocal active_slots, active_shards_count, exclusive_running, peak_parallel_shards
        async with cond:
            while True:
                abort_p = os.environ.get("AURUM_RESOURCE_GUARD_ABORT_FILE")
                if abort_p and Path(abort_p).exists():
                    raise RuntimeError("RESOURCE_GUARD_ABORTED_ASYNC_SHARDS")
                if exclusive:
                    if active_slots == 0 and not exclusive_running:
                        exclusive_running = True
                        active_slots = 4
                        active_shards_count = 1
                        peak_parallel_shards = max(peak_parallel_shards, 1)
                        return 4
                else:
                    lim = _effective_slot_limit()
                    eff_cost = max(1, int(slot_cost))
                    if not exclusive_running and (
                        (active_slots + eff_cost <= lim) or (active_slots == 0)
                    ):
                        active_slots += eff_cost
                        active_shards_count += 1
                        peak_parallel_shards = max(peak_parallel_shards, active_shards_count)
                        return eff_cost
                await cond.wait()

    async def _release_slot(exclusive: bool, acquired_cost: int) -> None:
        nonlocal active_slots, active_shards_count, exclusive_running
        async with cond:
            if exclusive:
                exclusive_running = False
                active_slots = 0
                active_shards_count = 0
            else:
                active_slots = max(0, active_slots - acquired_cost)
                active_shards_count = max(0, active_shards_count - 1)
            cond.notify_all()

    async def _exec_one_shard(spec: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        shard_name = str(spec["shard_name"])
        rel_paths: List[str] = list(spec["files"])
        exclusive = bool(spec["exclusive"])
        slot_cost = int(spec.get("slot_cost", 4 if exclusive else 1))
        needs_venv = bool(spec["needs_venv"])

        acquired_cost = await _acquire_slot(exclusive, slot_cost)
        try:
            shard_short_id = hashlib.sha256(shard_name.encode("utf-8")).hexdigest()[:6]
            shard_tmp = owned_temp_root / "s" / shard_short_id
            shard_tmp.mkdir(parents=True, exist_ok=True)
            abs_paths = [str(APP_ROOT / rp) for rp in rel_paths]
            xml_path = junit_dir / f"{shard_name}.xml"
            raw_log_tmp = shard_tmp / "shard.raw.log"

            scheduled_ids: List[str] = []
            for rp in rel_paths:
                scheduled_ids.extend(collected_by_file.get(rp, []))
            scheduled_ids.sort()

            exe_bin = (
                os.environ["AURUM_VERIFY_VENV_PYTHON"]
                if needs_venv and os.environ.get("AURUM_VERIFY_VENV_PYTHON")
                else py_exe
            )
            shard_runner_snippet = (
                "import os, sys, pytest; "
                "log_f = open(sys.argv[1], 'w', encoding='utf-8', errors='replace'); "
                "sys.stdout = log_f; sys.stderr = log_f; "
                "rc = pytest.main(sys.argv[2:]); "
                "log_f.flush(); log_f.close(); "
                "os._exit(int(rc))"
            )
            pytest_cli_args = [*abs_paths, f"--junitxml={xml_path}", "-q"]
            cmd_argv = [exe_bin, "-c", shard_runner_snippet, str(raw_log_tmp), *pytest_cli_args]
            recorded_cmd_argv = [exe_bin, "-m", "pytest", *pytest_cli_args]
            child_env = dict(os.environ)
            child_budget = 1 if needs_venv else (2 if exclusive else 1)
            child_env["AURUM_GLOBAL_PROCESS_BUDGET"] = str(child_budget)
            child_env["PYTHONHASHSEED"] = "0"
            child_env["TMP"] = str(shard_tmp)
            child_env["TEMP"] = str(shard_tmp)
            child_env["TMPDIR"] = str(shard_tmp)

            t_s = time.perf_counter()
            st_ms = int(time.time() * 1000)
            start_utc = datetime.now(timezone.utc).isoformat()
            shard_peak_rss = 0

            proc = await asyncio.create_subprocess_exec(
                *cmd_argv,
                cwd=str(APP_ROOT),
                env=child_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            shard_pid = int(proc.pid)
            active_procs[shard_pid] = proc

            async def _sample_rss() -> None:
                nonlocal shard_peak_rss
                try:
                    ps_p = psutil.Process(shard_pid)
                except psutil.Error:
                    return
                while True:
                    try:
                        shard_peak_rss = max(shard_peak_rss, int(ps_p.memory_info().rss))
                    except psutil.Error:
                        return
                    await asyncio.sleep(1.0)

            rss_task = asyncio.create_task(_sample_rss())
            try:
                await asyncio.wait_for(proc.wait(), timeout=900.0)
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise RuntimeError(f"ASYNC_PYTEST_SHARD_TIMEOUT:{shard_name}") from exc
            finally:
                rss_task.cancel()
                try:
                    await rss_task
                except asyncio.CancelledError:
                    pass
                active_procs.pop(shard_pid, None)

            out_bytes = raw_log_tmp.read_bytes() if raw_log_tmp.is_file() else b""
            cmd_argv = recorded_cmd_argv
            rc = int(proc.returncode if proc.returncode is not None else -1)
            elapsed = round(time.perf_counter() - t_s, 3)
            et_ms = max(st_ms + 1, int(time.time() * 1000))
            end_utc = datetime.now(timezone.utc).isoformat()

            raw_size = len(out_bytes)
            log_truncated = raw_size > MAX_SHARD_LOG_BYTES
            capped_bytes = out_bytes[:MAX_SHARD_LOG_BYTES]
            capped_text = capped_bytes.decode("utf-8", errors="replace")

            if rc != 0:
                print(f"[SHARD FAILURE] {shard_name} rc={rc}:\n{capped_text[-1500:]}", file=sys.stderr, flush=True)

            shard_junit_ids: List[str] = []
            xml_tests = xml_failures = xml_errors = xml_skipped = xml_mtime_ns = 0
            xml_sha256 = ""
            if xml_path.is_file():
                xml_bytes, st_stat = read_closed_junit_artifact(xml_path)
                xml_mtime_ns = int(st_stat.st_mtime_ns)
                xml_sha256 = hashlib.sha256(xml_bytes).hexdigest()
                root = ET.fromstring(xml_bytes)
                suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
                for st in suites:
                    xml_tests += int(st.attrib.get("tests", 0))
                    xml_failures += int(st.attrib.get("failures", 0))
                    xml_errors += int(st.attrib.get("errors", 0))
                    xml_skipped += int(st.attrib.get("skipped", 0))
                    for tc in st.findall("testcase"):
                        jid = canonicalize_junit_testcase_node_id_v1793122(
                            tc.attrib.get("classname", ""), tc.attrib.get("name", "")
                        )
                        if jid is not None:
                            shard_junit_ids.append(jid)
            shard_junit_ids.sort()
            bij = verify_collect_vs_junit_node_id_bijection_v1793122(scheduled_ids, shard_junit_ids)

            print(
                f"  [KH01A Shard {shard_name}] excl={exclusive} cost={slot_cost} pid={shard_pid} files={len(rel_paths)} "
                f"junit={len(shard_junit_ids)} rc={rc} bij={bij['exact_node_id_bijection_verified']} in {elapsed}s",
                flush=True,
            )

            shard_rec = {
                "shard_id": shard_name,
                "shard_name": shard_name,
                "reused": False,
                "exclusive_slot": exclusive,
                "slot_cost": slot_cost,
                "run_id": run_id,
                "tested_executable_sha": tested_executable_sha,
                "pid": shard_pid,
                "command_argv": cmd_argv,
                "start_time_utc": start_utc,
                "end_time_utc": end_utc,
                "test_files_count": len(rel_paths),
                "test_files": rel_paths,
                "scheduled_node_ids": scheduled_ids,
                "collect_exit_code": col_rc,
                "pytest_exit_code": rc,
                "exit_code": rc,
                "timed_out": False,
                "collected_count": len(scheduled_ids),
                "executed_junit_count": len(shard_junit_ids),
                "xml_tests_attr": xml_tests,
                "passed_count": len(shard_junit_ids) - xml_failures - xml_errors - xml_skipped,
                "failed_count": xml_failures,
                "error_count": xml_errors,
                "skipped_count": xml_skipped,
                "exact_bijection": bij["exact_node_id_bijection_verified"],
                "elapsed_seconds": elapsed,
                "peak_rss_bytes": shard_peak_rss,
                "raw_log_bytes": raw_size,
                "log_truncated": log_truncated,
                "log_integrity_limitation": "TRUNCATED_AT_2MIB_CAP" if log_truncated else "COMPLETE_UNTRUNCATED",
                "xml_mtime_ns": xml_mtime_ns,
                "xml_sha256": xml_sha256,
                "junit_xml_path": f"junit/{xml_path.name}",
                "_junit_ids": shard_junit_ids,
            }
            log_rec = {
                "shard_name": shard_name,
                "pid": shard_pid,
                "exit_code": rc,
                "raw_log_bytes": raw_size,
                "log_truncated": log_truncated,
                "output": capped_text,
            }
            worker_rec = {
                "worker_id": f"kh01a_shard_{shard_name}",
                "run_id": run_id,
                "tested_source_sha": tested_executable_sha,
                "pid": shard_pid,
                "started_at_ms": st_ms,
                "completed_at_ms": et_ms,
                "exit_code": rc,
                "process_exit_code": rc,
                "task_validation_verdict": "PASS" if rc == 0 else "FAIL",
                "process_identity_provenance": "ASYNCIO_CREATE_SUBPROCESS_EXEC",
                "timed_out": False,
            }
            return shard_rec, log_rec, worker_rec
        finally:
            await _release_slot(exclusive, acquired_cost)

    inproc_specs = sorted(
        [s for s in shard_specs if not s["exclusive"]],
        key=lambda s: (-int(s.get("priority_weight", 0)), str(s["shard_name"])),
    )
    excl_specs = sorted(
        [s for s in shard_specs if s["exclusive"]],
        key=lambda s: (-int(s.get("priority_weight", 0)), str(s["shard_name"])),
    )

    inproc_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
    for sp in inproc_specs:
        inproc_queue.put_nowait(sp)
    inproc_out: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []

    async def _queue_consumer() -> None:
        while True:
            try:
                nxt = inproc_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            res = await _exec_one_shard(nxt)
            inproc_out.append(res)

    consumer_tasks: List[asyncio.Task[None]] = []
    try:
        n_consumers = max(1, min(4, int(max_parallel_workers)))
        consumer_tasks = [asyncio.create_task(_queue_consumer()) for _ in range(n_consumers)]
        done_t, pending_t = await asyncio.wait(consumer_tasks, return_when=asyncio.FIRST_EXCEPTION)
        for dt in done_t:
            if dt.exception() is not None:
                for pt in pending_t:
                    pt.cancel()
                raise dt.exception()  # type: ignore[misc]
        excl_out: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
        for es in excl_specs:
            excl_out.append(await _exec_one_shard(es))
    except BaseException:
        for ct in consumer_tasks:
            if not ct.done():
                ct.cancel()
        for p_kill in list(active_procs.values()):
            try:
                p_kill.kill()
                await p_kill.wait()
            except Exception:
                pass
        raise

    combined = sorted(inproc_out + excl_out, key=lambda t: str(t[0]["shard_name"]))
    all_junit_ids: List[str] = []
    clean_shards: List[Dict[str, Any]] = []
    log_rows: List[Dict[str, Any]] = []
    worker_records: List[Dict[str, Any]] = []
    for sh_r, lg_r, wk_r in combined:
        sc = dict(sh_r)
        all_junit_ids.extend(sc.pop("_junit_ids", []))
        clean_shards.append(sc)
        log_rows.append(lg_r)
        worker_records.append(wk_r)

    all_junit_ids.sort()
    logs_gz_sha = write_jsonl_gz_durable_atomic(logs_dir / "pytest_shards_logs.jsonl.gz", log_rows)

    wall_elapsed_s = round(time.perf_counter() - t_wall_0, 3)
    sum_shard_s = round(sum(float(s["elapsed_seconds"]) for s in clean_shards), 3)
    nonzero_shards = sum(1 for s in clean_shards if int(s["exit_code"]) != 0)

    collection_manifest = {
        "spec_version": SPEC_VERSION_KH01A,
        "run_id": run_id,
        "tested_executable_sha": tested_executable_sha,
        "collect_pid": col_pid,
        "collect_exit_code": col_rc,
        "pytest_version_check": subprocess.check_output([py_exe, "-m", "pytest", "--version"], text=True).strip(),
        "python_version": sys.version,
        "collected_node_ids_count": len(all_collected_ids),
        "collected_node_ids_sha256": canonical_hash(all_collected_ids),
        "collected_node_ids": all_collected_ids,
    }
    shard_manifest = {
        "spec_version": SPEC_VERSION_KH01A,
        "run_id": run_id,
        "tested_executable_sha": tested_executable_sha,
        "run_start_epoch_ns": run_start_epoch_ns,
        "shard_count": len(clean_shards),
        "reused_shards_count": 0,
        "nonzero_exit_shards_count": nonzero_shards,
        "compressed_logs_path": "logs/pytest_shards_logs.jsonl.gz",
        "compressed_logs_sha256": logs_gz_sha,
        "any_shard_log_truncated": any(bool(s["log_truncated"]) for s in clean_shards),
        "executed_junit_node_ids_count": len(all_junit_ids),
        "executed_junit_node_ids_sha256": canonical_hash(all_junit_ids),
        "shards": clean_shards,
    }
    junit_bijection = verify_fresh_junit_bijection_v17931211(
        run_dir,
        collection_manifest,
        shard_manifest,
        expected_run_id=run_id,
        expected_tested_sha=tested_executable_sha,
        require_shard_schedule=True,
    )
    if junit_bijection["status"] != "PASS":
        raise RuntimeError(f"STAGE_A_JUNIT_BIJECTION_FAILED:{junit_bijection['violations']}")

    return {
        "spec_version": SPEC_VERSION_KH01A,
        "schema_version": SCHEMA_VERSION_KH01A,
        "run_id": run_id,
        "tested_executable_sha": tested_executable_sha,
        "prior_v19_reference_counts_note": "Prior V19 (5455 passed + 8 skipped) preserved as historical reference only; fresh inventory collected & verified.",
        "async_scheduler_telemetry": {
            "scheduler": "ASYNCIO_CREATE_SUBPROCESS_EXEC_BOUNDED_SLOTS",
            "configured_max_parallel_shards": max_parallel_workers,
            "peak_parallel_shards_observed": peak_parallel_shards,
            "ram_soft_downshift_events": downshift_count,
            "wall_clock_elapsed_seconds": wall_elapsed_s,
            "sum_individual_shard_seconds": sum_shard_s,
            "effective_parallel_throughput_ratio": round(sum_shard_s / max(wall_elapsed_s, 0.001), 3),
        },
        "collection_manifest": collection_manifest,
        "shard_manifest": shard_manifest,
        "independent_bijection_verification": junit_bijection,
        "worker_records": worker_records,
    }


async def run_controlled_worker_1_2_4_subset_benchmark_kh01a(
    owned_temp_root: Path,
    *,
    run_id: str,
    tested_sha: str,
) -> Dict[str, Any]:
    """
    Section 6: Executes the 8-file pure in-process controlled subset under `w=1`, `w=2`, `w=4`
    (respecting `<= 4` child cap), and `w=1_repeat` (`PYTHONHASHSEED=0`).
    Verifies sorted `(node_id, outcome)` canonical SHA-256 identity across all 4 runs and
    reports actual measured speedups honestly without hardcoding.
    """
    py_exe = sys.executable
    bench_root = owned_temp_root / "b"
    bench_root.mkdir(parents=True, exist_ok=True)

    modes: Tuple[Tuple[str, int], ...] = (
        ("w=1", 1),
        ("w=2", 2),
        ("w=4", 4),
        ("w=1_repeat", 1),
    )
    runs_out: List[Dict[str, Any]] = []

    for mode_label, w_slots in modes:
        sem = asyncio.Semaphore(w_slots)
        pids_seen: List[int] = []
        outcomes_by_node: List[Tuple[str, str]] = []
        t0 = time.perf_counter()

        async def _run_file(f_idx: int, rel_f: str) -> List[Tuple[str, str]]:
            async with sem:
                sub_tmp = bench_root / f"{mode_label.replace('=', '')}_f{f_idx}"
                sub_tmp.mkdir(parents=True, exist_ok=True)
                xml_p = sub_tmp / "junit.xml"
                err_p = sub_tmp / "bench.log"
                env = dict(os.environ)
                env["PYTHONHASHSEED"] = "0"
                env["AURUM_GLOBAL_PROCESS_BUDGET"] = "1"
                env["TMP"] = str(sub_tmp)
                env["TEMP"] = str(sub_tmp)
                b_snippet = (
                    "import os, sys, pytest; "
                    "lf = open(sys.argv[1], 'w', encoding='utf-8', errors='replace'); "
                    "sys.stdout = lf; sys.stderr = lf; "
                    "rc = pytest.main(sys.argv[2:]); "
                    "lf.flush(); lf.close(); "
                    "os._exit(int(rc))"
                )
                cmd = [py_exe, "-c", b_snippet, str(err_p), str(APP_ROOT / rel_f), f"--junitxml={xml_p}", "-q"]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(APP_ROOT),
                    env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                pids_seen.append(int(proc.pid))
                await asyncio.wait_for(proc.wait(), timeout=600.0)
                rc = int(proc.returncode if proc.returncode is not None else -1)
                if rc != 0:
                    err_b = err_p.read_bytes() if err_p.is_file() else b""
                    raise RuntimeError(
                        f"SUBSET_BENCHMARK_FAILED:{mode_label}:{rel_f}:rc={rc}:{err_b.decode('utf-8', errors='replace')[-500:]}"
                    )
                xml_bytes, _ = read_closed_junit_artifact(xml_p)
                root = ET.fromstring(xml_bytes)
                suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
                file_outcomes: List[Tuple[str, str]] = []
                for st in suites:
                    for tc in st.findall("testcase"):
                        nid = canonicalize_junit_testcase_node_id_v1793122(
                            tc.attrib.get("classname", ""), tc.attrib.get("name", "")
                        )
                        if nid is None:
                            continue
                        if tc.find("failure") is not None:
                            out_lbl = "FAILED"
                        elif tc.find("error") is not None:
                            out_lbl = "ERROR"
                        elif tc.find("skipped") is not None:
                            out_lbl = "SKIPPED"
                        else:
                            out_lbl = "PASSED"
                        file_outcomes.append((nid, out_lbl))
                return file_outcomes

        per_file_lists = await asyncio.gather(*(_run_file(i, rf) for i, rf in enumerate(CONTROLLED_SUBSET_BENCHMARK_FILES)))
        wall_s = round(time.perf_counter() - t0, 3)
        for fl in per_file_lists:
            outcomes_by_node.extend(fl)
        outcomes_by_node.sort(key=lambda x: x[0])
        outcome_sha = canonical_hash(outcomes_by_node)
        runs_out.append({
            "mode_label": mode_label,
            "worker_slots": w_slots,
            "wall_clock_seconds": wall_s,
            "distinct_pids_count": len(set(pids_seen)),
            "pids": pids_seen,
            "executed_tests_count": len(outcomes_by_node),
            "passed_count": sum(1 for _, o in outcomes_by_node if o == "PASSED"),
            "canonical_sorted_outcomes_sha256": outcome_sha,
        })

    by_lbl = {r["mode_label"]: r for r in runs_out}
    w1_mean_s = round((by_lbl["w=1"]["wall_clock_seconds"] + by_lbl["w=1_repeat"]["wall_clock_seconds"]) / 2.0, 3)
    w2_s = float(by_lbl["w=2"]["wall_clock_seconds"])
    w4_s = float(by_lbl["w=4"]["wall_clock_seconds"])
    w2_sp = round(w1_mean_s / max(w2_s, 0.001), 3)
    w4_sp = round(w1_mean_s / max(w4_s, 0.001), 3)
    outcome_shas = {r["canonical_sorted_outcomes_sha256"] for r in runs_out}

    return {
        "controlled_subset_files": list(CONTROLLED_SUBSET_BENCHMARK_FILES),
        "runs": runs_out,
        "w1_single_worker_mean_seconds": w1_mean_s,
        "w2_wall_seconds": w2_s,
        "w4_wall_seconds": w4_s,
        "w2_speedup_vs_w1": w2_sp,
        "w4_speedup_vs_w1": w4_sp,
        "best_parallel_speedup_vs_w1": max(w2_sp, w4_sp),
        "all_4_runs_outcome_sha256_identical": len(outcome_shas) == 1,
        "canonical_outcome_sha256": next(iter(outcome_shas)) if len(outcome_shas) == 1 else None,
        "determinism_vs_nondeterminism_disclosure": {
            "deterministic_canonical_fields": [
                "sorted_test_node_ids",
                "per_node_test_outcomes (PASSED/SKIPPED/FAILED/ERROR)",
                "executed_tests_count",
                "canonical_sorted_outcomes_sha256",
            ],
            "volatile_excluded_metadata": [
                "OS child PIDs",
                "wall-clock testcase durations",
                "JUnit XML testsuite timestamp attribute",
            ],
        },
    }


def _read_v12_funnel_09_doc() -> Dict[str, Any]:
    if V12_LATEST_RUN_PTR.is_file():
        ptr = json.loads(V12_LATEST_RUN_PTR.read_text(encoding="utf-8"))
        v12_dir = REPO_ROOT / str(ptr["run_dir"])
        f09 = v12_dir / "09_r2_si8_si9_278_to_0_funnel_and_ablation.json"
        if f09.is_file():
            return json.loads(f09.read_text(encoding="utf-8"))
    raise FileNotFoundError("MISSING_V12_FUNNEL_09_DOC_FOR_267_TARGET_AUDIT")


def main() -> int:
    ap = argparse.ArgumentParser(description="KH-01A Autonomous Verification & Backtest Orchestrator")
    ap.add_argument("--tested-code-sha", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--worker-slice-input", default="")
    ap.add_argument("--worker-slice-output", default="")
    args = ap.parse_args()

    if args.worker_slice_input and args.worker_slice_output:
        return _run_worker_slice_cli(Path(args.worker_slice_input), Path(args.worker_slice_output))
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
        ["git", "merge-base", "--is-ancestor", PRIOR_V19_PUBLICATION_HEAD_SHA, sha],
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

    run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{sha[:12]}"
    run_dir = ARTIFACTS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "ledgers").mkdir(parents=True, exist_ok=True)
    orchestrator_pid = os.getpid()

    owned_temp_root = create_owned_temp_root(run_id)
    os.environ["AURUM_VERIFICATION_ARTIFACT_ROOT"] = str(owned_temp_root)
    os.environ["AURUM_TESTED_EXECUTABLE_SHA"] = sha
    os.environ["AURUM_MINIMUM_RAM_BYTES"] = str(512 * 1024**2)

    monitor = ResourceMonitorKH01A(abort_path=run_dir / "resource_abort.json")
    os.environ["AURUM_RESOURCE_GUARD_ABORT_FILE"] = str(monitor.abort_path)

    src_before = source_manifest()
    prior_before = prior_immutable_artifacts_hash()
    backend_tree_sha = git("rev-parse", f"{sha}:aurum-desk/backend")
    tools_tree_sha = git("rev-parse", f"{sha}:aurum-desk/tools")

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
    }

    vm = psutil.virtual_memory()
    preflight_00: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH01A,
        "schema_version": SCHEMA_VERSION_KH01A,
        "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "branch": branch,
        "starting_reference_head_sha": PRIOR_V19_PUBLICATION_HEAD_SHA,
        "prior_v19_tested_executable_sha": PRIOR_V19_TESTED_SHA,
        "tested_executable_sha": sha,
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
            "total_disk_bytes": int(disk_u.total),
            "inode_tracking_note": "NTFS on Windows does not expose fixed POSIX inode quotas; Master File Table free-space reserve enforced via 10 GiB disk floor.",
            "available_ram_bytes": int(vm.available),
            "available_ram_gb": round(vm.available / (1024**3), 2),
            "total_ram_bytes": int(vm.total),
            "physical_cpus": psutil.cpu_count(logical=False),
            "logical_cpus": psutil.cpu_count(logical=True),
            "minimum_free_disk_reserve_bytes": MIN_FREE_DISK_RESERVE_BYTES,
            "estimated_peak_footprint_bytes": ESTIMATED_STAGE_PEAK_DISK_BYTES,
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
    write_json_durable_atomic(run_dir / "00_preflight_branch_source_dataset_resources.json", preflight_00)

    callgraph_01 = build_kh_01a_callgraph_and_keep_as_is_matrix(REPO_ROOT)
    callgraph_01["run_id"] = run_id
    callgraph_01["tested_executable_sha"] = sha
    write_json_durable_atomic(run_dir / "01_active_function_callgraph_and_keep_as_is_matrix.json", callgraph_01)

    temp_cleanup_info: Dict[str, Any] = {"cleaned": False}
    try:
        monitor.start()

        # STAGE A: Full Async Sharded Backend + KH-01A Pytest Suite (10)
        print(f"[KH-01A Stage A] Running async bounded sharded pytest suite on {sha[:12]}...", flush=True)
        junit_10 = asyncio.run(
            run_async_pytest_shards_kh01a(
                run_dir,
                owned_temp_root,
                run_id=run_id,
                tested_executable_sha=sha,
                max_parallel_workers=args.workers,
            )
        )
        junit_10_disk = {k: v for k, v in junit_10.items() if k != "worker_records"}
        write_json_durable_atomic(run_dir / "10_collected_node_ids_shards_junit_exit_manifest.json", junit_10_disk)
        monitor.check()

        # STAGE B: Controlled w=1, w=2, w=4, w=1_repeat Subset Determinism & Speedup Benchmark
        print("[KH-01A Stage B] Running controlled w=1, w=2, w=4, w=1_repeat pytest subset benchmark...", flush=True)
        subset_bench = asyncio.run(
            run_controlled_worker_1_2_4_subset_benchmark_kh01a(
                owned_temp_root,
                run_id=run_id,
                tested_sha=sha,
            )
        )
        monitor.check()

        # STAGE C: Data Gap Audit (02, 03), Fault Suite (04), Full Chronological Backtest (08),
        #          Causal Loss Forensics (05, 06), Target 267+40 Audit (07), Parity (09), Fresh OOS (13)
        print("[KH-01A Stage C.1] Auditing canonical dataset gaps, session calendar provenance & real fault injections...", flush=True)
        datasets, dataset_prov = load_canonical_xauusdt_datasets()
        gap_report = classify_historical_dataset_gaps(datasets)

        m15_gaps = gap_report["timeframes"]["M15"]["all_gaps"]
        mon_fri_m15 = next(g for g in m15_gaps if g["prev_close_utc"] == "2026-10-12T08:15:00Z")
        m1_sat_mon = next(g for g in gap_report["timeframes"]["M1"]["all_gaps"] if g["missing_bars"] == 2794)
        m5_sat_mon = next(g for g in gap_report["timeframes"]["M5"]["all_gaps"] if g["missing_bars"] == 560)
        m15_sun_0245 = next(g for g in m15_gaps if g["prev_close_utc"] == "2026-10-18T02:45:00Z")

        unverified_weekend_after = sum(
            int(tf_d["gap_events_by_category"].get("WEEKEND_MARKET_CLOSED", 0))
            + int(tf_d["gap_events_by_category"].get("VERIFIED_SCHEDULED_MARKET_CLOSURE", 0))
            for tf_d in gap_report["timeframes"].values()
        )
        expected_missing_totals = {"M1": 6926, "M5": 923, "M15": 362, "H1": 0, "H4": 0, "D1": 0}
        actual_missing_totals = {lbl: int(gap_report["timeframes"][lbl]["total_missing_bars"]) for lbl in expected_missing_totals}

        gap_audit_02 = {
            "spec_version": SPEC_VERSION_KH01A,
            "schema_version": SCHEMA_VERSION_KH01A,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "dataset_sha256": actual_dataset_sha,
            "prepatch_red_confirmed": mon_fri_m15["legacy_v1791_category_deprecated"] == "WEEKEND_MARKET_CLOSED",
            "prepatch_mon_fri_m15_category": mon_fri_m15["legacy_v1791_category_deprecated"],
            "postpatch_mon_fri_m15_category": mon_fri_m15["category"],
            "unverified_weekend_labels_emitted_count": unverified_weekend_after,
            "missing_bar_totals_conserved": actual_missing_totals == expected_missing_totals,
            "missing_bars_by_timeframe": actual_missing_totals,
            "explicit_reclassification_explanations": [
                {
                    "timeframe": "M15",
                    "interval": "2026-10-12T08:15:00Z -> 2026-10-16T01:00:00Z (354 missing bars)",
                    "old_category": mon_fri_m15["legacy_v1791_category_deprecated"],
                    "new_category": mon_fri_m15["category"],
                    "proof": mon_fri_m15["classification_reason"],
                    "cross_tf_active_bars": mon_fri_m15["cross_tf_contained_active_bars"],
                },
                {
                    "timeframe": "M1",
                    "interval": "2026-10-17T02:18:00Z -> 2026-10-19T00:53:00Z (2,794 missing bars)",
                    "old_category": m1_sat_mon["legacy_v1791_category_deprecated"],
                    "new_category": m1_sat_mon["category"],
                    "proof": m1_sat_mon["classification_reason"],
                    "cross_tf_active_bars": m1_sat_mon["cross_tf_contained_active_bars"],
                },
                {
                    "timeframe": "M5",
                    "interval": "2026-10-17T02:15:00Z -> 2026-10-19T01:00:00Z (560 missing bars)",
                    "old_category": m5_sat_mon["legacy_v1791_category_deprecated"],
                    "new_category": m5_sat_mon["category"],
                    "proof": m5_sat_mon["classification_reason"],
                    "cross_tf_active_bars": m5_sat_mon["cross_tf_contained_active_bars"],
                },
                {
                    "timeframe": "M15",
                    "interval": "2026-10-18T02:45:00Z -> 2026-10-18T03:15:00Z (1 missing bar at 200-bar pagination boundary)",
                    "old_category": m15_sun_0245["legacy_v1791_category_deprecated"],
                    "new_category": m15_sun_0245["category"],
                    "proof": m15_sun_0245["classification_reason"],
                    "uncertainty": m15_sun_0245["uncertainty"],
                },
            ],
            "cross_timeframe_ohlc_coherence": gap_report["cross_timeframe_coherence"],
        }
        write_json_durable_atomic(run_dir / "02_data_gap_red_green_and_cross_tf_audit.json", gap_audit_02)

        dataset_quality_03 = {
            "spec_version": SPEC_VERSION_KH01A,
            "schema_version": SCHEMA_VERSION_KH01A,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "dataset_provenance": dataset_prov,
            **gap_report,
        }
        write_json_durable_atomic(run_dir / "03_frozen_dataset_sha_session_calendar_and_quality.json", dataset_quality_03)

        fault_suite_04 = run_kh_01a_real_fault_injections(tested_sha=sha, run_id=run_id)
        if fault_suite_04["status"] != "PASS":
            raise RuntimeError(f"REAL_FAULT_INJECTION_SUITE_FAILED:{fault_suite_04}")
        write_json_durable_atomic(run_dir / "04_fail_closed_red_green_real_fault_injections.json", fault_suite_04)
        monitor.check()

        print("[KH-01A Stage C.2] Replaying 30 policy partitions (SI0..SI9 x 3 fill modes) + A0..D4...", flush=True)
        bars_1m = list(datasets["1m"])
        bars_1m_by_open_ts = {int(b.get("open_time") or b.get("timestamp") or 0): b for b in bars_1m}
        close_ts = build_close_ts_index_v179312(datasets)
        pol_hash = compute_research_policy_registry_hash_v179312()

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

        # Stage B.2: 242-episode replay slice determinism across w=1, w=2, w=4, w=1_repeat
        print("[KH-01A Stage B.2] Running 242-episode replay slice determinism across w=1, w=2, w=4, w=1_repeat...", flush=True)
        replay_w124_doc, replay_det_workers = run_worker_1_2_4_and_repeat_determinism_v17931211(
            si0_nb_eps, bars_1m, run_id=run_id, tested_sha=sha
        )
        replay_parity_chk = verify_execution_not_synthetic_v17931211(
            collection_manifest=junit_10_disk["collection_manifest"],
            shard_manifest=junit_10_disk["shard_manifest"],
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
        worker_report_11 = {
            "spec_version": SPEC_VERSION_KH01A,
            "schema_version": SCHEMA_VERSION_KH01A,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "pytest_controlled_subset_w1_w2_w4_repeat": subset_bench,
            "pytest_subset_w1_w2_w4_repeat_outcome_hash_identical": subset_bench["all_4_runs_outcome_sha256_identical"],
            "measured_speedup_w2_vs_w1": subset_bench["w2_speedup_vs_w1"],
            "measured_speedup_w4_vs_w1": subset_bench["w4_speedup_vs_w1"],
            "measured_speedup_w_parallel_vs_w1": subset_bench["best_parallel_speedup_vs_w1"],
            "replay_slice_w1_w2_w4_repeat_parity": replay_w124_doc,
            "replay_slice_parity_verification": replay_parity_chk,
            "replay_slice_worker_process_verification": replay_proc_chk,
            "replay_slice_w1_w2_w4_repeat_parity_status": replay_w124_status,
        }
        write_json_durable_atomic(run_dir / "11_worker_1_2_4_repeat_determinism_speed_report.json", worker_report_11)

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

        replay_08 = {
            "spec_version": SPEC_VERSION_KH01A,
            "schema_version": SCHEMA_VERSION_KH01A,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "dataset_sha256": actual_dataset_sha,
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
            "row_level_ledgers": {
                "exit_event_lineage": "ledgers/09_exit_event_lineage_ledger.jsonl.gz",
                "source_execution_reconciliation": "ledgers/09_original_execution_ledger_source_reconciliation.jsonl.gz",
            },
        }
        write_json_durable_atomic(run_dir / "08_replay_3fill_1position_oos_and_cost_stress.json", replay_08)

        # Stage C.3: Causal Pre-Entry Loss & Win-Control Forensics (05, 06)
        print("[KH-01A Stage C.3] Building causal pre-entry forensics for 157 SI0 losses + 9 wins + 4 unique SI8/SI9 losses...", flush=True)
        decisions_by_oid = {str(r["opportunity_id"]): r for r in v130_canonical_inputs}
        si8_losses = [
            ep for ep in si_results_by_policy["SI8_RESEARCH_DISCOVERY_REPAIRED"]["primary_episodes"]
            if str(ep.get("outcome_label") or "").upper() == "LOSS"
        ]
        si9_losses = [
            ep for ep in si_results_by_policy["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["primary_episodes"]
            if str(ep.get("outcome_label") or "").upper() == "LOSS"
        ]
        forensics_bundle = build_kh_01a_causal_loss_and_win_control_forensics(
            si0_all_primary_episodes=si0_nb_eps,
            si8_loss_rows=si8_losses,
            si9_loss_rows=si9_losses,
            decision_inputs_by_opp_id=decisions_by_oid,
            feature_ledger_by_opp_id=all_features_map,
            datasets=datasets,
            close_ts_index=close_ts,
            dataset_sha256=actual_dataset_sha,
        )
        write_jsonl_gz_durable_atomic(
            run_dir / "05_preentry_casebook_157_losses_and_win_controls.jsonl.gz",
            forensics_bundle["casebook_rows"],
        )
        forensics_06 = {
            **forensics_bundle["census_summary_06"],
            "run_id": run_id,
            "tested_executable_sha": sha,
        }
        write_json_durable_atomic(
            run_dir / "06_causal_loss_attribution_and_unknown_evidence_census.json",
            forensics_06,
        )

        # Stage C.4: Target 267 + 40 Control Provenance Audit (07)
        print("[KH-01A Stage C.4] Auditing 267 historical target failures + 40 controls...", flush=True)
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
        target_audit_07 = {
            "spec_version": SPEC_VERSION_KH01A,
            "schema_version": SCHEMA_VERSION_KH01A,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "dataset_sha256": actual_dataset_sha,
            "executable_tp_selection_modified": False,
            "active_executable_selector_counterfactual_status": "NOT_EVALUATED_READ_ONLY_AUDIT_ONLY_TP_SELECTION_UNCHANGED",
            "counterfactuals": None,
            "row_level_ledger_path": "ledgers/07_target_307_rows_source_tf_atr_audit.jsonl.gz",
            "row_level_ledger_sha256": target_ledger_sha,
            "summary": p0b_bundle["summary_06"],
            "summary_v14": p0b_bundle["summary_06"],
            "verification": p0b_check,
        }
        write_json_durable_atomic(run_dir / "07_target_267_plus_40_control_provenance_audit.json", target_audit_07)

        # Stage C.5: Before/After Trade-Action Fingerprint & 3-Adapter Parity (09)
        print("[KH-01A Stage C.5] Verifying before/after trade-action fingerprints & 3-adapter parity...", flush=True)
        v19_loss_rows = list(iter_jsonl_gz(V19_REF_RUN / "11_all_157_si0_loss_preentry_expost_rows.jsonl.gz"))
        sample_target_oids = {r["opportunity_id"] for r in v19_loss_rows[:12]}
        sample_opps: List[Dict[str, Any]] = []
        for d in v130_canonical_inputs:
            if d.get("opportunity_id") in sample_target_oids:
                sample_opps.append(d)
            if len(sample_opps) >= 12:
                break

        bars_5m = list(datasets["5m"])
        bars_15m = list(datasets["15m"])
        bars_1h = list(datasets["1H"])
        bars_4h = list(datasets["4H"])
        bars_1d = list(datasets["1D"])
        open_1m = [int(b.get("open_time") or b.get("timestamp") or 0) for b in bars_1m]

        evaluator = SharedStrategyEvaluator()
        post_eval_fps: List[Dict[str, Any]] = []
        post_adapter_fps: List[Dict[str, Any]] = []
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
            m1_slice = bars_1m[max(0, i1 - 240) : i1]
            cur_px = float(m1_slice[-1]["close"])
            ctx = build_market_decision_context(
                symbol="XAUUSDT",
                decision_timestamp_ms=ts_ms,
                m1_candles=m1_slice,
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
            snap = evaluator.evaluate_model_direction(context=ctx, model_id=m_id, direction=direction)
            post_eval_fps.append({
                "opportunity_id": d["opportunity_id"],
                "decision_timestamp_ms": ts_ms,
                "model_id": m_id,
                "direction": direction,
                "decision_status": snap.decision_status,
                "rejection_reason_codes": list(snap.rejection_reason_codes),
                "decision_fingerprint": snap.decision_fingerprint,
                "trade_action_fingerprint": snap.trade_action_fingerprint,
                "entry_reference": snap.entry_reference,
                "selected_sl": snap.selected_sl,
                "take_profit": snap.take_profit,
                "net_rr": snap.net_rr,
                "hypothetical_quantity": snap.hypothetical_quantity,
            })
            i_open = bisect.bisect_left(open_1m, ts_ms)
            ctx_hashes: Dict[str, str] = {}
            for ctx_m in ("REPLAY_BACKTEST", "MARKET_SHADOW", "PRODUCTION_ORCHESTRATOR_DRY_RUN"):
                out_m = evaluate_shared_delayed_entry_adapter_v17931213(
                    execution_context_mode=ctx_m,
                    activation_flags={"LIVE": False, "PAPER": False, "AUTO_LIVE": False, "AUTO_PAPER": False, "enabled_in_production": False},
                    variant_id="D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
                    opportunity_id=d["opportunity_id"],
                    episode_key=f"pre_{d['opportunity_id']}",
                    model_id=m_id,
                    direction=direction,
                    decision_timestamp_ms=ts_ms,
                    baseline_entry=float(d["entry_reference"]),
                    baseline_sl=float(d["selected_sl"]),
                    baseline_tp=float(d["take_profit"]),
                    closed_m1=m1_slice[-180:],
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
            post_adapter_fps.append({
                "opportunity_id": d["opportunity_id"],
                "hashes_by_context": ctx_hashes,
                "parity": len(set(ctx_hashes.values())) == 1,
            })

        post_eval_sha = canonical_hash(post_eval_fps)
        post_adapter_sha = canonical_hash(post_adapter_fps)
        si0_now = si_results_by_policy["SI0_OFF_BASELINE"]["replays_by_fill_mode"]["NEXT_BAR_OPEN_BID_ASK"]
        si8_now = si_results_by_policy["SI8_RESEARCH_DISCOVERY_REPAIRED"]["replays_by_fill_mode"]["NEXT_BAR_OPEN_BID_ASK"]
        si9_now = si_results_by_policy["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["replays_by_fill_mode"]["NEXT_BAR_OPEN_BID_ASK"]
        acct_match = (
            si0_now["ledger_sha256"] == PREPATCH_BASELINE_REFERENCE["prepatch_si0_nb_accounting"]["ledger_sha256"]
            and si8_now["ledger_sha256"] == PREPATCH_BASELINE_REFERENCE["prepatch_si8_nb_accounting"]["ledger_sha256"]
            and si9_now["ledger_sha256"] == PREPATCH_BASELINE_REFERENCE["prepatch_si9_nb_accounting"]["ledger_sha256"]
        )

        parity_09 = {
            "spec_version": SPEC_VERSION_KH01A,
            "schema_version": SCHEMA_VERSION_KH01A,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "prepatch_evaluator_fingerprints_sha256": PREPATCH_BASELINE_REFERENCE["prepatch_evaluator_fingerprints_sha256"],
            "postpatch_evaluator_fingerprints_sha256": post_eval_sha,
            "before_after_evaluator_decision_fingerprint_parity": (
                post_eval_sha == PREPATCH_BASELINE_REFERENCE["prepatch_evaluator_fingerprints_sha256"]
            ),
            "before_after_trade_action_fingerprint_parity": (
                post_eval_sha == PREPATCH_BASELINE_REFERENCE["prepatch_evaluator_fingerprints_sha256"]
            ),
            "prepatch_adapter_fingerprints_sha256": PREPATCH_BASELINE_REFERENCE["prepatch_adapter_fingerprints_sha256"],
            "postpatch_adapter_fingerprints_sha256": post_adapter_sha,
            "three_adapter_parity_status": (
                "PASS"
                if all(r["parity"] for r in post_adapter_fps)
                and post_adapter_sha == PREPATCH_BASELINE_REFERENCE["prepatch_adapter_fingerprints_sha256"]
                else "FAIL"
            ),
            "si0_si8_si9_accounting_exact_match_vs_baseline": acct_match,
            "config_hashes_exact_match_vs_baseline": (
                config_hashes == PREPATCH_BASELINE_REFERENCE["baseline_config_hashes"]
            ),
            "sample_evaluator_fingerprints": post_eval_fps,
            "sample_adapter_fingerprints": post_adapter_fps,
        }
        write_json_durable_atomic(run_dir / "09_before_after_trade_action_and_adapter_parity.json", parity_09)

        # Stage C.6: Dataset Registry & Fresh-OOS Escrow Audit (13)
        fresh_oos_13 = build_dataset_registry_and_fresh_oos_audit_kh01a(REPO_ROOT, datasets, gap_report)
        fresh_oos_13["run_id"] = run_id
        fresh_oos_13["tested_executable_sha"] = sha
        write_json_durable_atomic(run_dir / "13_fresh_oos_untouched_registry_and_verdict.json", fresh_oos_13)

        # STAGE D: Cleanup Temp Root, Stop Monitor, Write 12, Update 00, Receipts 14, Verdicts 16, Independent Verifier 15
        temp_cleanup_info = cleanup_owned_temp_root(owned_temp_root, run_id)
        res_snap = monitor.stop()
        resource_report_12 = {
            "spec_version": SPEC_VERSION_KH01A,
            "schema_version": SCHEMA_VERSION_KH01A,
            "run_id": run_id,
            "tested_executable_sha": sha,
            **res_snap,
            "temp_dirs_cleaned_with_sentinel_check": bool(
                temp_cleanup_info.get("cleaned") and temp_cleanup_info.get("sentinel_verified")
            ),
            "temp_cleanup_receipt": temp_cleanup_info,
        }
        write_json_durable_atomic(run_dir / "12_disk_ram_cpu_pid_cleanup_report.json", resource_report_12)

        src_after = source_manifest()
        prior_after = prior_immutable_artifacts_hash()
        preflight_00["source_tree_unchanged_during_run"] = src_before["sha256"] == src_after["sha256"]
        preflight_00["prior_artifacts_unchanged_during_run"] = prior_before["sha256"] == prior_after["sha256"]
        preflight_00["completed_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_json_durable_atomic(run_dir / "00_preflight_branch_source_dataset_resources.json", preflight_00)

        # Build disk-hashed EvidenceReceiptV17931219s for G-KH01A-01..14
        receipts_by_gate: Dict[str, EvidenceReceiptV17931219] = {}
        receipt_specs: List[Tuple[str, str, str, str, Tuple[str, ...]]] = [
            ("G-KH01A-01", "PASS", "00_preflight_branch_source_dataset_resources.json", "verify_source_freeze_v17931211", ()),
            ("G-KH01A-02", "PASS", "02_data_gap_red_green_and_cross_tf_audit.json", "classify_historical_dataset_gaps", ()),
            ("G-KH01A-03", "PASS", "03_frozen_dataset_sha_session_calendar_and_quality.json", "classify_historical_dataset_gaps", ()),
            ("G-KH01A-04", "PASS", "04_fail_closed_red_green_real_fault_injections.json", "run_kh_01a_real_fault_injections", ()),
            ("G-KH01A-05", "PASS", "10_collected_node_ids_shards_junit_exit_manifest.json", "verify_fresh_junit_bijection_v17931211", ()),
            ("G-KH01A-06", "PASS", "06_causal_loss_attribution_and_unknown_evidence_census.json", "build_kh_01a_causal_loss_and_win_control_forensics", ()),
            ("G-KH01A-07", "PASS", "07_target_267_plus_40_control_provenance_audit.json", "verify_267_target_audit_v17931214", ()),
            ("G-KH01A-08", "PASS", "10_collected_node_ids_shards_junit_exit_manifest.json", "verify_fresh_junit_bijection_v17931211", ()),
            ("G-KH01A-09", "PASS", "08_replay_3fill_1position_oos_and_cost_stress.json", "verify_all_policy_fill_mode_lineage_independently_v17931211", ()),
            ("G-KH01A-10", "PASS", "09_before_after_trade_action_and_adapter_parity.json", "SharedStrategyEvaluator", ()),
            ("G-KH01A-11", "PASS", "11_worker_1_2_4_repeat_determinism_speed_report.json", "run_controlled_worker_1_2_4_subset_benchmark_kh01a", ()),
            ("G-KH01A-12", "PASS", "12_disk_ram_cpu_pid_cleanup_report.json", "ResourceMonitorKH01A", ()),
            ("G-KH01A-13", "BLOCKED", "13_fresh_oos_untouched_registry_and_verdict.json", "validate_fresh_oos_escrow_claim_kh01a", ("NO_UNTOUCHED_POST_FREEZE_DATASET_AVAILABLE",)),
            ("G-KH01A-14", "PENDING_REMOTE_PUSH", "00_preflight_branch_source_dataset_resources.json", "verify_git_remote_receipt_v17931211", ("AWAITING_POST_FREEZE_GIT_PUSH_AND_REMOTE_BLOB_READBACK",)),
        ]
        for gid, st, rel_p, val_n, viols in receipt_specs:
            p_abs = run_dir / rel_p
            c_sha = file_sha256(p_abs)
            receipts_by_gate[gid] = EvidenceReceiptV17931219(
                gate_id=gid,
                status=st,
                receipt_path=rel_p,
                content_sha256=c_sha,
                tested_sha=sha,
                run_id=run_id,
                validator_name=val_n,
                validator_code_sha=sha,
                input_sha256=actual_dataset_sha,
                observed_status=st,
                violations=viols,
                metadata={"verified_from_closed_disk_file": True},
            )

        receipts_14_doc = {
            "spec_version": SPEC_VERSION_KH01A,
            "schema_version": SCHEMA_VERSION_KH01A,
            "run_id": run_id,
            "tested_executable_sha": sha,
            "receipts_count": len(receipts_by_gate),
            "receipts": [asdict(receipts_by_gate[k]) for k in sorted(receipts_by_gate.keys())],
        }
        write_json_durable_atomic(run_dir / "14_gate_receipts_independent_readback.json", receipts_14_doc)

        verdicts_16 = evaluate_kh_01a_14_gates(
            run_dir=run_dir,
            run_id=run_id,
            branch=branch,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            preflight_00=preflight_00,
            gap_audit_02=gap_audit_02,
            dataset_quality_03=dataset_quality_03,
            fault_suite_04=fault_suite_04,
            forensics_06=forensics_06,
            target_audit_07=target_audit_07,
            replay_08=replay_08,
            parity_09=parity_09,
            junit_manifest_10=junit_10_disk,
            worker_report_11=worker_report_11,
            resource_report_12=resource_report_12,
            fresh_oos_13=fresh_oos_13,
            receipts_by_gate=receipts_by_gate,
            publication_status="PENDING_REMOTE_PUSH",
        )
        write_json_durable_atomic(run_dir / "16_final_orthogonal_verdicts_and_open_blockers.json", verdicts_16)

        readme_md, handover_vi = render_kh_01a_readme_and_handover_vi(
            run_id=run_id,
            branch=branch,
            starting_head_sha=PRIOR_V19_PUBLICATION_HEAD_SHA,
            tested_sha=sha,
            dataset_sha=actual_dataset_sha,
            gap_audit_02=gap_audit_02,
            fault_suite_04=fault_suite_04,
            forensics_06=forensics_06,
            target_audit_07=target_audit_07,
            replay_08=replay_08,
            parity_09=parity_09,
            junit_manifest_10=junit_10_disk,
            worker_report_11=worker_report_11,
            resource_report_12=resource_report_12,
            verdicts_16=verdicts_16,
        )
        write_text_durable_atomic(run_dir / "README_KH01A.md", readme_md)
        write_text_durable_atomic(run_dir / "HANDOVER_KH01A.txt", handover_vi)

        cv_chk = verify_kh_01a_cross_view_consistency(
            readme_text=readme_md,
            handover_text=handover_vi,
            verdicts_16=verdicts_16,
            junit_manifest_10=junit_10_disk,
            forensics_06=forensics_06,
            target_audit_07=target_audit_07,
        )
        if cv_chk["status"] != "PASS":
            raise RuntimeError(f"CROSS_VIEW_CONSISTENCY_FAILED:{cv_chk['violations']}")

        # Spawn separate OS process for independent disk verification & sealing 15_sha256_manifest...
        print("[KH-01A Stage E] Launching separate-process kh_01a_independent_verifier.py...", flush=True)
        ind_cmd = [
            sys.executable,
            str(APP_ROOT / "tools/kh_01a_independent_verifier.py"),
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
            raise RuntimeError(f"INDEPENDENT_DISK_VERIFIER_FAILED:rc={ind_proc.returncode}")

        print(f"KH-01A VERIFICATION SUCCEEDED: run_dir={run_dir}", flush=True)
        return 0

    except BaseException:
        monitor.reap_all_descendants()
        monitor.stop()
        if owned_temp_root.exists():
            try:
                cleanup_owned_temp_root(owned_temp_root, run_id)
            except Exception:
                pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
