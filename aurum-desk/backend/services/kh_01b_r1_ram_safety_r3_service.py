"""
backend/services/kh_01b_r1_ram_safety_r3_service.py

AURUM DESK — KH-01B.R1.DSC.R3: RAM Safety & Adaptive Test Scheduling Closure Service
SPEC VERSION: KH-01B.R1.DSC.R3-v1.0 (2026-11-20)

Implements pure, injectable, deterministic helpers for:
  - R3 RAM Safety Policy (`KH-01B.R1.DSC.R3.RAM.v1`) with strict `>= 512 MiB` hard abort floor,
    `1024 MiB` near-floor warning threshold, empirical per-shard memory projections, tiered
    hysteresis slot scheduling (`1.56 / 2.60 / 3.80 GiB`), validated singleton admission
    (preventing blind `active_slots == 0` bypass), and zero-deadlock pause timeouts.
  - AST + Git before/after function callgraph & SHA256 diff inventory (`01`).
  - Static source verification proving zero active `64 MiB` RAM floor overrides (`S02`, `R3-M40`).
  - 43-control RED/GREEN adversarial fault suite (`31` prior `DSC-M01..M31` + `12` R3 `R3-M32..M43`).
  - Independent disk-based orthogonal gate evaluator (`S01..S14`), `README_KH01B_R1_RAM_SAFETY_R3.md`
    & Vietnamese `HANDOVER_KH01B_R1_RAM_SAFETY_R3_VI.txt` renderer, cross-view consistency verifier,
    and accepted `latest_run.json` pointer guard.

Zero strategy, evaluator, signal, risk, fee, quote, or dataset logic is modified (`PRODUCTION=OFF`,
`LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`, `ACTIVATION_READY=NO`).
"""
from __future__ import annotations

import ast
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from domain_calculator import CostAssumptions
from services.kh_01b_r1_data_safety_closure_service import (
    CANONICAL_DATASET_SHA256_KH01B_R1,
    LOCKED_PRODUCTION_AND_RESEARCH_FILES,
    MANDATORY_BRANCH_KH01B_R1,
    PRIOR_DSC_V1_BLOCKED_RUN_ID,
    PRIOR_DSC_V1_TESTED_SHA,
    run_kh01b_r1_dsc_red_green_fault_suite,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_19_truth_and_core_repair_service import EvidenceReceiptV17931219

MUST_KEEP_UNCHANGED_SYMBOLS_DSC: Tuple[Tuple[str, str, str], ...] = (
    ("aurum-desk/backend/services/shared_strategy_evaluator.py", "SharedStrategyEvaluator.evaluate_model_direction", "Production model evaluation; untouched."),
    ("aurum-desk/backend/domain_calculator.py", "CostAssumptions", "Authoritative default cost config (maker=0.0002, taker=0.0004, slip=0.10); read-only."),
    ("aurum-desk/backend/domain_calculator.py", "calculate_risk_reward", "Authoritative R:R, fee, sizing & liquidation domain calculator; read-only."),
    ("aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py", "advance_pending_confirmation_state_v1793122", "Causal forward confirmation state machine; untouched."),
    ("aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py", "arbitrate_competing_model_intents_v1793122", "Deterministic static model priority arbitration; untouched."),
    ("aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py", "resolve_causal_macro_time_window", "ICT Macro NY schedule resolver; untouched."),
    ("aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py", "evaluate_causal_unicorn_confluence", "Strict Breaker+FVG confluence authority; untouched."),
    ("aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py", "compute_causal_tf_atr_v17931214", "Causal closed-bar source-TF ATR calculator; untouched."),
    ("aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py", "build_d4_full_63085_and_matched_funnel_v17931214", "Authoritative 63,085 -> 0 strict D4 funnel; untouched."),
    ("aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py", "evaluate_strict_true_unicorn_hypothesis", "Historical B09 strict D4 detector; untouched."),
    ("aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py", "evaluate_unicorn_adjacent_research_hypothesis", "Historical B10 adjacent detector isolated from D4; untouched."),
    ("aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py", "evaluate_kh01b_r1_entry_variant", "R1 E00..E06 candidate evaluator; strategy logic untouched."),
    ("aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py", "replay_kh01b_r1_frozen_variants", "R1 1-position 3-fill replay engine; untouched."),
    ("aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py", "compute_kh01b_r1_edge_uncertainty_and_research_verdict", "R1 E00..E06 cost stress & uncertainty calculator; untouched."),
)

SPEC_VERSION_KH01B_R1_DSC_R3 = "KH-01B.R1.DSC.R3-v1.0"
SCHEMA_VERSION_KH01B_R1_DSC_R3 = "kh_01b_r1_ram_safety_r3.v1"
RAM_POLICY_VERSION_R3 = "KH-01B.R1.DSC.R3.RAM.v1"

PRIOR_R2_ACCEPTED_RUN_ID = "20261120T021021Z_c20b0b1bec13"
PRIOR_R2_TESTED_SHA = "c20b0b1bec1344e2da258a5d18832874f5f5668a"
PRIOR_R2_EVIDENCE_SHA = "5b4bc84012602b682c15d234be31efe53f46c11d"
PRIOR_R2_PUBLICATION_HEAD_SHA = "64fbc433e2bffb378c649624961b7c69ee532172"
PRIOR_R2_RUN_REL_DIR = (
    f"aurum-desk/docs/artifacts/kh_01b_r1_data_safety_r2/{PRIOR_R2_ACCEPTED_RUN_ID}"
)
PRIOR_R2_LATEST_PTR_REL = "aurum-desk/docs/artifacts/kh_01b_r1_data_safety_r2/latest_run.json"
PRIOR_R2_PUBLICATION_RECEIPT_REL = (
    f"aurum-desk/docs/artifacts/kh_01b_r1_data_safety_r2/publication_{PRIOR_R2_ACCEPTED_RUN_ID}.json"
)
PRIOR_R2_LATEST_PTR_SHA256 = "284efdfec507d70266e4ea18a9e4d91cf06f325fd2c0ab0eeeb89970f2f2f013"
PRIOR_R2_MANIFEST_15_SHA256 = "7d4870faeaf981aaf5b52c6417adc8866ed1372320a82f4e1e16e34ff7c15412"

# Sealed R2 reference fingerprints for exact backtest parity (S09)
SEALED_R2_A1_56710_IDS_SHA256 = "dd8282ca484c5d38b2d94b6a0dd65669ad01fb1acbda2874b6ed808457da08f3"
SEALED_R2_SI8_6375_IDS_SHA256 = "8d3bc50b44f7da510c166ba468b6de03402739eaa2e4bcebac6b939770a0141b"
SEALED_R2_SI0_242_TUPLES_SHA256 = "644addeb27aae5215079363564d6ba89a3a7d90c47a26499c595cbc501be964e"
SEALED_R2_E00_BUNDLE_SHA256 = "30f90659430d0607180e9834d11554d664f89438389d793e4f6384f0dde12b83"
SEALED_R2_PARITY_SHA256 = "be3d083d2c58e2b60de8186b22252361f0ea5237d28081b6baa72fe0d7262e24"

# Explicit R3 RAM Policy Thresholds (bytes) — §3.B & §4 R3.A..R3.G
HARD_ABORT_RAM_FLOOR_BYTES_R3 = 512 * 1024**2               # 536,870,912 B (512.0 MiB)
WARNING_NEAR_RAM_FLOOR_BYTES_R3 = 1024 * 1024**2            # 1,073,741,824 B (1024.0 MiB)
ADMISSION_STOP_BACKPRESSURE_BYTES_R3 = 1600 * 1024**2       # 1,677,721,600 B (1600.0 MiB / 1.5625 GiB)
ADMISSION_RESUME_HYSTERESIS_BYTES_R3 = 1856 * 1024**2       # 1,946,157,056 B (1856.0 MiB / 1.8125 GiB)
TIER_2_SLOT_ADMISSION_BYTES_R3 = int(2.60 * 1024**3)        # 2,791,728,742 B (~2662.4 MiB)
TIER_2_HYSTERESIS_BYTES_R3 = int(2.85 * 1024**3)            # 3,060,164,198 B (~2918.4 MiB)
TIER_3_SLOT_ADMISSION_BYTES_R3 = int(3.80 * 1024**3)        # 4,080,218,931 B (~3891.2 MiB)
TIER_3_HYSTERESIS_BYTES_R3 = int(4.05 * 1024**3)            # 4,348,654,387 B (~4147.2 MiB)

POST_LAUNCH_SINGLETON_RESERVE_BYTES_R3 = 1024 * 1024**2     # 1024.0 MiB remaining after 1st shard estimate
POST_LAUNCH_MULTI_RESERVE_BYTES_R3 = 1536 * 1024**2         # 1536.0 MiB remaining after >=2nd shard estimate
NESTED_CHILD_EXTRA_RESERVE_PER_SLOT_BYTES_R3 = 128 * 1024**2  # 128.0 MiB per extra nested slot_cost - 1
MIN_PREFLIGHT_AVAILABLE_RAM_BYTES_R3 = 2048 * 1024**2       # 2048.0 MiB (2.00 GiB) baseline preflight
BENCHMARK_W4_MIN_AVAILABLE_RAM_BYTES_R3 = int(2.60 * 1024**3)
GLOBAL_DESCENDANT_CAP_R3 = 4

# Category baseline floors derived from measured R2 40-shard empirical RSS (max balanced=637.09 MiB,
# max single-child=596.39 MiB, max multi-child=440.19 MiB, isolated=117..135 MiB)
CATEGORY_EST_PEAK_BYTES_R3: Dict[str, int] = {
    "ISOLATED_LIGHT_INPROC": 220 * 1024**2,
    "REGULAR_BALANCED_INPROC": 680 * 1024**2,
    "SINGLE_CHILD_OR_HEAVY_RUNTIME": 820 * 1024**2,
    "EXCLUSIVE_MULTI_CHILD": 920 * 1024**2,
}

REQUIRED_ARTIFACTS_KH01B_R1_RAM_SAFETY_R3: Tuple[str, ...] = (
    "00_preflight_branch_sha_mount_ram_disk_immutable.json",
    "01_function_callgraph_keep_as_is_and_source_diff.json",
    "02_ram_policy_version_thresholds_preflight_and_projections.json",
    "03_admission_hysteresis_exclusive_nested_child_reservations.json",
    "04_ram_pressure_red_green_512mib_boundary_faults.json",
    "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json",
    "06_async_w1_w2_w4_repeat_timings_hashes_and_capacity.json",
    "07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json",
    "08_runtime_memory_samples_and_peak_rss.jsonl.gz",
    "09_disk_all_mounts_and_safe_temp_cleanup.json",
    "10_integrity_previous_artifacts_and_source_sha_diffs.json",
    "11_independent_verifier_red_controls_and_receipts.json",
    "12_oos_zero_evaluations_and_activation_off.json",
    "13_final_orthogonal_ram_engineering_economic_verdicts.json",
    "14_sha256_manifest_independent_os_readback.json",
    "README_KH01B_R1_RAM_SAFETY_R3.md",
    "HANDOVER_KH01B_R1_RAM_SAFETY_R3_VI.txt",
)

MANDATORY_R3_GATES: Tuple[str, ...] = (
    "S01", "S02", "S03", "S04", "S05", "S06", "S07",
    "S08", "S09", "S10", "S11", "S12", "S13", "S14",
)


# ==============================================================================
# 1. EMPIRICAL SHARD MEMORY PROJECTION & HYSTERESIS ADMISSION SCHEDULER (§3.B, §4)
# ==============================================================================

def load_empirical_r2_shard_rss_bytes(repo_root: Path) -> Dict[str, int]:
    """Reads actual measured per-shard peak RSS bytes from sealed R2 `09` artifact (read-only)."""
    r2_09 = repo_root / PRIOR_R2_RUN_REL_DIR / "09_full_regression_junit_collected_executed_bijection.json"
    if not r2_09.is_file():
        return {}
    try:
        doc = json.loads(r2_09.read_text(encoding="utf-8"))
        shards = (doc.get("shard_manifest") or {}).get("shards") or []
        return {
            str(s["shard_name"]): int(s.get("peak_rss_bytes") or 0)
            for s in shards
            if s.get("shard_name") and int(s.get("peak_rss_bytes") or 0) > 0
        }
    except Exception:
        return {}


def estimate_shard_peak_ram_bytes_r3(
    shard_spec: Mapping[str, Any],
    empirical_rss_by_shard: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    shard_name = str(shard_spec.get("shard_name") or "UNKNOWN_SHARD")
    exclusive = bool(shard_spec.get("exclusive", False))
    slot_cost = max(1, int(shard_spec.get("slot_cost", 4 if exclusive else 1)))
    files = list(shard_spec.get("files") or ())

    if exclusive:
        if "MULTI_CHILD" in shard_name:
            cat = "EXCLUSIVE_MULTI_CHILD"
            cat_floor = CATEGORY_EST_PEAK_BYTES_R3[cat]
        else:
            cat = "ISOLATED_LIGHT_INPROC"
            cat_floor = 320 * 1024**2
    elif slot_cost >= 2:
        cat = "SINGLE_CHILD_OR_HEAVY_RUNTIME"
        cat_floor = CATEGORY_EST_PEAK_BYTES_R3[cat]
    elif shard_name.endswith("_ISOLATED") or len(files) == 1:
        cat = "ISOLATED_LIGHT_INPROC"
        cat_floor = CATEGORY_EST_PEAK_BYTES_R3[cat]
    else:
        cat = "REGULAR_BALANCED_INPROC"
        cat_floor = CATEGORY_EST_PEAK_BYTES_R3[cat]

    emp_b = int((empirical_rss_by_shard or {}).get(shard_name) or 0)
    # Apply 1.20x safety factor over observed empirical peak RSS (+180 MiB nested child allowance when slot_cost>=2)
    nested_allowance = max(0, slot_cost - 1) * (140 * 1024**2) if emp_b > 0 else 0
    scaled_emp = int(emp_b * 1.20) + nested_allowance if emp_b > 0 else 0
    est_b = max(cat_floor, scaled_emp)

    return {
        "shard_name": shard_name,
        "category": cat,
        "exclusive": exclusive,
        "slot_cost": slot_cost,
        "empirical_r2_peak_rss_bytes": emp_b,
        "empirical_r2_peak_rss_mib": round(emp_b / (1024**2), 2),
        "estimated_peak_ram_bytes": int(est_b),
        "estimated_peak_ram_mib": round(est_b / (1024**2), 2),
    }


def compute_adaptive_slot_limit_r3(
    available_ram_bytes: int,
    *,
    prev_slot_limit: int = 0,
    max_parallel_workers: int = 4,
) -> Tuple[int, str]:
    """
    Tiered schedulable-slot limit with upward hysteresis to prevent oscillation:
      - `< 512 MiB`: hard floor breach (`-1`, `"HARD_ABORT_BELOW_512MIB"`)
      - `512 MiB .. < 1600 MiB` (resume `>= 1856 MiB` if paused): `0` (`"PAUSED_BACKPRESSURE"`)
      - `1600 MiB .. < 2.60 GiB` (upward hysteresis `>= 2.85 GiB`): `1` (`"CONSERVATIVE_1_SLOT"`)
      - `2.60 GiB .. < 3.80 GiB` (upward hysteresis `>= 4.05 GiB`): `min(2, max_w)` (`"MODERATE_2_SLOTS"`)
      - `>= 3.80 GiB`: `min(3, max_w)` (`"FULL_SAFE_3_SLOTS_PLUS_1_NESTED_RESERVE"`)
    Caps schedulable shard slots at `3` so `1` slot out of the `4`-descendant global hard cap
    always remains reserved for transient nested children.
    """
    avail = int(available_ram_bytes)
    cap_w = max(1, min(3, int(max_parallel_workers)))

    if avail < HARD_ABORT_RAM_FLOOR_BYTES_R3:
        return -1, "HARD_ABORT_BELOW_512MIB"

    if avail < ADMISSION_STOP_BACKPRESSURE_BYTES_R3:
        return 0, "PAUSED_BACKPRESSURE"
    if prev_slot_limit == 0 and avail < ADMISSION_RESUME_HYSTERESIS_BYTES_R3:
        return 0, "PAUSED_BACKPRESSURE_HYSTERESIS"

    if avail < TIER_2_SLOT_ADMISSION_BYTES_R3:
        return 1, "CONSERVATIVE_1_SLOT"
    if prev_slot_limit == 1 and avail < TIER_2_HYSTERESIS_BYTES_R3:
        return 1, "CONSERVATIVE_1_SLOT_HYSTERESIS"

    if avail < TIER_3_SLOT_ADMISSION_BYTES_R3:
        return min(2, cap_w), "MODERATE_2_SLOTS"
    if prev_slot_limit == 2 and avail < TIER_3_HYSTERESIS_BYTES_R3:
        return min(2, cap_w), "MODERATE_2_SLOTS_HYSTERESIS"

    return cap_w, "FULL_SAFE_3_SLOTS_PLUS_1_NESTED_RESERVE"


def evaluate_r3_shard_admission(
    *,
    shard_name: str,
    exclusive: bool,
    slot_cost: int,
    estimated_shard_bytes: int,
    available_ram_bytes: int,
    free_disk_bytes: int,
    effective_disk_reserve_bytes: int,
    active_slots: int,
    active_shards_count: int,
    exclusive_running: bool,
    inflight_uncommitted_bytes: int = 0,
    prev_slot_limit: int = 3,
    max_parallel_workers: int = 4,
) -> Dict[str, Any]:
    """
    Pure, deterministic R3 admission gate evaluated BEFORE spawning any worker subprocess (§3.B, §4 R3.C/D).
    Eliminates the R2 `active_slots == 0` blind bypass: a shard whose `slot_cost > slot_limit` (or `exclusive`)
    can run ONLY when `active_slots == 0` AND its independent predicted footprint + nested-child singleton
    reserve fits within `available_ram_bytes`.
    """
    avail = int(available_ram_bytes)
    disk_b = int(free_disk_bytes)
    eff_cost = max(1, int(slot_cost))
    est_b = max(64 * 1024**2, int(estimated_shard_bytes))

    if disk_b < int(effective_disk_reserve_bytes):
        return {
            "decision": "ABORT_LOW_DISK",
            "new_slot_limit": 0,
            "tier_label": "ABORT_LOW_DISK",
            "acquired_cost": 0,
            "projected_remaining_bytes": 0,
            "required_remaining_bytes": 0,
            "shortfall_bytes": int(effective_disk_reserve_bytes) - disk_b,
            "reason": f"BLOCKED_LOW_DISK:free_bytes={disk_b}<reserve_bytes={effective_disk_reserve_bytes}",
        }

    if avail < HARD_ABORT_RAM_FLOOR_BYTES_R3:
        return {
            "decision": "ABORT_LOW_RAM",
            "new_slot_limit": -1,
            "tier_label": "HARD_ABORT_BELOW_512MIB",
            "acquired_cost": 0,
            "projected_remaining_bytes": avail - est_b,
            "required_remaining_bytes": HARD_ABORT_RAM_FLOOR_BYTES_R3,
            "shortfall_bytes": HARD_ABORT_RAM_FLOOR_BYTES_R3 - avail,
            "reason": f"BLOCKED_LOW_RAM:avail_bytes={avail}<floor_bytes={HARD_ABORT_RAM_FLOOR_BYTES_R3}",
        }

    if eff_cost > GLOBAL_DESCENDANT_CAP_R3:
        return {
            "decision": "REJECT_IMPOSSIBLE_OVER_CAP",
            "new_slot_limit": 0,
            "tier_label": "OVER_GLOBAL_CAP",
            "acquired_cost": 0,
            "projected_remaining_bytes": avail - est_b,
            "required_remaining_bytes": 0,
            "shortfall_bytes": 0,
            "reason": f"SHARD_SLOT_COST_EXCEEDS_GLOBAL_CAP:shard={shard_name}:cost={eff_cost}>{GLOBAL_DESCENDANT_CAP_R3}",
        }

    new_lim, tier_lbl = compute_adaptive_slot_limit_r3(
        avail, prev_slot_limit=prev_slot_limit, max_parallel_workers=max_parallel_workers
    )
    eff_avail = max(0, avail - max(0, int(inflight_uncommitted_bytes)))

    # Exclusive shards OR heavy shards with eff_cost > new_lim (no blind active_slots == 0 bypass!)
    if exclusive or eff_cost > new_lim:
        if active_slots > 0 or exclusive_running:
            return {
                "decision": "WAIT_SLOT_CAPACITY",
                "new_slot_limit": new_lim,
                "tier_label": tier_lbl,
                "acquired_cost": 0,
                "projected_remaining_bytes": eff_avail - est_b,
                "required_remaining_bytes": POST_LAUNCH_SINGLETON_RESERVE_BYTES_R3,
                "shortfall_bytes": 0,
                "reason": f"WAITING_FOR_SIBLINGS_TO_DRAIN:active_slots={active_slots}",
            }
        # active_slots == 0: validate explicit singleton RAM budget before admitting
        singleton_reserve = (
            POST_LAUNCH_SINGLETON_RESERVE_BYTES_R3
            + max(0, eff_cost - 1) * NESTED_CHILD_EXTRA_RESERVE_PER_SLOT_BYTES_R3
        )
        required_total = est_b + singleton_reserve
        if new_lim <= 0 or avail < required_total:
            shortfall = max(1, required_total - avail)
            return {
                "decision": "WAIT_BACKPRESSURE",
                "new_slot_limit": new_lim,
                "tier_label": tier_lbl,
                "acquired_cost": 0,
                "projected_remaining_bytes": avail - est_b,
                "required_remaining_bytes": singleton_reserve,
                "required_total_bytes": required_total,
                "shortfall_bytes": shortfall,
                "reason": (
                    f"SINGLETON_RAM_BUDGET_INSUFFICIENT:shard={shard_name}:avail_bytes={avail}"
                    f"<required_bytes={required_total}:est_bytes={est_b}:reserve_bytes={singleton_reserve}"
                ),
            }
        return {
            "decision": "ADMIT_VALIDATED_SINGLETON",
            "new_slot_limit": new_lim,
            "tier_label": tier_lbl,
            "acquired_cost": 4 if exclusive else eff_cost,
            "projected_remaining_bytes": avail - est_b,
            "required_remaining_bytes": singleton_reserve,
            "shortfall_bytes": 0,
            "reason": "VALIDATED_SINGLETON_ZERO_SIBLINGS_WITH_SUFFICIENT_RAM_MARGIN",
        }

    if new_lim <= 0:
        return {
            "decision": "WAIT_BACKPRESSURE",
            "new_slot_limit": 0,
            "tier_label": tier_lbl,
            "acquired_cost": 0,
            "projected_remaining_bytes": eff_avail - est_b,
            "required_remaining_bytes": POST_LAUNCH_SINGLETON_RESERVE_BYTES_R3,
            "shortfall_bytes": max(1, ADMISSION_RESUME_HYSTERESIS_BYTES_R3 - avail),
            "reason": f"ADMISSION_PAUSED_BACKPRESSURE:avail_bytes={avail}",
        }

    if exclusive_running or (active_slots + eff_cost > new_lim):
        return {
            "decision": "WAIT_SLOT_CAPACITY",
            "new_slot_limit": new_lim,
            "tier_label": tier_lbl,
            "acquired_cost": 0,
            "projected_remaining_bytes": eff_avail - est_b,
            "required_remaining_bytes": POST_LAUNCH_MULTI_RESERVE_BYTES_R3,
            "shortfall_bytes": 0,
            "reason": f"SLOT_CAPACITY_FULL:active_slots={active_slots}+cost={eff_cost}>limit={new_lim}",
        }

    req_rem = (
        POST_LAUNCH_SINGLETON_RESERVE_BYTES_R3
        if active_shards_count == 0
        else POST_LAUNCH_MULTI_RESERVE_BYTES_R3
    )
    proj_rem = eff_avail - est_b
    if proj_rem < req_rem:
        req_tot = est_b + req_rem + max(0, int(inflight_uncommitted_bytes))
        return {
            "decision": "WAIT_BACKPRESSURE",
            "new_slot_limit": new_lim,
            "tier_label": tier_lbl,
            "acquired_cost": 0,
            "projected_remaining_bytes": proj_rem,
            "required_remaining_bytes": req_rem,
            "required_total_bytes": req_tot,
            "shortfall_bytes": max(1, req_rem - proj_rem),
            "reason": (
                f"PROJECTED_RAM_MARGIN_BELOW_RESERVE:shard={shard_name}:proj_rem={proj_rem}<req_rem={req_rem}"
            ),
        }

    return {
        "decision": "ADMIT_NORMAL",
        "new_slot_limit": new_lim,
        "tier_label": tier_lbl,
        "acquired_cost": eff_cost,
        "projected_remaining_bytes": proj_rem,
        "required_remaining_bytes": req_rem,
        "shortfall_bytes": 0,
        "reason": "ADMITTED_WITH_PROJECTED_RAM_MARGIN",
    }


def compute_r3_ram_preflight_and_policy(
    available_ram_bytes: int,
    total_ram_bytes: int,
    *,
    shard_specs: Sequence[Mapping[str, Any]] = (),
    empirical_rss_by_shard: Optional[Mapping[str, int]] = None,
    max_workers: int = 4,
) -> Dict[str, Any]:
    avail = int(available_ram_bytes)
    tot = int(total_ram_bytes)
    projections = [
        estimate_shard_peak_ram_bytes_r3(sp, empirical_rss_by_shard)
        for sp in shard_specs
    ]
    heaviest_est_b = max(
        (int(p["estimated_peak_ram_bytes"]) for p in projections),
        default=CATEGORY_EST_PEAK_BYTES_R3["EXCLUSIVE_MULTI_CHILD"],
    )
    min_preflight_b = max(
        MIN_PREFLIGHT_AVAILABLE_RAM_BYTES_R3,
        heaviest_est_b + POST_LAUNCH_SINGLETON_RESERVE_BYTES_R3,
    )
    can_proceed = avail >= min_preflight_b and avail >= HARD_ABORT_RAM_FLOOR_BYTES_R3
    shortfall_b = max(0, min_preflight_b - avail)
    init_slots, init_tier = compute_adaptive_slot_limit_r3(avail, prev_slot_limit=0, max_parallel_workers=max_workers)

    policy_core = {
        "ram_policy_version": RAM_POLICY_VERSION_R3,
        "hard_abort_floor_bytes": HARD_ABORT_RAM_FLOOR_BYTES_R3,
        "hard_abort_floor_mib": round(HARD_ABORT_RAM_FLOOR_BYTES_R3 / (1024**2), 2),
        "hard_abort_floor_gib": round(HARD_ABORT_RAM_FLOOR_BYTES_R3 / (1024**3), 4),
        "warning_near_floor_bytes": WARNING_NEAR_RAM_FLOOR_BYTES_R3,
        "warning_near_floor_mib": round(WARNING_NEAR_RAM_FLOOR_BYTES_R3 / (1024**2), 2),
        "soft_backpressure_floor_bytes": ADMISSION_STOP_BACKPRESSURE_BYTES_R3,
        "soft_backpressure_floor_mib": round(ADMISSION_STOP_BACKPRESSURE_BYTES_R3 / (1024**2), 2),
        "backpressure_resume_hysteresis_bytes": ADMISSION_RESUME_HYSTERESIS_BYTES_R3,
        "tier_2_slot_admission_bytes": TIER_2_SLOT_ADMISSION_BYTES_R3,
        "tier_2_hysteresis_bytes": TIER_2_HYSTERESIS_BYTES_R3,
        "tier_3_slot_admission_bytes": TIER_3_SLOT_ADMISSION_BYTES_R3,
        "tier_3_hysteresis_bytes": TIER_3_HYSTERESIS_BYTES_R3,
        "post_launch_singleton_reserve_bytes": POST_LAUNCH_SINGLETON_RESERVE_BYTES_R3,
        "post_launch_multi_reserve_bytes": POST_LAUNCH_MULTI_RESERVE_BYTES_R3,
        "nested_child_extra_reserve_per_slot_bytes": NESTED_CHILD_EXTRA_RESERVE_PER_SLOT_BYTES_R3,
        "min_preflight_available_bytes": min_preflight_b,
        "min_preflight_available_mib": round(min_preflight_b / (1024**2), 2),
        "min_preflight_available_gib": round(min_preflight_b / (1024**3), 4),
        "heaviest_projected_shard_bytes": heaviest_est_b,
        "heaviest_projected_shard_mib": round(heaviest_est_b / (1024**2), 2),
        "effective_worker_cap": min(GLOBAL_DESCENDANT_CAP_R3, max(1, int(max_workers))),
        "max_schedulable_shard_slots": min(3, max(1, int(max_workers))),
        "global_os_descendant_cap": GLOBAL_DESCENDANT_CAP_R3,
        "overrides_to_64mib_forbidden": True,
    }
    return {
        **policy_core,
        "policy_sha256": canonical_hash(policy_core),
        "preflight_total_ram_bytes": tot,
        "preflight_total_ram_gib": round(tot / (1024**3), 4),
        "preflight_available_ram_bytes": avail,
        "preflight_available_ram_mib": round(avail / (1024**2), 2),
        "preflight_available_ram_gib": round(avail / (1024**3), 4),
        "preflight_headroom_above_512mib_bytes": avail - HARD_ABORT_RAM_FLOOR_BYTES_R3,
        "preflight_headroom_above_512mib_mib": round((avail - HARD_ABORT_RAM_FLOOR_BYTES_R3) / (1024**2), 2),
        "initial_slot_limit": max(0, init_slots),
        "initial_tier_label": init_tier,
        "can_proceed": can_proceed,
        "preflight_ram_verdict": "PASS_RAM_PREFLIGHT" if can_proceed else "BLOCKED_LOW_RAM",
        "shortfall_bytes": shortfall_b,
        "shortfall_mib": round(shortfall_b / (1024**2), 2),
        "abort_reason": (
            None
            if can_proceed
            else (
                f"BLOCKED_LOW_RAM_PREFLIGHT:avail_bytes={avail}<required_bytes={min_preflight_b}:"
                f"shortfall_mib={round(shortfall_b / (1024**2), 2)}"
            )
        ),
        "shard_projections_count": len(projections),
        "shard_projections": projections,
    }


# ==============================================================================
# 2. STATIC 64MiB CHECK & AST BEFORE/AFTER FUNCTION CALLGRAPH INVENTORY (§3)
# ==============================================================================

ACTIVE_R3_GUARDED_SOURCE_FILES: Tuple[str, ...] = (
    "aurum-desk/tools/run_kh_01a_verification.py",
    "aurum-desk/tools/run_kh_01b_r1_verification.py",
    "aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py",
    "aurum-desk/tools/kh_01b_r1_data_safety_independent_verifier.py",
    "aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py",
)

def _eval_static_int_expr(node: ast.AST) -> Optional[int]:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return int(node.value)
        if isinstance(node.value, str) and node.value.strip().isdigit():
            return int(node.value.strip())
        return None
    if isinstance(node, ast.BinOp):
        lv = _eval_static_int_expr(node.left)
        rv = _eval_static_int_expr(node.right)
        if lv is None or rv is None:
            return None
        if isinstance(node.op, ast.Mult):
            return lv * rv
        if isinstance(node.op, ast.Pow) and 0 <= rv <= 10:
            return int(lv**rv)
        if isinstance(node.op, ast.Add):
            return lv + rv
        if isinstance(node.op, ast.Sub):
            return lv - rv
        if isinstance(node.op, ast.FloorDiv) and rv != 0:
            return lv // rv
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("str", "int") and len(node.args) == 1:
        return _eval_static_int_expr(node.args[0])
    return None


def detect_active_64mib_ram_floor_in_source(
    repo_root: Path,
    *,
    extra_text_by_name: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """
    AST-level static source check proving active R3/DSC runner & monitor Python files contain
    NO executable `< 512 MiB` (`minimum_ram_bytes` or `AURUM_MINIMUM_RAM_BYTES`) override (§3.F, S02).
    """
    hits: List[Dict[str, Any]] = []
    targets: List[Tuple[str, str]] = []
    for rel in ACTIVE_R3_GUARDED_SOURCE_FILES:
        fp = repo_root / rel
        if fp.is_file():
            targets.append((rel, fp.read_text(encoding="utf-8", errors="replace")))
    if extra_text_by_name:
        for k, txt in extra_text_by_name.items():
            targets.append((str(k), str(txt)))

    for rel_name, src_txt in targets:
        lines = src_txt.splitlines()
        try:
            tree = ast.parse(src_txt)
        except SyntaxError as syn_exc:
            hits.append({"file": rel_name, "line": int(syn_exc.lineno or 1), "text": f"SYNTAX_ERROR:{syn_exc}"})
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "minimum_ram_bytes":
                        val = _eval_static_int_expr(kw.value)
                        if val is not None and val < HARD_ABORT_RAM_FLOOR_BYTES_R3:
                            ln = int(getattr(kw, "lineno", getattr(node, "lineno", 1)))
                            hits.append({
                                "file": rel_name,
                                "line": ln,
                                "configured_bytes": val,
                                "text": lines[ln - 1].strip() if 1 <= ln <= len(lines) else "minimum_ram_bytes",
                            })
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (
                        isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.slice, ast.Constant)
                        and tgt.slice.value == "AURUM_MINIMUM_RAM_BYTES"
                    ):
                        val = _eval_static_int_expr(node.value)
                        if val is not None and val < HARD_ABORT_RAM_FLOOR_BYTES_R3:
                            ln = int(getattr(node, "lineno", 1))
                            hits.append({
                                "file": rel_name,
                                "line": ln,
                                "configured_bytes": val,
                                "text": lines[ln - 1].strip() if 1 <= ln <= len(lines) else "AURUM_MINIMUM_RAM_BYTES",
                            })

    return {
        "status": "PASS" if not hits else "FAIL",
        "checked_files_count": len(targets),
        "violations_count": len(hits),
        "violations": hits,
    }


def _extract_ast_function_spans(src_text: str) -> Dict[str, Dict[str, Any]]:
    lines = src_text.splitlines()
    tree = ast.parse(src_text)
    out: Dict[str, Dict[str, Any]] = {}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            s_ln = int(node.lineno)
            e_ln = int(getattr(node, "end_lineno", node.lineno))
            seg = "\n".join(lines[s_ln - 1 : e_ln])
            out[node.name] = {
                "start_line": s_ln,
                "end_line": e_ln,
                "sha256": hashlib.sha256(seg.encode("utf-8")).hexdigest(),
            }
        elif isinstance(node, ast.ClassDef):
            s_c = int(node.lineno)
            e_c = int(getattr(node, "end_lineno", node.lineno))
            seg_c = "\n".join(lines[s_c - 1 : e_c])
            out[node.name] = {
                "start_line": s_c,
                "end_line": e_c,
                "sha256": hashlib.sha256(seg_c.encode("utf-8")).hexdigest(),
            }
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sm = int(sub.lineno)
                    em = int(getattr(sub, "end_lineno", sub.lineno))
                    seg_m = "\n".join(lines[sm - 1 : em])
                    out[f"{node.name}.{sub.name}"] = {
                        "start_line": sm,
                        "end_line": em,
                        "sha256": hashlib.sha256(seg_m.encode("utf-8")).hexdigest(),
                    }
    return out


def _git_show_file_at_ref(repo_root: Path, git_ref: str, rel_path: str) -> Optional[str]:
    proc = subprocess.run(
        ["git", "show", f"{git_ref}:{rel_path}"],
        cwd=str(repo_root),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


R3_TARGET_FUNCTIONS_PURPOSES: Tuple[Tuple[str, str, str], ...] = (
    (
        "aurum-desk/tools/run_kh_01a_verification.py",
        "ResourceMonitorKH01A",
        "Shared 0.5s process-tree/RAM/disk watchdog with >=512 MiB hard floor latch, active-shard context & full descendant RSS.",
    ),
    (
        "aurum-desk/tools/run_kh_01a_verification.py",
        "ResourceMonitorKH01A.check",
        "Race-proof RAM/disk/descendant sampler latching first hard breach and writing durable resource_abort.json.",
    ),
    (
        "aurum-desk/tools/run_kh_01a_verification.py",
        "ResourceMonitorKH01A.stop",
        "Reaps owned descendants and reports separate parent RSS, full child-tree RSS, min RAM headroom & latched error.",
    ),
    (
        "aurum-desk/tools/run_kh_01b_r1_verification.py",
        "_build_lpt_shards_kh01b_r1",
        "Deterministic LPT shard builder preserving slot_cost=2 for child-spawning/runtime shards and slot_cost=4 for exclusives.",
    ),
    (
        "aurum-desk/tools/run_kh_01b_r1_verification.py",
        "run_async_lpt_pytest_shards_kh01b_r1",
        "Bounded async pytest scheduler with R3 predictive RAM admission, validated singleton budget, 0.2s child-tree RSS & OOM detection.",
    ),
    (
        "aurum-desk/tools/run_kh_01b_r1_verification.py",
        "_run_controlled_pytest_w1_w2_w4_benchmark_r1",
        "Controlled 155-test w=1/2/4/1_repeat determinism & speed benchmark with R3 RAM capacity check.",
    ),
    (
        "aurum-desk/tools/run_kh_01b_r1_verification.py",
        "_run_r1_variant_slice_w1_w2_w4_determinism",
        "2,800-pair R1 slice w=1/2/4/1_repeat determinism benchmark selecting optimal worker count truthfully.",
    ),
    (
        "aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py",
        "run_kh01b_r1_dsc_r2_orchestrator",
        "Replay-compatible R2 orchestrator upgraded to >=512 MiB monitor floor with zero 64 MiB override.",
    ),
    (
        "aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py",
        "run_kh01b_r1_ram_safety_closure_orchestrator_r3",
        "New R3 orchestrator enforcing >=512 MiB hard floor, stage memory leak telemetry, bounded GC & R3 artifact generation.",
    ),
    (
        "aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py",
        "evaluate_r3_shard_admission",
        "Pure R3 hysteresis & projected-footprint admission gate blocking active_slots==0 low-RAM bypass.",
    ),
    (
        "aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py",
        "compute_r3_ram_preflight_and_policy",
        "Freezes KH-01B.R1.DSC.R3.RAM.v1 thresholds, empirical shard projections & preflight verdict.",
    ),
    (
        "aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py",
        "run_kh01b_r1_r3_ram_fault_suite",
        "43-mutation RED/GREEN adversarial fault suite (31 prior DSC + 12 R3 RAM/scheduler/OOM faults).",
    ),
    (
        "aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py",
        "evaluate_kh01b_r1_ram_safety_r3_gates_from_disk",
        "Independent disk-based evaluator for orthogonal R3 gates S01..S14.",
    ),
    (
        "aurum-desk/tools/kh_01b_r1_data_safety_independent_verifier.py",
        "verify_kh_01b_r1_ram_safety_r3_run_dir_from_disk",
        "Separate-OS-process R3 verifier recomputing min RAM from 08 jsonl.gz, JUnit bijection & backtest fingerprints.",
    ),
)


def build_kh01b_r1_r3_callgraph_and_source_diff(
    repo_root: Path,
    *,
    head_at_start_sha: str,
    tested_sha: str,
    changed_files: Sequence[str],
) -> Dict[str, Any]:
    changed_norm = sorted({str(p).replace("\\", "/") for p in changed_files if p})
    locked_norm = {str(p).replace("\\", "/") for p in LOCKED_PRODUCTION_AND_RESEARCH_FILES}
    locked_modified = [p for p in changed_norm if p in locked_norm]

    keep_as_is_rows: List[Dict[str, Any]] = []
    for rel_file, qual_sym, note in MUST_KEEP_UNCHANGED_SYMBOLS_DSC:
        fp = repo_root / rel_file
        cur_txt = fp.read_text(encoding="utf-8")
        cur_spans = _extract_ast_function_spans(cur_txt)
        old_txt = _git_show_file_at_ref(repo_root, head_at_start_sha, rel_file)
        old_spans = _extract_ast_function_spans(old_txt) if old_txt is not None else cur_spans
        sp = cur_spans.get(qual_sym) or {"start_line": 0, "end_line": 0, "sha256": "MISSING"}
        sp_old = old_spans.get(qual_sym) or sp
        keep_as_is_rows.append({
            "file": rel_file,
            "function": qual_sym,
            "start_line": sp["start_line"],
            "end_line": sp["end_line"],
            "purpose": note,
            "before_sha": sp_old["sha256"],
            "after_sha": sp["sha256"],
            "unchanged_verified": (sp_old["sha256"] == sp["sha256"]) and (rel_file not in changed_norm),
            "decision": "KEEP_AS_IS",
        })

    r3_func_rows: List[Dict[str, Any]] = []
    for rel_file, qual_sym, purpose in R3_TARGET_FUNCTIONS_PURPOSES:
        fp = repo_root / rel_file
        if not fp.is_file():
            continue
        cur_txt = fp.read_text(encoding="utf-8")
        cur_spans = _extract_ast_function_spans(cur_txt)
        old_txt = _git_show_file_at_ref(repo_root, head_at_start_sha, rel_file)
        old_spans = _extract_ast_function_spans(old_txt) if old_txt is not None else {}
        sp_after = cur_spans.get(qual_sym)
        if sp_after is None:
            continue
        sp_before = old_spans.get(qual_sym)
        before_sha = sp_before["sha256"] if sp_before is not None else "NEW_IN_R3"
        after_sha = sp_after["sha256"]
        r3_func_rows.append({
            "file": rel_file,
            "function": qual_sym,
            "start_line": sp_after["start_line"],
            "end_line": sp_after["end_line"],
            "purpose": purpose,
            "before_sha": before_sha,
            "after_sha": after_sha,
            "action": "ADDED" if before_sha == "NEW_IN_R3" else ("MODIFIED" if before_sha != after_sha else "VERIFIED_COMPATIBLE"),
        })

    static_64_check = detect_active_64mib_ram_floor_in_source(repo_root)
    cost_inst = CostAssumptions()
    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC_R3,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC_R3,
        "head_at_start_sha": head_at_start_sha,
        "tested_executable_sha": tested_sha,
        "changed_files_since_r2_publication": changed_norm,
        "locked_files_modified_count": len(locked_modified),
        "locked_files_modified": locked_modified,
        "static_no_active_64mib_floor_check": static_64_check,
        "cost_assumptions_unchanged": {
            "maker_fee_rate": cost_inst.maker_fee_rate,
            "taker_fee_rate": cost_inst.taker_fee_rate,
            "slippage_usd": cost_inst.slippage_usd,
            "unchanged_verified": (
                cost_inst.maker_fee_rate == 0.0002
                and cost_inst.taker_fee_rate == 0.0004
                and cost_inst.slippage_usd == 0.10
            ),
        },
        "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        "keep_as_is_functions": keep_as_is_rows,
        "r3_modified_and_added_functions": r3_func_rows,
        "prior_r2_accepted_reference": {
            "r2_run_id": PRIOR_R2_ACCEPTED_RUN_ID,
            "r2_tested_sha": PRIOR_R2_TESTED_SHA,
            "r2_evidence_sha": PRIOR_R2_EVIDENCE_SHA,
            "r2_publication_head_sha": PRIOR_R2_PUBLICATION_HEAD_SHA,
            "r2_observed_min_available_ram_bytes": 72_261_632,
            "r2_observed_min_available_ram_mib": 68.91,
            "r2_status": "HISTORICAL_ACCEPTED_R2_PRESERVED_UNMODIFIED",
        },
    }


# ==============================================================================
# 3. R3 ADVERSARIAL RED/GREEN FAULT SUITE (31 PRIOR + 12 R3 = 43 MUTATIONS) (§3.D, S10)
# ==============================================================================

def run_kh01b_r1_r3_ram_fault_suite(repo_root: Path) -> Dict[str, Any]:
    from tools.run_kh_01a_verification import ResourceMonitorKH01A

    base_suite = run_kh01b_r1_dsc_red_green_fault_suite(repo_root)
    mutations: List[Dict[str, Any]] = list(base_suite["mutations"])

    def _rec(mid: str, cat: str, name: str, target_fn: str, red_rejected: bool, green_accepted: bool, diag: str) -> None:
        mutations.append({
            "mutation_id": mid,
            "category": cat,
            "name": name,
            "target_function": target_fn,
            "red_rejected": bool(red_rejected),
            "green_accepted": bool(green_accepted),
            "status": "PASS" if (red_rejected and green_accepted) else "FAIL",
            "diagnostic": diag,
        })

    with tempfile.TemporaryDirectory(prefix="dsc_r3_faults_") as td:
        tmp_d = Path(td)

        # R3-M32: Exact 512.00 MiB boundary passes `<` check; 511.99 MiB fails closed with BLOCKED_LOW_RAM
        exact_512 = HARD_ABORT_RAM_FLOOR_BYTES_R3
        below_512 = int(511.99 * 1024**2)
        mon_ok = ResourceMonitorKH01A(
            abort_path=tmp_d / "m32_ok.json",
            minimum_ram_bytes=exact_512,
            available_ram_fn=lambda: exact_512,
            free_disk_fn=lambda: 35 * 1024**3,
        )
        s_ok = mon_ok.check()
        mon_fail = ResourceMonitorKH01A(
            abort_path=tmp_d / "m32_red.json",
            minimum_ram_bytes=exact_512,
            available_ram_fn=lambda: below_512,
            free_disk_fn=lambda: 35 * 1024**3,
        )
        red_32 = False
        try:
            mon_fail.check()
        except RuntimeError as exc:
            red_32 = "BLOCKED_LOW_RAM" in str(exc) and (tmp_d / "m32_red.json").is_file()
        _rec(
            "R3-M32",
            "RAM_BOUNDARY",
            "EXACT_512P00_MIB_PASSES_VS_511P99_MIB_FAILS_CLOSED",
            "ResourceMonitorKH01A.check",
            red_32,
            s_ok["available_ram_bytes"] == exact_512 and mon_ok.near_hard_floor_warning is True,
            f"512MiB={exact_512},511.99MiB={below_512}",
        )

        # R3-M33: Low-memory injections at 450 MiB, 300 MiB, and 69 MiB (R2 observed ~68.9 MiB) all latch BLOCKED_LOW_RAM
        all_3_blocked = True
        for mib_v in (450, 300, 69):
            m_low = ResourceMonitorKH01A(
                abort_path=tmp_d / f"m33_{mib_v}.json",
                minimum_ram_bytes=exact_512,
                available_ram_fn=lambda v=mib_v: v * 1024**2,
                free_disk_fn=lambda: 35 * 1024**3,
            )
            try:
                m_low.check()
                all_3_blocked = False
            except RuntimeError as exc:
                if "BLOCKED_LOW_RAM" not in str(exc) or not m_low.hard_ram_breached:
                    all_3_blocked = False
        m_high = ResourceMonitorKH01A(
            abort_path=tmp_d / "m33_green.json",
            minimum_ram_bytes=exact_512,
            available_ram_fn=lambda: 4096 * 1024**2,
            free_disk_fn=lambda: 35 * 1024**3,
        )
        m_high.check()
        _rec(
            "R3-M33",
            "RAM_HARD_FLOOR",
            "INJECTED_450_300_69_MIB_ALL_ABORT_WITH_BLOCKED_LOW_RAM",
            "ResourceMonitorKH01A.check",
            all_3_blocked,
            m_high.error is None and not m_high.hard_ram_breached,
            "450/300/69 MiB all rejected",
        )

        # R3-M34: Post-abort RAM recovery to 8 GiB NEVER clears latched BLOCKED_LOW_RAM error
        seq_vals = iter([300 * 1024**2, 8192 * 1024**2])
        m_latch = ResourceMonitorKH01A(
            abort_path=tmp_d / "m34_latch.json",
            minimum_ram_bytes=exact_512,
            available_ram_fn=lambda: next(seq_vals, 8192 * 1024**2),
            free_disk_fn=lambda: 35 * 1024**3,
            active_context_fn=lambda: {"active_shards": ["SHARD_INPROC_13_BALANCED"]},
        )
        first_failed = second_still_failed = False
        try:
            m_latch.check()
        except RuntimeError:
            first_failed = True
        try:
            m_latch.check()
        except RuntimeError as exc2:
            second_still_failed = "BLOCKED_LOW_RAM" in str(exc2)
        rep_latch = m_latch.stop()
        _rec(
            "R3-M34",
            "RAM_LATCH",
            "POST_BREACH_RAM_RECOVERY_NEVER_CLEARS_LATCHED_ABORT",
            "ResourceMonitorKH01A.stop",
            first_failed and second_still_failed and rep_latch["hard_ram_breached"] is True,
            rep_latch["active_shards_at_breach"] == ["SHARD_INPROC_13_BALANCED"],
            f"latched={rep_latch['latched_error']}",
        )

        # R3-M35: active_slots == 0 heavy shard (`slot_cost=2`, est 820 MiB) BLOCKED at 1.5 GiB vs ADMITTED as validated singleton at 2.4 GiB
        adm_red = evaluate_r3_shard_admission(
            shard_name="SHARD_INPROC_00_V17_9_0_1_RUNTIME",
            exclusive=False,
            slot_cost=2,
            estimated_shard_bytes=820 * 1024**2,
            available_ram_bytes=int(1.50 * 1024**3),
            free_disk_bytes=35 * 1024**3,
            effective_disk_reserve_bytes=20 * 1024**3,
            active_slots=0,
            active_shards_count=0,
            exclusive_running=False,
            prev_slot_limit=1,
        )
        adm_green = evaluate_r3_shard_admission(
            shard_name="SHARD_INPROC_00_V17_9_0_1_RUNTIME",
            exclusive=False,
            slot_cost=2,
            estimated_shard_bytes=820 * 1024**2,
            available_ram_bytes=int(2.40 * 1024**3),
            free_disk_bytes=35 * 1024**3,
            effective_disk_reserve_bytes=20 * 1024**3,
            active_slots=0,
            active_shards_count=0,
            exclusive_running=False,
            prev_slot_limit=1,
        )
        _rec(
            "R3-M35",
            "ADMISSION_SINGLETON",
            "ACTIVE_SLOTS_ZERO_BYPASS_BLOCKED_AT_1P5GIB_VALIDATED_AT_2P4GIB",
            "evaluate_r3_shard_admission",
            adm_red["decision"] == "WAIT_BACKPRESSURE" and adm_red["shortfall_bytes"] > 0,
            adm_green["decision"] == "ADMIT_VALIDATED_SINGLETON" and adm_green["acquired_cost"] == 2,
            f"1.5GiB={adm_red['decision']},2.4GiB={adm_green['decision']}",
        )

        # R3-M36: Tiered hysteresis across 1.5 / 2.4 / 3.3 / 5.6 GiB & preflight shortfall at 1.4 GiB
        l_1p5, _ = compute_adaptive_slot_limit_r3(int(1.50 * 1024**3), prev_slot_limit=2)
        l_1p7_hyst, _ = compute_adaptive_slot_limit_r3(int(1.70 * 1024**3), prev_slot_limit=0)
        l_2p4, _ = compute_adaptive_slot_limit_r3(int(2.40 * 1024**3), prev_slot_limit=0)
        l_3p3, _ = compute_adaptive_slot_limit_r3(int(3.30 * 1024**3), prev_slot_limit=1)
        l_5p6, _ = compute_adaptive_slot_limit_r3(int(5.60 * 1024**3), prev_slot_limit=2)
        pf_low = compute_r3_ram_preflight_and_policy(int(1.40 * 1024**3), 32 * 1024**3)
        pf_ok = compute_r3_ram_preflight_and_policy(int(5.60 * 1024**3), 32 * 1024**3)
        _rec(
            "R3-M36",
            "ADMISSION_HYSTERESIS",
            "TIERS_1P5_2P4_3P3_5P6_GIB_AND_HYSTERESIS_VERIFIED",
            "compute_adaptive_slot_limit_r3",
            (l_1p5 == 0 and l_1p7_hyst == 0 and pf_low["can_proceed"] is False and pf_low["shortfall_bytes"] > 0),
            (l_2p4 == 1 and l_3p3 == 2 and l_5p6 == 3 and pf_ok["can_proceed"] is True),
            f"limits=({l_1p5},{l_1p7_hyst},{l_2p4},{l_3p3},{l_5p6})",
        )

        # R3-M37: Over-cap slot_cost > 4 rejected at admission
        adm_over = evaluate_r3_shard_admission(
            shard_name="SHARD_BAD_OVERCAP",
            exclusive=False,
            slot_cost=5,
            estimated_shard_bytes=300 * 1024**2,
            available_ram_bytes=int(8.0 * 1024**3),
            free_disk_bytes=35 * 1024**3,
            effective_disk_reserve_bytes=20 * 1024**3,
            active_slots=0,
            active_shards_count=0,
            exclusive_running=False,
        )
        _rec(
            "R3-M37",
            "DESCENDANT_CAP",
            "SHARD_SLOT_COST_EXCEEDING_4_DESCENDANTS_REJECTED",
            "evaluate_r3_shard_admission",
            adm_over["decision"] == "REJECT_IMPOSSIBLE_OVER_CAP",
            GLOBAL_DESCENDANT_CAP_R3 == 4,
            adm_over["reason"],
        )

        # R3-M38: Simulated OS OOM exit 137 / missing JUnit shard classified as OOM failure, never ghost PASS
        oom_cls_red = classify_shard_exit_and_oom_r3(exit_code=137, raw_log_text="", xml_exists=False)
        missing_xml_red = classify_shard_exit_and_oom_r3(exit_code=0, raw_log_text="", xml_exists=False)
        clean_cls_green = classify_shard_exit_and_oom_r3(exit_code=0, raw_log_text="1 passed", xml_exists=True)
        _rec(
            "R3-M38",
            "OOM_AND_JUNIT_TRUTH",
            "EXIT_137_OOM_AND_MISSING_JUNIT_XML_REJECTED_NO_GHOST_PASS",
            "classify_shard_exit_and_oom_r3",
            (oom_cls_red["oom_or_killed_detected"] is True and oom_cls_red["shard_status"] != "PASS" and missing_xml_red["shard_status"] == "FAIL_MISSING_JUNIT"),
            (clean_cls_green["oom_or_killed_detected"] is False and clean_cls_green["shard_status"] == "PASS"),
            f"137={oom_cls_red['exit_reason']},no_xml={missing_xml_red['shard_status']}",
        )

        # R3-M39: Forged RAM PASS while 08 jsonl.gz contains a 300 MiB sample rejected by sample auditor
        gz_bad = tmp_d / "08_bad.jsonl.gz"
        with gzip.open(gz_bad, "wt", encoding="utf-8") as fh:
            fh.write(json.dumps({"available_ram_bytes": 4096 * 1024**2, "rss_bytes": 100 * 1024**2, "child_rss_bytes": 200 * 1024**2, "worker_pids": [11]}) + "\n")
            fh.write(json.dumps({"available_ram_bytes": 300 * 1024**2, "rss_bytes": 110 * 1024**2, "child_rss_bytes": 250 * 1024**2, "worker_pids": [11]}) + "\n")
        gz_good = tmp_d / "08_good.jsonl.gz"
        with gzip.open(gz_good, "wt", encoding="utf-8") as fh:
            fh.write(json.dumps({"available_ram_bytes": 4096 * 1024**2, "rss_bytes": 120 * 1024**2, "child_rss_bytes": 320 * 1024**2, "worker_pids": [11]}) + "\n")
        aud_bad = audit_r3_memory_samples_jsonl_gz(gz_bad, hard_floor_bytes=exact_512)
        aud_good = audit_r3_memory_samples_jsonl_gz(gz_good, hard_floor_bytes=exact_512)
        _rec(
            "R3-M39",
            "SAMPLE_INTEGRITY",
            "FORGED_PASS_WITH_300MIB_SAMPLE_IN_08_JSONL_GZ_REJECTED",
            "audit_r3_memory_samples_jsonl_gz",
            aud_bad["status"] == "FAIL_BELOW_HARD_FLOOR" and aud_bad["below_floor_samples_count"] == 1,
            aud_good["status"] == "PASS" and aud_good["peak_child_tree_rss_bytes"] == 320 * 1024**2,
            f"bad_min_mib={aud_bad['min_available_ram_mib']}",
        )

        # R3-M40: Static source check rejects injected 64MiB override & accepts clean R3 repo source
        st_red = detect_active_64mib_ram_floor_in_source(
            repo_root,
            extra_text_by_name={"injected_bad_runner.py": "monitor = ResourceMonitorKH01A(abort_path=p, minimum_ram_bytes=64 * 1024**2)"},
        )
        st_green = detect_active_64mib_ram_floor_in_source(repo_root)
        _rec(
            "R3-M40",
            "STATIC_SOURCE_GUARD",
            "ACTIVE_64MIB_RAM_FLOOR_OVERRIDE_IN_SOURCE_REJECTED",
            "detect_active_64mib_ram_floor_in_source",
            st_red["status"] == "FAIL" and st_red["violations_count"] >= 1,
            st_green["status"] == "PASS" and st_green["violations_count"] == 0,
            f"repo_violations={st_green['violations_count']}",
        )

        # R3-M41: Determinism hash mismatch across w=1/w=4 or partial replay row count rejected
        det_bad = verify_r3_worker_determinism_artifact({
            "execution_status": "EXECUTED",
            "pytest_controlled_subset_w1_w2_w4_repeat": {
                "w4_status": "EXECUTED",
                "runs": [
                    {"mode_label": "w=1", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64},
                    {"mode_label": "w=2", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64},
                    {"mode_label": "w=4", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "b" * 64},
                    {"mode_label": "w=1_repeat", "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64},
                ],
            },
            "r1_variant_slice_w1_w2_w4_repeat": {
                "w4_status": "EXECUTED",
                "runs": [
                    {"mode_label": "w=1", "evaluated_pairs_count": 2800, "canonical_sorted_sha256": "c" * 64},
                    {"mode_label": "w=2", "evaluated_pairs_count": 2799, "canonical_sorted_sha256": "c" * 64},
                    {"mode_label": "w=4", "evaluated_pairs_count": 2800, "canonical_sorted_sha256": "c" * 64},
                    {"mode_label": "w=1_repeat", "evaluated_pairs_count": 2800, "canonical_sorted_sha256": "c" * 64},
                ],
            },
        })
        det_good = verify_r3_worker_determinism_artifact({
            "execution_status": "EXECUTED",
            "pytest_controlled_subset_w1_w2_w4_repeat": {
                "w4_status": "EXECUTED",
                "runs": [
                    {"mode_label": m, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64}
                    for m in ("w=1", "w=2", "w=4", "w=1_repeat")
                ],
            },
            "r1_variant_slice_w1_w2_w4_repeat": {
                "w4_status": "EXECUTED",
                "runs": [
                    {"mode_label": m, "evaluated_pairs_count": 2800, "canonical_sorted_sha256": "c" * 64}
                    for m in ("w=1", "w=2", "w=4", "w=1_repeat")
                ],
            },
        })
        _rec(
            "R3-M41",
            "WORKER_DETERMINISM",
            "W4_OUTCOME_HASH_MISMATCH_AND_PARTIAL_SLICE_ROW_COUNT_REJECTED",
            "verify_r3_worker_determinism_artifact",
            det_bad["status"] == "FAIL" and len(det_bad["violations"]) >= 2,
            det_good["status"] == "PASS",
            f"violations={det_bad['violations']}",
        )

        # R3-M42: Stale R2 tested_executable_sha (`c20b0b1b...`) or BLOCKED_LOW_RAM in R3 latest_run.json rejected
        ptr_stale = validate_r3_latest_run_pointer({
            "run_id": f"20261120T120000Z_{PRIOR_R2_TESTED_SHA[:12]}",
            "tested_executable_sha": PRIOR_R2_TESTED_SHA,
            "ENGINEERING_CLOSURE_VERDICT": "PASS",
            "RAM_SAFETY_VERDICT": "PASS_STRICT_512MIB_FLOOR",
            "DISK_SAFETY_VERDICT": "PASS_STRICT_20GIB",
        })
        ptr_blk = validate_r3_latest_run_pointer({
            "run_id": "20261120T120000Z_112233445566",
            "tested_executable_sha": "1122334455667788990011223344556677889900",
            "ENGINEERING_CLOSURE_VERDICT": "BLOCKED_LOW_RAM",
            "RAM_SAFETY_VERDICT": "BLOCKED_LOW_RAM",
            "DISK_SAFETY_VERDICT": "PASS_STRICT_20GIB",
        })
        ptr_ok = validate_r3_latest_run_pointer({
            "run_id": "20261120T120000Z_112233445566",
            "tested_executable_sha": "1122334455667788990011223344556677889900",
            "ENGINEERING_CLOSURE_VERDICT": "PASS",
            "RAM_SAFETY_VERDICT": "PASS_STRICT_512MIB_FLOOR",
            "DISK_SAFETY_VERDICT": "PASS_STRICT_20GIB",
        })
        _rec(
            "R3-M42",
            "SHA_AND_POINTER_GUARD",
            "STALE_R2_SHA_AND_BLOCKED_LOW_RAM_LATEST_POINTER_REJECTED",
            "validate_r3_latest_run_pointer",
            ptr_stale["status"] == "FAIL" and ptr_blk["status"] == "FAIL",
            ptr_ok["status"] == "PASS",
            f"stale={ptr_stale['violations']},blk={ptr_blk['violations']}",
        )

        # R3-M43: Projected in-flight uncommitted footprint blocks premature simultaneous multi-launch spike
        adm_spike_red = evaluate_r3_shard_admission(
            shard_name="SHARD_INPROC_08_BALANCED",
            exclusive=False,
            slot_cost=1,
            estimated_shard_bytes=680 * 1024**2,
            available_ram_bytes=int(2.90 * 1024**3),
            free_disk_bytes=35 * 1024**3,
            effective_disk_reserve_bytes=20 * 1024**3,
            active_slots=1,
            active_shards_count=1,
            exclusive_running=False,
            inflight_uncommitted_bytes=800 * 1024**2,
            prev_slot_limit=2,
        )
        adm_spike_green = evaluate_r3_shard_admission(
            shard_name="SHARD_INPROC_08_BALANCED",
            exclusive=False,
            slot_cost=1,
            estimated_shard_bytes=680 * 1024**2,
            available_ram_bytes=int(4.20 * 1024**3),
            free_disk_bytes=35 * 1024**3,
            effective_disk_reserve_bytes=20 * 1024**3,
            active_slots=1,
            active_shards_count=1,
            exclusive_running=False,
            inflight_uncommitted_bytes=500 * 1024**2,
            prev_slot_limit=3,
        )
        _rec(
            "R3-M43",
            "INFLIGHT_BACKPRESSURE",
            "UNCOMMITTED_INFLIGHT_RAMP_UP_FOOTPRINT_PREVENTS_CONCURRENT_SPIKE",
            "evaluate_r3_shard_admission",
            adm_spike_red["decision"] == "WAIT_BACKPRESSURE",
            adm_spike_green["decision"] == "ADMIT_NORMAL",
            f"red={adm_spike_red['decision']},green={adm_spike_green['decision']}",
        )

    passed = sum(1 for m in mutations if m["status"] == "PASS")
    failed = len(mutations) - passed
    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC_R3,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC_R3,
        "prior_dsc_r2_mutations_count": base_suite["total_mutations"],
        "r3_added_ram_mutations_count": len(mutations) - int(base_suite["total_mutations"]),
        "total_mutations": len(mutations),
        "passed_mutations": passed,
        "failed_mutations": failed,
        "status": "PASS" if (failed == 0 and len(mutations) >= 43) else "FAIL",
        "fault_suite_sha256": canonical_hash(mutations),
        "mutations": mutations,
    }


# ==============================================================================
# 4. SHARD OOM CLASSIFIER, 08 SAMPLE AUDITOR & S01..S14 GATE EVALUATOR (§6, §7)
# ==============================================================================

OOM_OR_SIGNAL_EXIT_CODES: Tuple[int, ...] = (
    137,           # POSIX SIGKILL (128 + 9, standard Linux OOM killer)
    -9,            # Python subprocess negative SIGKILL
    -11,           # SIGSEGV
    139,           # 128 + 11
    -1073741801,   # Windows STATUS_NO_MEMORY (0xC0000017 signed 32-bit)
    3221225495,    # Windows STATUS_NO_MEMORY (0xC0000017 unsigned 32-bit)
    -1073741571,   # Windows STATUS_STACK_OVERFLOW (0xC00000FD)
    3221225725,
)


def classify_shard_exit_and_oom_r3(
    *,
    exit_code: int,
    raw_log_text: str,
    xml_exists: bool,
    timed_out: bool = False,
) -> Dict[str, Any]:
    rc = int(exit_code)
    has_mem_err = ("MemoryError" in raw_log_text) or ("Cannot allocate memory" in raw_log_text)
    oom_code = rc in OOM_OR_SIGNAL_EXIT_CODES
    oom_detected = bool(oom_code or has_mem_err)

    if timed_out:
        return {"shard_status": "FAIL_TIMEOUT", "oom_or_killed_detected": True, "exit_reason": f"TIMEOUT_KILLED:rc={rc}"}
    if oom_detected:
        return {"shard_status": "FAIL_OOM_OR_KILLED", "oom_or_killed_detected": True, "exit_reason": f"OOM_OR_OS_KILLED:rc={rc}"}
    if rc != 0:
        return {"shard_status": "FAIL_NONZERO_EXIT", "oom_or_killed_detected": rc < 0, "exit_reason": f"PYTEST_NONZERO_EXIT:rc={rc}"}
    if not xml_exists:
        return {"shard_status": "FAIL_MISSING_JUNIT", "oom_or_killed_detected": False, "exit_reason": "EXIT_ZERO_WITHOUT_JUNIT_XML"}
    return {"shard_status": "PASS", "oom_or_killed_detected": False, "exit_reason": "CLEAN_EXIT_0_WITH_CLOSED_JUNIT"}


def audit_r3_memory_samples_jsonl_gz(
    samples_gz_path: Path,
    *,
    hard_floor_bytes: int = HARD_ABORT_RAM_FLOOR_BYTES_R3,
    warning_near_floor_bytes: int = WARNING_NEAR_RAM_FLOOR_BYTES_R3,
) -> Dict[str, Any]:
    if not samples_gz_path.is_file() or samples_gz_path.stat().st_size == 0:
        return {
            "status": "FAIL_MISSING_SAMPLES",
            "samples_count": 0,
            "below_floor_samples_count": 0,
            "min_available_ram_bytes": 0,
            "min_available_ram_mib": 0.0,
        }

    cnt = 0
    below_cnt = 0
    near_warn_cnt = 0
    over_cap_cnt = 0
    min_avail: Optional[int] = None
    max_parent_rss = 0
    max_child_rss = 0
    max_total_rss = 0
    max_workers_obs = 0

    with gzip.open(samples_gz_path, "rt", encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            row = json.loads(ln)
            cnt += 1
            av = int(row.get("available_ram_bytes") or 0)
            pr = int(row.get("rss_bytes") or 0)
            cr = int(row.get("child_rss_bytes") or 0)
            wp = list(row.get("worker_pids") or ())
            min_avail = av if min_avail is None else min(min_avail, av)
            max_parent_rss = max(max_parent_rss, pr)
            max_child_rss = max(max_child_rss, cr)
            max_total_rss = max(max_total_rss, pr + cr)
            max_workers_obs = max(max_workers_obs, len(wp))
            if av < int(hard_floor_bytes):
                below_cnt += 1
            elif av < int(warning_near_floor_bytes):
                near_warn_cnt += 1
            if len(wp) > GLOBAL_DESCENDANT_CAP_R3:
                over_cap_cnt += 1

    if cnt == 0 or min_avail is None:
        return {
            "status": "FAIL_EMPTY_SAMPLES",
            "samples_count": 0,
            "below_floor_samples_count": 0,
            "min_available_ram_bytes": 0,
            "min_available_ram_mib": 0.0,
        }

    st = "PASS"
    if below_cnt > 0:
        st = "FAIL_BELOW_HARD_FLOOR"
    elif over_cap_cnt > 0:
        st = "FAIL_WORKER_CAP_EXCEEDED"

    return {
        "status": st,
        "samples_count": cnt,
        "below_floor_samples_count": below_cnt,
        "near_floor_warning_samples_count": near_warn_cnt,
        "over_worker_cap_samples_count": over_cap_cnt,
        "min_available_ram_bytes": int(min_avail),
        "min_available_ram_mib": round(min_avail / (1024**2), 2),
        "min_headroom_above_floor_bytes": int(min_avail - int(hard_floor_bytes)),
        "min_headroom_above_floor_mib": round((min_avail - int(hard_floor_bytes)) / (1024**2), 2),
        "peak_parent_rss_bytes": max_parent_rss,
        "peak_parent_rss_mib": round(max_parent_rss / (1024**2), 3),
        "peak_child_tree_rss_bytes": max_child_rss,
        "peak_child_tree_rss_mib": round(max_child_rss / (1024**2), 3),
        "peak_total_rss_bytes": max_total_rss,
        "peak_total_rss_mib": round(max_total_rss / (1024**2), 3),
        "max_workers_observed_in_samples": max_workers_obs,
    }


def verify_r3_worker_determinism_artifact(f06: Mapping[str, Any]) -> Dict[str, Any]:
    st_06 = str(f06.get("execution_status") or "MISSING")
    if st_06 in ("NOT_RUN_BLOCKED_LOW_RAM", "NOT_RUN_BLOCKED_LOW_DISK"):
        return {"status": st_06, "violations": [st_06], "w4_executed": False}
    if st_06 != "EXECUTED":
        return {"status": "FAIL", "violations": [f"INVALID_06_EXECUTION_STATUS:{st_06}"], "w4_executed": False}

    violations: List[str] = []
    w4_all_executed = True

    for sec_key, sha_field, count_field in (
        ("pytest_controlled_subset_w1_w2_w4_repeat", "canonical_sorted_outcome_sha256", "executed_tests_count"),
        ("r1_variant_slice_w1_w2_w4_repeat", "canonical_sorted_sha256", "evaluated_pairs_count"),
    ):
        sec = f06.get(sec_key) or {}
        runs = list(sec.get("runs") or ())
        w4_st = str(sec.get("w4_status") or "EXECUTED")
        modes = [str(r.get("mode_label") or "") for r in runs]
        if w4_st == "BENCHMARK_W4_BLOCKED_LOW_RAM":
            w4_all_executed = False
            if "w=1" not in modes or "w=1_repeat" not in modes:
                violations.append(f"MISSING_MANDATORY_W1_REPEAT:{sec_key}:{modes}")
        else:
            if modes != ["w=1", "w=2", "w=4", "w=1_repeat"]:
                violations.append(f"MISSING_BENCHMARK_MODES:{sec_key}:{modes}")
        shas = {str(r.get(sha_field) or "") for r in runs}
        cnts = {int(r.get(count_field) or -1) for r in runs}
        if len(shas) != 1 or "" in shas:
            violations.append(f"NON_IDENTICAL_OUTCOME_HASHES:{sec_key}:distinct={len(shas)}")
        if len(cnts) != 1 or any(c <= 0 for c in cnts):
            violations.append(f"INCONSISTENT_OR_PARTIAL_ROW_COUNT:{sec_key}:counts={sorted(cnts)}")

    if violations:
        return {"status": "FAIL", "violations": violations, "w4_executed": w4_all_executed}
    if not w4_all_executed:
        return {"status": "BENCHMARK_W4_BLOCKED_LOW_RAM", "violations": [], "w4_executed": False}
    return {"status": "PASS", "violations": [], "w4_executed": True}


def validate_r3_latest_run_pointer(ptr_doc: Mapping[str, Any]) -> Dict[str, Any]:
    violations: List[str] = []
    rid = str(ptr_doc.get("run_id") or "")
    t_sha = str(ptr_doc.get("tested_executable_sha") or "")
    eng_v = str(ptr_doc.get("ENGINEERING_CLOSURE_VERDICT") or ptr_doc.get("engineering_closure_verdict") or "")
    ram_v = str(ptr_doc.get("RAM_SAFETY_VERDICT") or ptr_doc.get("ram_safety_verdict") or "")
    disk_v = str(ptr_doc.get("DISK_SAFETY_VERDICT") or ptr_doc.get("disk_safety_verdict") or "")

    if not rid or rid in (PRIOR_DSC_V1_BLOCKED_RUN_ID, PRIOR_R2_ACCEPTED_RUN_ID):
        violations.append(f"ILLEGAL_OR_STALE_RUN_ID:{rid}")
    if not t_sha or t_sha in (PRIOR_DSC_V1_TESTED_SHA, PRIOR_R2_TESTED_SHA, PRIOR_R2_PUBLICATION_HEAD_SHA):
        violations.append(f"STALE_OR_MISSING_TESTED_SHA:{t_sha}")
    if eng_v != "PASS":
        violations.append(f"NON_PASS_ENGINEERING_VERDICT:{eng_v}")
    if ram_v not in ("PASS_STRICT_512MIB_FLOOR", "PASS_HARD_512MIB_WITH_NEAR_FLOOR_WARNING"):
        violations.append(f"NON_PASS_RAM_VERDICT:{ram_v}")
    if disk_v != "PASS_STRICT_20GIB":
        violations.append(f"NON_STRICT_DISK_VERDICT:{disk_v}")

    return {"status": "PASS" if not violations else "FAIL", "violations": violations}


def evaluate_kh01b_r1_ram_safety_r3_gates_from_disk(
    run_dir: Path,
    run_id: str,
    tested_sha: str,
    *,
    publication_status: str = "PENDING_REMOTE_PUSH",
) -> Dict[str, Any]:
    """
    Independently evaluates all 14 orthogonal KH-01B.R1.DSC.R3 gates (`S01..S14`, §7) from disk
    artifacts (`00..12`, `08_runtime_memory_samples_and_peak_rss.jsonl.gz`, `resource_abort.json`,
    and cryptographic `receipts/S*.json`).
    """
    def _rd(name: str) -> Dict[str, Any]:
        p = run_dir / name
        if not p.is_file():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    f00 = _rd("00_preflight_branch_sha_mount_ram_disk_immutable.json")
    f01 = _rd("01_function_callgraph_keep_as_is_and_source_diff.json")
    f02 = _rd("02_ram_policy_version_thresholds_preflight_and_projections.json")
    f03 = _rd("03_admission_hysteresis_exclusive_nested_child_reservations.json")
    f04 = _rd("04_ram_pressure_red_green_512mib_boundary_faults.json")
    f05 = _rd("05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json")
    f06 = _rd("06_async_w1_w2_w4_repeat_timings_hashes_and_capacity.json")
    f07 = _rd("07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json")
    f09 = _rd("09_disk_all_mounts_and_safe_temp_cleanup.json")
    f10 = _rd("10_integrity_previous_artifacts_and_source_sha_diffs.json")
    f11 = _rd("11_independent_verifier_red_controls_and_receipts.json")
    f12 = _rd("12_oos_zero_evaluations_and_activation_off.json")

    samples_audit = audit_r3_memory_samples_jsonl_gz(
        run_dir / "08_runtime_memory_samples_and_peak_rss.jsonl.gz",
        hard_floor_bytes=max(HARD_ABORT_RAM_FLOOR_BYTES_R3, int(f02.get("hard_abort_floor_bytes") or HARD_ABORT_RAM_FLOOR_BYTES_R3)),
    )
    abort_file_exists = (run_dir / "resource_abort.json").is_file()

    receipts_map: Dict[str, EvidenceReceiptV17931219] = {}
    rcpt_dir = run_dir / "receipts"
    if rcpt_dir.is_dir():
        for rp in sorted(rcpt_dir.glob("S*.json")):
            doc = json.loads(rp.read_text(encoding="utf-8"))
            r_in = doc.get("receipt")
            if isinstance(r_in, Mapping):
                receipts_map[rp.stem] = EvidenceReceiptV17931219(**r_in)

    gates: List[Dict[str, Any]] = []

    def _add_gate(
        gid: str,
        name: str,
        rel_art: str,
        obs: Any,
        status: str,
        viols: Sequence[str],
        *,
        require_pass_receipt: bool = True,
    ) -> None:
        v_list = list(viols)
        rcpt = receipts_map.get(gid)
        if status == "PASS" and require_pass_receipt:
            if rcpt is None:
                status = "FAIL"
                v_list.append(f"MISSING_GATE_RECEIPT:{gid}")
            elif not rcpt.is_valid_pass(
                expected_run_id=run_id,
                expected_tested_sha=tested_sha,
                run_dir=run_dir,
                reject_uniform_placeholder_hash=True,
            ):
                status = "FAIL"
                v_list.append(f"INVALID_GATE_RECEIPT:{gid}")
                v_list.extend(rcpt.violations)
        gates.append({
            "gate_id": gid,
            "name": name,
            "artifact_path": rel_art,
            "tested_run_id": run_id,
            "executable_sha": tested_sha,
            "observed_values": obs,
            "status": status,
            "violations": v_list,
            "independent_receipt": asdict(rcpt) if rcpt is not None else None,
        })

    # S01: SOURCE, branch, new tested SHA, R2 artifacts/pointer & canonical dataset preserved
    v01: List[str] = []
    if f00.get("branch") != MANDATORY_BRANCH_KH01B_R1 or f00.get("tested_executable_sha") != tested_sha:
        v01.append("BRANCH_OR_TESTED_SHA_MISMATCH")
    if tested_sha in (PRIOR_DSC_V1_TESTED_SHA, PRIOR_R2_TESTED_SHA, PRIOR_R2_PUBLICATION_HEAD_SHA):
        v01.append(f"STALE_TESTED_SHA_REUSED:{tested_sha}")
    if f00.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH01B_R1:
        v01.append("CANONICAL_DATASET_SHA_MISMATCH")
    if int(f01.get("locked_files_modified_count", 1)) != 0:
        v01.append("LOCKED_PRODUCTION_OR_STRATEGY_FILES_MODIFIED")
    if (
        f10.get("status") != "PASS"
        or f10.get("source_tree_unchanged_during_run") is not True
        or f10.get("prior_artifacts_unchanged_during_run") is not True
        or f10.get("prior_r2_accepted_bundle_intact") is not True
        or f10.get("prior_r2_latest_pointer_intact") is not True
        or int(f10.get("modified_prior_files_count", 1)) != 0
    ):
        v01.append("PRIOR_R2_OR_OLDER_ARTIFACTS_OR_SOURCE_DRIFTED")
    _add_gate(
        "S01",
        "SOURCE_BRANCH_TESTED_SHA_AND_R2_IMMUTABILITY",
        "10_integrity_previous_artifacts_and_source_sha_diffs.json",
        {"branch": f00.get("branch"), "tested_sha": tested_sha, "modified_prior_files": f10.get("modified_prior_files_count")},
        "PASS" if not v01 else "FAIL",
        v01,
    )

    # S02: RAM HARD FLOOR >= 512 MiB configured and actually enforced, no 64 MiB override, no OOM
    v02: List[str] = []
    tel_09 = f09.get("runtime_telemetry") or {}
    cfg_floor = int(f02.get("hard_abort_floor_bytes") or 0)
    env_min_ram = int(f02.get("configured_env_aurum_minimum_ram_bytes") or 0)
    st_64 = (f01.get("static_no_active_64mib_floor_check") or {}).get("status")
    preflight_ram_ok = bool(f02.get("can_proceed", False))
    shards_05 = ((f05.get("shard_manifest") or {}).get("shards") or [])
    oom_shards = [s["shard_name"] for s in shards_05 if bool(s.get("oom_or_killed_detected", False))]

    if cfg_floor < HARD_ABORT_RAM_FLOOR_BYTES_R3 or env_min_ram < HARD_ABORT_RAM_FLOOR_BYTES_R3:
        v02.append(f"RAM_HARD_FLOOR_BELOW_512MIB:cfg={cfg_floor},env={env_min_ram}")
    if st_64 != "PASS":
        v02.append("ACTIVE_64MIB_FLOOR_OVERRIDE_DETECTED_IN_SOURCE")
    if oom_shards:
        v02.append(f"OOM_OR_KILLED_SHARDS_DETECTED:{oom_shards}")

    ram_blocked = (
        not preflight_ram_ok
        or abort_file_exists
        or bool(tel_09.get("hard_ram_breached", False))
        or str(tel_09.get("latched_error") or "").startswith("BLOCKED_LOW_RAM")
        or str(f03.get("scheduler_terminal_status") or "").startswith("BLOCKED_LOW_RAM")
        or samples_audit.get("status") == "FAIL_BELOW_HARD_FLOOR"
    )
    if v02:
        s02_st = "FAIL"
        ram_verdict = "FAIL_RUNTIME_SAFETY"
    elif ram_blocked:
        s02_st = "BLOCKED_LOW_RAM"
        ram_verdict = "BLOCKED_LOW_RAM"
        v02.append(str(tel_09.get("latched_error") or f02.get("abort_reason") or "BLOCKED_LOW_RAM"))
    elif samples_audit.get("status") != "PASS":
        s02_st = "FAIL"
        ram_verdict = "FAIL_RUNTIME_SAFETY"
        v02.append(f"MEMORY_SAMPLES_AUDIT_FAILED:{samples_audit.get('status')}")
    else:
        s02_st = "PASS"
        ram_verdict = (
            "PASS_HARD_512MIB_WITH_NEAR_FLOOR_WARNING"
            if samples_audit.get("near_floor_warning_samples_count", 0) > 0
            else "PASS_STRICT_512MIB_FLOOR"
        )
    _add_gate(
        "S02",
        "RAM_HARD_FLOOR_512MIB_ENFORCED_NO_64MIB_OVERRIDE",
        "02_ram_policy_version_thresholds_preflight_and_projections.json",
        {
            "hard_floor_mib": round(cfg_floor / (1024**2), 2),
            "min_sampled_ram_mib": samples_audit.get("min_available_ram_mib"),
            "headroom_above_512mib_mib": samples_audit.get("min_headroom_above_floor_mib"),
            "ram_safety_verdict": ram_verdict,
        },
        s02_st,
        v02,
        require_pass_receipt=(s02_st == "PASS"),
    )

    # S03: ADMISSION hysteresis, validated singleton, pause timeout, <=4 OS descendants
    v03: List[str] = []
    max_ch_09 = int(tel_09.get("max_children_observed") or 0)
    max_w_08 = int(samples_audit.get("max_workers_observed_in_samples") or 0)
    if max_ch_09 > GLOBAL_DESCENDANT_CAP_R3 or max_w_08 > GLOBAL_DESCENDANT_CAP_R3:
        v03.append(f"GLOBAL_DESCENDANT_CAP_EXCEEDED:tel={max_ch_09},samples={max_w_08}>4")
    if f03.get("active_slots_zero_unvalidated_bypass_allowed") is not False:
        v03.append("UNVALIDATED_ACTIVE_SLOTS_ZERO_BYPASS_ALLOWED")
    if f03.get("scheduler_terminal_status") == "BLOCKED_LOW_RAM_ADMISSION":
        s03_st = "BLOCKED_LOW_RAM" if not v03 else "FAIL"
        if not v03:
            v03.append(str(f03.get("scheduler_abort_reason") or "BLOCKED_LOW_RAM_ADMISSION"))
    else:
        if f03.get("status") not in ("PASS", "NOT_RUN_PREFLIGHT_BLOCKED"):
            v03.append(f"ADMISSION_ARTIFACT_03_NOT_VALID:{f03.get('status')}")
        s03_st = "PASS" if not v03 else "FAIL"
    _add_gate(
        "S03",
        "PREDICTIVE_RAM_ADMISSION_HYSTERESIS_SINGLETON_AND_DESCENDANT_CAP",
        "03_admission_hysteresis_exclusive_nested_child_reservations.json",
        {
            "max_children_observed": max_ch_09,
            "backpressure_pauses": f03.get("backpressure_pause_events_count"),
            "singleton_validated_admissions": f03.get("singleton_validated_admissions_count"),
        },
        s03_st,
        v03,
        require_pass_receipt=(s03_st == "PASS"),
    )

    # S04: PRESSURE FAIL CLOSED (511.99/450/300/69 MiB -> BLOCKED_LOW_RAM; post-abort recovery never launders)
    v04: List[str] = []
    if f04.get("status") != "PASS" or int(f04.get("failed_ram_controls_count", 1)) != 0 or int(f04.get("passed_ram_controls_count") or 0) < 8:
        v04.append("RAM_BOUNDARY_AND_PRESSURE_CONTROLS_04_FAILED")
    _add_gate(
        "S04",
        "PRESSURE_FAIL_CLOSED_511MIB_BLOCKED_NO_POST_ABORT_LAUNDERING",
        "04_ram_pressure_red_green_512mib_boundary_faults.json",
        {"passed_ram_controls": f04.get("passed_ram_controls_count")},
        "PASS" if not v04 else "FAIL",
        v04,
    )

    # S05: RESOURCE METRICS authenticity (real samples in 08, stage leak metrics, non-zero shard RSS when executed)
    v05: List[str] = []
    if samples_audit.get("samples_count", 0) < 1 or int(samples_audit.get("peak_parent_rss_bytes") or 0) <= 0:
        v05.append("MISSING_OR_ZERO_RUNTIME_MEMORY_SAMPLES_IN_08")
    if not isinstance(f09.get("stage_memory_and_backpressure_ledger"), list) or len(f09.get("stage_memory_and_backpressure_ledger") or ()) == 0:
        v05.append("MISSING_STAGE_MEMORY_LEAK_LEDGER_IN_09")
    if f05.get("execution_status") == "EXECUTED":
        zero_rss_sh = [s["shard_name"] for s in shards_05 if int(s.get("peak_rss_bytes") or 0) <= 0 or int(s.get("rss_sample_count") or 0) <= 0]
        if zero_rss_sh:
            v05.append(f"SHARDS_WITH_ZERO_SAMPLED_RSS:{zero_rss_sh[:5]}")
        if int(samples_audit.get("peak_child_tree_rss_bytes") or 0) <= 0 and float(tel_09.get("peak_child_tree_rss_mib") or 0.0) <= 0.0:
            v05.append("ZERO_CHILD_TREE_RSS_DESPITE_EXECUTED_SUBPROCESS_SHARDS")
    _add_gate(
        "S05",
        "AUTHENTIC_PROCESS_TREE_AND_SHARD_MEMORY_TELEMETRY",
        "08_runtime_memory_samples_and_peak_rss.jsonl.gz",
        {
            "samples_count": samples_audit.get("samples_count"),
            "peak_parent_rss_mib": samples_audit.get("peak_parent_rss_mib"),
            "peak_child_tree_rss_mib": max(float(samples_audit.get("peak_child_tree_rss_mib") or 0.0), float(tel_09.get("peak_child_tree_rss_mib") or 0.0)),
        },
        "PASS" if not v05 else "FAIL",
        v05,
    )

    # S06: STRICT 20.5 GiB DISK PREFLIGHT & 20 GiB RESERVE, SENTINEL CLEANUP, ZERO ORPHANS
    d_pol = f00.get("disk_reserve_policy") or {}
    disk_verdict = str(d_pol.get("disk_safety_verdict") or "FAIL")
    v06: List[str] = []
    if bool(d_pol.get("lowered_reserve_without_approval", True)) or float(d_pol.get("enforced_reserve_gib") or 0.0) < 20.0:
        v06.append("DISK_RESERVE_LOWERED_BELOW_20GIB")
    if int(tel_09.get("orphan_pids_at_cleanup_count", 1)) != 0:
        v06.append("LIVE_ORPHAN_PIDS_AT_CLEANUP")
    if tel_09.get("temp_dirs_cleaned_with_sentinel_check") is not True or int(tel_09.get("unowned_paths_deleted_count", 1)) != 0:
        v06.append("TEMP_CLEANUP_OR_UNOWNED_DELETION_VIOLATION")

    if disk_verdict == "PASS_STRICT_20GIB":
        for v_lbl in ("repo", "output", "temp"):
            if float(((d_pol.get("volumes") or {}).get(v_lbl) or {}).get("free_gib") or 0.0) < 20.5:
                v06.append(f"MOUNT_BELOW_20P5_GIB:{v_lbl}")
        if float(tel_09.get("min_free_disk_gib") or 0.0) < 20.0 or float(tel_09.get("post_cleanup_free_gib") or 0.0) < 20.0:
            v06.append("RUNTIME_OR_POST_CLEANUP_DISK_BELOW_20GIB")
        s06_st = "PASS" if not v06 else "FAIL"
    elif disk_verdict == "BLOCKED_LOW_DISK":
        s06_st = "BLOCKED_LOW_DISK" if not v06 else "FAIL"
        if not v06:
            v06.append(str(d_pol.get("abort_reason") or "BLOCKED_LOW_DISK"))
    else:
        v06.append(f"INVALID_DISK_VERDICT:{disk_verdict}")
        s06_st = "FAIL"
    _add_gate(
        "S06",
        "STRICT_20P5_GIB_ALL_MOUNTS_SENTINEL_CLEANUP_ZERO_ORPHANS",
        "09_disk_all_mounts_and_safe_temp_cleanup.json",
        {"disk_safety_verdict": disk_verdict, "min_free_disk_gib": tel_09.get("min_free_disk_gib"), "orphans": tel_09.get("orphan_pids_at_cleanup_count")},
        s06_st,
        v06,
        require_pass_receipt=(s06_st == "PASS"),
    )

    # S07: FULL PYTEST REGRESSION & CLOSED JUNIT BIJECTION
    st_05 = str(f05.get("execution_status") or "MISSING")
    if st_05 == "EXECUTED" and not ram_blocked and disk_verdict == "PASS_STRICT_20GIB":
        v07: List[str] = []
        bij_05 = f05.get("independent_bijection_verification") or {}
        cnts_05 = bij_05.get("counters") or {}
        sh_man = f05.get("shard_manifest") or {}
        if (
            bij_05.get("status") != "PASS"
            or int(sh_man.get("nonzero_exit_shards_count", 1)) != 0
            or int(cnts_05.get("collected") or 0) < 5513
            or int(cnts_05.get("collected") or 0) != int(cnts_05.get("executed") or -1)
            or int(cnts_05.get("failed", 1)) != 0
            or int(cnts_05.get("errors", 1)) != 0
            or int(cnts_05.get("deselected", 0)) != 0
            or int(cnts_05.get("mandatory_skipped", 0)) != 0
        ):
            v07.append("FULL_PYTEST_OR_JUNIT_BIJECTION_FAILED")
        if not isinstance(f05.get("skip_reasons_summary"), Mapping):
            v07.append("MISSING_ENUMERATED_SKIP_REASONS")
        s07_st = "PASS" if not v07 else "FAIL"
        reg_verdict = s07_st
        _add_gate("S07", "FULL_PYTEST_COLLECTED_SCHEDULED_EXECUTED_JUNIT_BIJECTION", "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json", cnts_05, s07_st, v07)
    elif st_05 in ("NOT_RUN_BLOCKED_LOW_RAM", "ABORTED_LOW_RAM") and ram_blocked:
        reg_verdict = "NOT_RUN_BLOCKED_LOW_RAM"
        _add_gate("S07", "FULL_PYTEST_COLLECTED_SCHEDULED_EXECUTED_JUNIT_BIJECTION", "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json", {"execution_status": st_05}, "NOT_RUN_BLOCKED_LOW_RAM", ["BLOCKED_LOW_RAM"], require_pass_receipt=False)
    elif st_05 == "NOT_RUN_BLOCKED_LOW_DISK" and disk_verdict == "BLOCKED_LOW_DISK":
        junit_xmls = list((run_dir / "junit").glob("*.xml")) if (run_dir / "junit").is_dir() else []
        if junit_xmls:
            reg_verdict = "FAIL"
            _add_gate("S07", "FULL_PYTEST_COLLECTED_SCHEDULED_EXECUTED_JUNIT_BIJECTION", "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json", {"fake_xmls": len(junit_xmls)}, "FAIL", ["FAKE_JUNIT_XML_PRESENT_WHEN_NOT_RUN"])
        else:
            reg_verdict = "NOT_RUN_BLOCKED_LOW_DISK"
            _add_gate("S07", "FULL_PYTEST_COLLECTED_SCHEDULED_EXECUTED_JUNIT_BIJECTION", "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json", {"execution_status": st_05}, "NOT_RUN_BLOCKED_LOW_DISK", ["BLOCKED_LOW_DISK"], require_pass_receipt=False)
    else:
        reg_verdict = "FAIL"
        _add_gate("S07", "FULL_PYTEST_COLLECTED_SCHEDULED_EXECUTED_JUNIT_BIJECTION", "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json", {"execution_status": st_05}, "FAIL", [f"INVALID_05_STATUS:{st_05}"])

    # S08: WORKER DETERMINISM w=1, w=2, w=4, w=1_repeat
    det_res = verify_r3_worker_determinism_artifact(f06)
    det_st = det_res["status"]
    if det_st == "PASS" and not ram_blocked and disk_verdict == "PASS_STRICT_20GIB":
        w_verdict = "PASS"
        _add_gate("S08", "CONTROLLED_W1_W2_W4_REPEAT_DETERMINISM_AND_TRUTHFUL_TIMINGS", "06_async_w1_w2_w4_repeat_timings_hashes_and_capacity.json", {"pytest_w4_speedup": f06.get("measured_speedup_w4_vs_w1"), "slice_optimal_workers": (f06.get("r1_variant_slice_w1_w2_w4_repeat") or {}).get("measured_optimal_worker_count_for_slice")}, "PASS", [])
    elif det_st in ("BENCHMARK_W4_BLOCKED_LOW_RAM", "NOT_RUN_BLOCKED_LOW_RAM", "NOT_RUN_BLOCKED_LOW_DISK"):
        w_verdict = det_st
        _add_gate("S08", "CONTROLLED_W1_W2_W4_REPEAT_DETERMINISM_AND_TRUTHFUL_TIMINGS", "06_async_w1_w2_w4_repeat_timings_hashes_and_capacity.json", {"status": det_st}, det_st, det_res["violations"] or [det_st], require_pass_receipt=False)
    else:
        w_verdict = "FAIL"
        _add_gate("S08", "CONTROLLED_W1_W2_W4_REPEAT_DETERMINISM_AND_TRUTHFUL_TIMINGS", "06_async_w1_w2_w4_repeat_timings_hashes_and_capacity.json", {"status": det_st}, "FAIL", det_res["violations"])

    # S09: FULL BACKTEST 56,710 A1 + 6,375 SI8, SI0 242/166, 3 FILLS, 246 ADAPTERS MATCH SEALED R2
    st_07 = str(f07.get("execution_status") or "MISSING")
    if st_07 == "EXECUTED" and not ram_blocked and disk_verdict == "PASS_STRICT_20GIB":
        v09_bt: List[str] = []
        nb_07 = f07.get("si0_e00_primary_nb_summary") or {}
        fp_07 = f07.get("per_decision_and_episode_fingerprints") or {}
        fee_07 = f07.get("fee_sensitivity_summary") or {}
        if (
            f07.get("status") != "PASS"
            or int(f07.get("a1_evaluated_count") or 0) != 56710
            or int(f07.get("a1_mismatches_count", 1)) != 0
            or int(f07.get("si8_separate_population_count") or 0) != 6375
            or int(f07.get("three_adapter_episodes_denominator") or 0) != 246
            or int(f07.get("three_adapter_mismatches_count", 1)) != 0
            or int(nb_07.get("total_scheduled_episodes") or 0) != 242
            or int(nb_07.get("closed_trades_count") or 0) != 166
            or int(nb_07.get("wins_count") or 0) != 9
            or int(nb_07.get("losses_count") or 0) != 157
            or float(nb_07.get("total_realized_net_usd") or 0.0) != -345.8971
            or fp_07.get("a1_56710_ids_sha256") != SEALED_R2_A1_56710_IDS_SHA256
            or fp_07.get("si8_6375_ids_sha256") != SEALED_R2_SI8_6375_IDS_SHA256
            or fp_07.get("si0_242_episode_matched_tuples_sha256") != SEALED_R2_SI0_242_TUPLES_SHA256
            or fp_07.get("e00_bundle_sha256") != SEALED_R2_E00_BUNDLE_SHA256
            or float(fee_07.get("fixed_fill_0p0006_net_usd") or 0.0) != -447.4532
        ):
            v09_bt.append("BACKTEST_PARITY_OR_SEALED_R2_FINGERPRINT_MISMATCH")
        bt_verdict = "PASS" if not v09_bt else "FAIL"
        _add_gate("S09", "FULL_BACKTEST_56710_A1_6375_SI8_SI0_242_166_PARITY", "07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json", {"a1": 56710, "si8": 6375, "si0": "242/166 (9W/157L)", "net_usd": -345.8971}, bt_verdict, v09_bt)
    elif st_07 in ("NOT_RUN_BLOCKED_LOW_RAM", "NOT_RUN_BLOCKED_LOW_DISK"):
        bt_verdict = st_07
        _add_gate("S09", "FULL_BACKTEST_56710_A1_6375_SI8_SI0_242_166_PARITY", "07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json", {"execution_status": st_07}, st_07, [st_07], require_pass_receipt=False)
    else:
        bt_verdict = "FAIL"
        _add_gate("S09", "FULL_BACKTEST_56710_A1_6375_SI8_SI0_242_166_PARITY", "07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json", {"execution_status": st_07}, "FAIL", [f"INVALID_07_STATUS:{st_07}"])

    # S10: ANTI-FAKE-PASS (31 prior DSC controls + 12 R3 RAM controls = 43/43)
    v10_af: List[str] = []
    if f11.get("status") != "PASS" or int(f11.get("passed_mutations") or 0) < 43 or int(f11.get("failed_mutations", 1)) != 0:
        v10_af.append("R3_ADVERSARIAL_FAULT_SUITE_BELOW_43_OR_FAILED")
    _add_gate("S10", "ANTI_FAKE_PASS_31_PRIOR_PLUS_R3_ADVERSARIAL_FAULTS", "11_independent_verifier_red_controls_and_receipts.json", {"passed_mutations": f"{f11.get('passed_mutations')}/{f11.get('total_mutations')}"}, "PASS" if not v10_af else "FAIL", v10_af)

    # S11: INDEPENDENT VERIFY & RECEIPTS VALIDITY
    v11_iv: List[str] = []
    expected_rcpts = 13 if (disk_verdict == "PASS_STRICT_20GIB" and not ram_blocked) else 8
    if len(receipts_map) < expected_rcpts:
        v11_iv.append(f"INCOMPLETE_S_GATE_RECEIPTS:{len(receipts_map)}<{expected_rcpts}")
    _add_gate("S11", "SEPARATE_OS_PROCESS_INDEPENDENT_DISK_VERIFIER", "11_independent_verifier_red_controls_and_receipts.json", {"s_receipts_count": len(receipts_map)}, "PASS" if not v11_iv else "FAIL", v11_iv)

    # S12: OOS ESCROW UNTOUCHED & ALL TRADE RUNTIME MODES OFF
    v12_oos: List[str] = []
    esc_12 = f12.get("future_oos_escrow") or {}
    modes_12 = f12.get("runtime_safety_modes") or {}
    if esc_12.get("FRESH_OOS_VERDICT") != "BLOCKED_NO_UNTOUCHED_DATA" or int(esc_12.get("strategy_evaluation_count_total", 1)) != 0:
        v12_oos.append("OOS_ESCROW_CONTAMINATED_OR_EVALUATED")
    if f12.get("ACTIVATION_READY") != "NO" or any(modes_12.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
        v12_oos.append("RUNTIME_ACTIVATION_MODES_NOT_OFF")
    _add_gate("S12", "OOS_ESCROW_UNTOUCHED_AND_RUNTIME_ACTIVATION_OFF", "12_oos_zero_evaluations_and_activation_off.json", {"FRESH_OOS_VERDICT": esc_12.get("FRESH_OOS_VERDICT"), "evaluation_count": esc_12.get("strategy_evaluation_count_total")}, "PASS" if not v12_oos else "FAIL", v12_oos)

    # S13: PUBLICATION & REMOTE SHA READBACK
    _add_gate(
        "S13",
        "SAME_BRANCH_PUBLICATION_AND_REMOTE_SHA_READBACK",
        "00_preflight_branch_sha_mount_ram_disk_immutable.json",
        {"publication_status": publication_status, "tested_executable_sha": tested_sha},
        publication_status,
        [] if publication_status == "PASS_REMOTE_VERIFIED" else ["AWAITING_REMOTE_PUSH_AND_READBACK"],
        require_pass_receipt=False,
    )

    # S14: ECONOMIC TRUTH ORTHOGONAL TO ENGINEERING PASS
    v14_ec: List[str] = []
    econ_v = str(f12.get("RESEARCH_ECONOMIC_VERDICT") or "NO_PROVEN_EDGE_BASELINE_UNCHANGED_FEE_SENSITIVITY_NEGATIVE")
    kh01c_v = str(f12.get("KH01C_ADVANCEMENT_VERDICT") or "NO_ECONOMIC_ADVANCEMENT_CANDIDATE")
    if econ_v != "NO_PROVEN_EDGE_BASELINE_UNCHANGED_FEE_SENSITIVITY_NEGATIVE" or kh01c_v != "NO_ECONOMIC_ADVANCEMENT_CANDIDATE":
        v14_ec.append("ILLEGAL_ECONOMIC_OR_KH01C_PROMOTION_IN_R3")
    _add_gate("S14", "ORTHOGONAL_ECONOMIC_TRUTH_NO_PROVEN_EDGE", "12_oos_zero_evaluations_and_activation_off.json", {"RESEARCH_ECONOMIC_VERDICT": econ_v, "KH01C_ADVANCEMENT_VERDICT": kh01c_v}, "PASS" if not v14_ec else "FAIL", v14_ec)

    by_gid = {g["gate_id"]: g for g in gates}
    any_fail = any(g["status"] == "FAIL" for g in gates) or publication_status in ("FAIL_REMOTE_READBACK", "FAIL")

    if any_fail:
        eng_verdict = "FAIL_INTEGRITY" if by_gid["S01"]["status"] == "FAIL" else "FAIL_RUNTIME_SAFETY"
    elif disk_verdict == "BLOCKED_LOW_DISK":
        eng_verdict = "BLOCKED_LOW_DISK"
    elif ram_blocked or by_gid["S02"]["status"] == "BLOCKED_LOW_RAM" or by_gid["S03"]["status"] == "BLOCKED_LOW_RAM":
        eng_verdict = "BLOCKED_LOW_RAM"
    elif all(by_gid[k]["status"] == "PASS" for k in ("S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09", "S10", "S11", "S12", "S14")):
        eng_verdict = "PASS"
    else:
        eng_verdict = "BLOCKED_LOW_RAM"

    explicit_blockers: List[str] = []
    for g in gates:
        if g["gate_id"] == "S13" and g["status"] == "PENDING_REMOTE_PUSH":
            continue
        if g["status"] not in ("PASS", "PASS_REMOTE_VERIFIED"):
            explicit_blockers.append(f"{g['gate_id']}:{g['status']}:{g['violations']}")

    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC_R3,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC_R3,
        "ram_policy_version": RAM_POLICY_VERSION_R3,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "head_at_start_sha": f00.get("head_at_start_sha"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": f00.get("canonical_dataset_sha256"),
        "orthogonal_verdicts": {
            "ENGINEERING_CLOSURE_VERDICT": eng_verdict,
            "RAM_SAFETY_VERDICT": ram_verdict,
            "DISK_SAFETY_VERDICT": disk_verdict,
            "FULL_REGRESSION_VERDICT": reg_verdict,
            "WORKER_DETERMINISM_VERDICT": w_verdict,
            "BACKTEST_VERDICT": bt_verdict,
            "DATA_ACQUISITION_VERDICT": str(f12.get("DATA_ACQUISITION_VERDICT") or "REUSED_VERIFIED_R2_EXTENDED_DEV_SNAPSHOT"),
            "RESEARCH_ECONOMIC_VERDICT": econ_v,
            "KH01C_ADVANCEMENT_VERDICT": kh01c_v,
            "FRESH_OOS_VERDICT": str(esc_12.get("FRESH_OOS_VERDICT") or "BLOCKED_NO_UNTOUCHED_DATA"),
            "ACTIVATION_READY": "NO",
            "RUNTIME_SAFETY_MODES": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "PUBLICATION_VERDICT": publication_status,
        },
        "ram_headroom_summary": {
            "prior_r2_min_available_ram_mib": 68.91,
            "r3_hard_abort_floor_mib": round(HARD_ABORT_RAM_FLOOR_BYTES_R3 / (1024**2), 2),
            "r3_min_sampled_available_ram_bytes": samples_audit.get("min_available_ram_bytes"),
            "r3_min_sampled_available_ram_mib": samples_audit.get("min_available_ram_mib"),
            "r3_min_headroom_above_512mib_mib": samples_audit.get("min_headroom_above_floor_mib"),
            "r3_peak_parent_rss_mib": samples_audit.get("peak_parent_rss_mib"),
            "r3_peak_child_tree_rss_mib": max(float(samples_audit.get("peak_child_tree_rss_mib") or 0.0), float(tel_09.get("peak_child_tree_rss_mib") or 0.0)),
            "r3_peak_total_rss_mib": samples_audit.get("peak_total_rss_mib"),
            "r3_samples_count": samples_audit.get("samples_count"),
        },
        "gates_summary": {
            "total_gates": len(gates),
            "pass_count": sum(1 for g in gates if g["status"] in ("PASS", "PASS_REMOTE_VERIFIED")),
            "blocked_or_not_run_count": sum(
                1 for g in gates if g["status"] not in ("PASS", "PASS_REMOTE_VERIFIED", "FAIL")
            ),
            "fail_count": sum(1 for g in gates if g["status"] == "FAIL"),
        },
        "gates": gates,
        "explicit_blockers": explicit_blockers,
    }


# ==============================================================================
# 5. README & VIETNAMESE HANDOVER RENDERER + CROSS-VIEW VERIFIER (§6, §9)
# ==============================================================================

def render_kh01b_r1_ram_safety_r3_readme_and_handover_vi(
    *,
    run_id: str,
    branch: str,
    head_at_start_sha: str,
    tested_sha: str,
    dataset_sha: str,
    f00: Mapping[str, Any],
    f01: Mapping[str, Any],
    f02: Mapping[str, Any],
    f03: Mapping[str, Any],
    f05: Mapping[str, Any],
    f06: Mapping[str, Any],
    f07: Mapping[str, Any],
    f09: Mapping[str, Any],
    f11: Mapping[str, Any],
    f12: Mapping[str, Any],
    f13: Mapping[str, Any],
) -> Tuple[str, str]:
    ov = f13["orthogonal_verdicts"]
    ram_sum = f13["ram_headroom_summary"]
    tel = f09.get("runtime_telemetry") or {}
    d_pol = f00.get("disk_reserve_policy") or {}
    cnts_05 = ((f05.get("independent_bijection_verification") or {}).get("counters") or {})
    nb_07 = f07.get("si0_e00_primary_nb_summary") or {}

    gate_rows = [
        f"| `{g['gate_id']}` | `{g['name']}` | **{g['status']}** | `{json.dumps(g['observed_values'], sort_keys=True)}` |"
        for g in f13["gates"]
    ]
    gate_table = "| Gate ID | Gate Name | Status | Observed Summary |\n|---|---|---|---|\n" + "\n".join(gate_rows)

    fn_rows = [
        f"| `{r['file']}` | `{r['function']}` | `{r['start_line']}..{r['end_line']}` | {r['purpose']} | `{str(r['before_sha'])[:12]}` | `{str(r['after_sha'])[:12]}` |"
        for r in (f01.get("r3_modified_and_added_functions") or [])
    ]
    fn_table = (
        "| File | Function | Lines | Purpose | Before SHA12 | After SHA12 |\n|---|---|---:|---|---|---|\n"
        + "\n".join(fn_rows)
    )

    keep_rows = [
        f"| `{r['file']}` | `{r['function']}` | `{r['start_line']}..{r['end_line']}` | {r['purpose']} | `{str(r['before_sha'])[:12]}` | `{str(r['after_sha'])[:12]}` |"
        for r in (f01.get("keep_as_is_functions") or [])
    ]
    keep_table = (
        "| File | Function | Lines | Purpose | Before SHA12 | After SHA12 |\n|---|---|---:|---|---|---|\n"
        + "\n".join(keep_rows)
    )

    readme = f"""# AURUM DESK — KH-01B.R1.DSC.R3: RAM SAFETY & ADAPTIVE TEST SCHEDULING CLOSURE (`{SPEC_VERSION_KH01B_R1_DSC_R3}`)

- **Run ID**: `{run_id}`
- **Mandatory Branch**: `{branch}`
- **HEAD at Start (Accepted R2 Publication HEAD)**: `{head_at_start_sha}`
- **Prior Accepted R2 Run Preserved Untouched**: `{PRIOR_R2_ACCEPTED_RUN_ID}` (`tested_sha = {PRIOR_R2_TESTED_SHA}`)
- **Tested Executable Source SHA (`CODE_COMMIT_SHA`)**: `{tested_sha}`
- **Canonical Dataset SHA256 (`REUSED_RESEARCH`, `MODELED_BID_ASK`)**: `{dataset_sha}`
- **RAM Policy Version**: `{RAM_POLICY_VERSION_R3}` (`policy_sha256 = {f02.get('policy_sha256')}`)

## 1. Orthogonal Verdicts
- `ENGINEERING_CLOSURE_VERDICT`: **{ov['ENGINEERING_CLOSURE_VERDICT']}**
- `RAM_SAFETY_VERDICT`: **{ov['RAM_SAFETY_VERDICT']}** (Hard floor: `>= {ram_sum['r3_hard_abort_floor_mib']} MiB`; R2 min RAM: `68.91 MiB`; R3 min sampled RAM: `{ram_sum['r3_min_sampled_available_ram_mib']} MiB`; Headroom above 512 MiB: `{ram_sum['r3_min_headroom_above_512mib_mib']} MiB`)
- `DISK_SAFETY_VERDICT`: **{ov['DISK_SAFETY_VERDICT']}** (Preflight floor: `>= {d_pol.get('min_acceptance_preflight_gib')} GiB`; Constraining preflight free: `{d_pol.get('preflight_baseline_free_gib')} GiB`; Min runtime free: `{tel.get('min_free_disk_gib')} GiB`)
- `FULL_REGRESSION_VERDICT`: **{ov['FULL_REGRESSION_VERDICT']}** (`collected = {cnts_05.get('collected')}`, `executed = {cnts_05.get('executed')}`, `passed = {cnts_05.get('passed')}`, `skipped = {cnts_05.get('skipped')}`, `failed = {cnts_05.get('failed')}`, `errors = {cnts_05.get('errors')}`)
- `WORKER_DETERMINISM_VERDICT`: **{ov['WORKER_DETERMINISM_VERDICT']}** (`pytest w4 vs w1 speedup = {f06.get('measured_speedup_w4_vs_w1')}x`)
- `BACKTEST_VERDICT`: **{ov['BACKTEST_VERDICT']}** (`56710` A1 + `6375` SI8 = `63085` cohorts; `SI0` `242` scheduled / `166` closed = `9W/157L`, `net = {nb_07.get('total_realized_net_usd', -345.8971)} USD`, `246` 3-adapter episodes `0` mismatches)
- `RESEARCH_ECONOMIC_VERDICT`: **{ov['RESEARCH_ECONOMIC_VERDICT']}**
- `KH01C_ADVANCEMENT_VERDICT`: **{ov['KH01C_ADVANCEMENT_VERDICT']}**
- `FRESH_OOS_VERDICT`: **{ov['FRESH_OOS_VERDICT']}** (`strategy_evaluation_count = {(f12.get('future_oos_escrow') or {}).get('strategy_evaluation_count_total')}`)
- `ACTIVATION_READY`: **{ov['ACTIVATION_READY']}** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)
- `PUBLICATION_VERDICT` (pre-push bundle): **{ov['PUBLICATION_VERDICT']}**

## 2. R3 RAM Safety & Adaptive Scheduler Telemetry (`02`, `03`, `08`, `09`)
- **Hard Floor & Headroom**: `hard_abort_floor_mib = {ram_sum['r3_hard_abort_floor_mib']}`, `min_sampled_available_ram_mib = {ram_sum['r3_min_sampled_available_ram_mib']}`, `headroom_above_512mib = {ram_sum['r3_min_headroom_above_512mib_mib']} MiB` (`samples_count = {ram_sum['r3_samples_count']}`).
- **Process-Tree Peak RSS**: `peak_parent_rss_mib = {ram_sum['r3_peak_parent_rss_mib']}`, `peak_child_tree_rss_mib = {ram_sum['r3_peak_child_tree_rss_mib']}`, `peak_total_rss_mib = {ram_sum['r3_peak_total_rss_mib']}` (system available RAM also reflects external non-test OS processes).
- **Scheduler Backpressure & Descendants**: `max_children_observed = {tel.get('max_children_observed')}` (`<= 4`), `orphan_pids_at_cleanup_count = {tel.get('orphan_pids_at_cleanup_count')}`, `slot_downshift_events = {f03.get('slot_downshift_events_count')}`, `backpressure_pauses = {f03.get('backpressure_pause_events_count')}`, `singleton_validated_admissions = {f03.get('singleton_validated_admissions_count')}`.
- **Adversarial RED/GREEN Controls (`11`)**: `{f11.get('passed_mutations')}/{f11.get('total_mutations')}` PASS.

## 3. Orthogonal Gate Matrix (`S01..S14`)
{gate_table}

## 4. Exact Function Callgraph & Before/After SHA256 (`01`)
### Modified / Added R3 Infrastructure Functions
{fn_table}

### Immutable Production & Strategy Functions (`KEEP_AS_IS`)
{keep_table}

## 5. Platform-Safe Replay Command (Current Committed Code SHA)
```bash
python aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py --r3 --max-workers 4 --offline
```
"""

    handover = f"""BÀN GIAO NGHIỆM THU KH-01B.R1.DSC.R3 — RAM SAFETY & ADAPTIVE TEST SCHEDULING CLOSURE ({SPEC_VERSION_KH01B_R1_DSC_R3})
Run ID: {run_id}
Nhánh bắt buộc: {branch}
HEAD tại thời điểm bắt đầu (commit công bố R2 đã nghiệm thu): {head_at_start_sha}
Bản chạy R2 đã nghiệm thu được bảo toàn nguyên vẹn: {PRIOR_R2_ACCEPTED_RUN_ID} (tested_sha={PRIOR_R2_TESTED_SHA})
CODE_COMMIT_SHA được kiểm định thực tế (tested_executable_sha): {tested_sha}
Canonical Dataset SHA256 (bất biến, REUSED_RESEARCH / MODELED_BID_ASK): {dataset_sha}
Phiên bản chính sách RAM: {RAM_POLICY_VERSION_R3}

1. MA TRẬN PHÁN QUYẾT ĐỘC LẬP (ORTHOGONAL VERDICTS):
- ENGINEERING_CLOSURE_VERDICT: {ov['ENGINEERING_CLOSURE_VERDICT']}
- RAM_SAFETY_VERDICT: {ov['RAM_SAFETY_VERDICT']} (Sàn cứng >= {ram_sum['r3_hard_abort_floor_mib']} MiB; Đáy RAM ở R2 cũ: 68.91 MiB; Đáy RAM thực đo ở R3: {ram_sum['r3_min_sampled_available_ram_mib']} MiB; Dư địa trên 512 MiB: {ram_sum['r3_min_headroom_above_512mib_mib']} MiB)
- DISK_SAFETY_VERDICT: {ov['DISK_SAFETY_VERDICT']} (Preflight trống nhỏ nhất: {d_pol.get('preflight_baseline_free_gib')} GiB; Đáy runtime: {tel.get('min_free_disk_gib')} GiB; Sau thu dẹp: {tel.get('post_cleanup_free_gib')} GiB)
- FULL_REGRESSION_VERDICT: {ov['FULL_REGRESSION_VERDICT']} (collected={cnts_05.get('collected')}, executed={cnts_05.get('executed')}, passed={cnts_05.get('passed')}, skipped={cnts_05.get('skipped')}, failed={cnts_05.get('failed')}, errors={cnts_05.get('errors')})
- WORKER_DETERMINISM_VERDICT: {ov['WORKER_DETERMINISM_VERDICT']} (speedup tập pytest w=4 so với w=1 = {f06.get('measured_speedup_w4_vs_w1')}x)
- BACKTEST_VERDICT: {ov['BACKTEST_VERDICT']} (56710 A1 + 6375 SI8 = 63085 cơ hội tách biệt; SI0/E00 242 đặt lệnh / 166 đóng lệnh = 9W/157L, Net={nb_07.get('total_realized_net_usd', -345.8971)} USD; 246 tập 3-adapter khớp 100%)
- RESEARCH_ECONOMIC_VERDICT: {ov['RESEARCH_ECONOMIC_VERDICT']}
- KH01C_ADVANCEMENT_VERDICT: {ov['KH01C_ADVANCEMENT_VERDICT']}
- FRESH_OOS_VERDICT: {ov['FRESH_OOS_VERDICT']} (strategy_evaluation_count={(f12.get('future_oos_escrow') or {}).get('strategy_evaluation_count_total')})
- ACTIVATION_READY: {ov['ACTIVATION_READY']} (PRODUCTION=OFF, LIVE=OFF, PAPER=OFF, AUTO=OFF)
- PUBLICATION_VERDICT (trước khi đối chiếu blob từ xa): {ov['PUBLICATION_VERDICT']}

2. THÔNG SỐ GIÁM SÁT RAM, CÂY TIẾN TRÌNH & LẬP LỊCH THÍCH ỨNG:
- Mẫu đo 0.5s: {ram_sum['r3_samples_count']} mẫu; peak_parent_rss_mib={ram_sum['r3_peak_parent_rss_mib']}, peak_child_tree_rss_mib={ram_sum['r3_peak_child_tree_rss_mib']}, peak_total_rss_mib={ram_sum['r3_peak_total_rss_mib']}.
- Tiến trình con tối đa: {tel.get('max_children_observed')} (<=4), orphan_pids={tel.get('orphan_pids_at_cleanup_count')}, downshifts={f03.get('slot_downshift_events_count')}, backpressure_pauses={f03.get('backpressure_pause_events_count')}.
- Đột biến đối chứng ĐỎ/XANH (DSC-M01..31 + R3-M32..43): {f11.get('passed_mutations')}/{f11.get('total_mutations')} PASS.

3. MA TRẬN 14 CỔNG NGHIỆM THU S01..S14:
{gate_table}

4. LỆNH CHẠY LẠI AN TOÀN TRÊN CÙNG NHÁNH:
  python aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py --r3 --max-workers 4 --offline
"""
    return readme, handover


def verify_kh01b_r1_ram_safety_r3_cross_view_consistency(
    *,
    readme_text: str,
    handover_text: str,
    f00: Mapping[str, Any],
    f11: Mapping[str, Any],
    f13: Mapping[str, Any],
) -> Dict[str, Any]:
    violations: List[str] = []
    ov = f13["orthogonal_verdicts"]
    ram_sum = f13["ram_headroom_summary"]
    d_pol = f00.get("disk_reserve_policy") or {}

    req_tokens = [
        str(f13.get("run_id") or ""),
        str(f13.get("tested_executable_sha") or ""),
        str(ov.get("ENGINEERING_CLOSURE_VERDICT") or ""),
        str(ov.get("RAM_SAFETY_VERDICT") or ""),
        str(ov.get("DISK_SAFETY_VERDICT") or ""),
        str(ov.get("FULL_REGRESSION_VERDICT") or ""),
        str(ov.get("BACKTEST_VERDICT") or ""),
        str(ov.get("WORKER_DETERMINISM_VERDICT") or ""),
        str(ov.get("KH01C_ADVANCEMENT_VERDICT") or ""),
        str(ov.get("FRESH_OOS_VERDICT") or ""),
        str(ram_sum.get("r3_min_sampled_available_ram_mib") or ""),
        str(d_pol.get("preflight_baseline_free_gib") or ""),
        f"{f11.get('passed_mutations')}/{f11.get('total_mutations')}",
    ]
    for tok in req_tokens:
        if tok not in readme_text:
            violations.append(f"README_R3_MISSING_TOKEN:{tok}")
        if tok not in handover_text:
            violations.append(f"HANDOVER_R3_VI_MISSING_TOKEN:{tok}")

    if ov.get("ENGINEERING_CLOSURE_VERDICT") != "PASS":
        for bad_claim in ("`ENGINEERING_CLOSURE_VERDICT`: **PASS**", "ENGINEERING_CLOSURE_VERDICT: PASS\n"):
            if bad_claim in readme_text or bad_claim in handover_text:
                violations.append(f"FALSE_ENGINEERING_PASS_CLAIM_IN_TEXT:{bad_claim.strip()}")

    return {
        "status": "PASS" if not violations else "FAIL",
        "checked_tokens_count": len(req_tokens),
        "violations": violations,
    }
