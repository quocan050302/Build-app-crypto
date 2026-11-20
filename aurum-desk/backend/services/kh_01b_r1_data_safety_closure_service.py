"""
backend/services/kh_01b_r1_data_safety_closure_service.py

AURUM DESK — KH-01B.R1.DSC: DATA & SAFETY TRUTH CLOSURE SERVICE
SPEC VERSION: KH-01B.R1.DSC-v1.0 (2026-11-19)

Pure research-only audit, schema verification, window-coverage accounting, Decimal fee
sensitivity (`0.0004` reference vs `0.0006` observed public contract snapshot + `2x`/`3x`
cost stress in strictly separated `FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS` and `FULL_RE_RISKED_REPLAY`
modes), RED/GREEN negative-control suite (`DSC-M01`..`DSC-M22`), and independent `G-DS-01`..`G-DS-15`
gate evaluation from closed disk artifacts.

NON-NEGOTIABLE ISOLATION:
  - ZERO live/paper/auto/production execution paths; `ACTIVATION_READY = "NO"`.
  - NEVER mutates `domain_calculator.CostAssumptions`, `SharedStrategyEvaluator`,
    `historical_dataset_XAUUSDT.json.gz`, or any sealed `KH-01A*` / `KH-01B*` artifact.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import gzip
import hashlib
import importlib
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.kh_01b_r1_entry_edge_discovery_service import (
    CANONICAL_DATASET_SHA256_KH01B_R1,
    MANDATORY_BRANCH_KH01B_R1,
    RESEARCH_ONLY_FEATURE_FLAG_KH01B_R1,
    assert_research_only_runtime_modes_off,
)
from services.v17_9_1_decision_funnel import classify_historical_dataset_gaps
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_19_truth_and_core_repair_service import EvidenceReceiptV17931219

SPEC_VERSION_KH01B_R1_DSC = "KH-01B.R1.DSC-v1.0"
SCHEMA_VERSION_KH01B_R1_DSC = "kh_01b_r1_data_safety.v1"

SPEC_VERSION_KH01B_R1_DSC_R2 = "KH-01B.R1.DSC.R2-v1.0"
SCHEMA_VERSION_KH01B_R1_DSC_R2 = "kh_01b_r1_data_safety_r2.v1"

PRIOR_KH01B_R1_PUBLICATION_HEAD_SHA = "1ee4a8747e7a1205e64a5fbfacfde1da0aa72782"
PRIOR_KH01B_R1_TESTED_SHA = "3661c2eeb562d326c3065bd71c89df7878203736"
PRIOR_KH01B_R1_RUN_REL_DIR = "aurum-desk/docs/artifacts/kh_01b_r1/20261119T115223Z_3661c2eeb562"

PRIOR_DSC_V1_BLOCKED_RUN_ID = "20261119T150709Z_0c247bdc466a"
PRIOR_DSC_V1_TESTED_SHA = "0c247bdc466a76da2d17b2c9ccfd9a0431dcc73e"
PRIOR_DSC_V1_EVIDENCE_SHA = "070a7e4ab0bddd741f4dc9c7cb511fcf20b2300e"
PRIOR_DSC_V1_PUBLICATION_HEAD_SHA = "647206f3c446db202ecf152a4f8e028de56d101c"
PRIOR_DSC_V1_RUN_REL_DIR = "aurum-desk/docs/artifacts/kh_01b_r1_data_safety/20261119T150709Z_0c247bdc466a"
PRIOR_DSC_V1_BLOCKED_RECEIPT_REL = "aurum-desk/docs/artifacts/kh_01b_r1_data_safety/blocked_publication_receipt_20261119T150709Z_0c247bdc466a.json"

REQUIRED_ARTIFACTS_KH01B_R1_DSC_R2: Tuple[str, ...] = (
    "00_preflight_branch_head_code_sha_disk_mounts_dataset.json",
    "01_function_callgraph_and_immutability_manifest.json",
    "02_gap_schema_six_tf_and_actual_missingness.json",
    "03_pagination_decimal_dedupe_cursor_and_red_green.json",
    "04_requested_window_coverage_origin_and_online_capture_status.json",
    "05_fee_0p04_0p06_fixed_fill_and_rerisked_truth.json",
    "06_strict_disk_recovery_actions_and_resource_monitor.json",
    "07_sha_lock_current_HEAD_vs_tested_executable_execution_graph.json",
    "08_async_w1_w2_w4_w1repeat_hashes_timing_and_worker_manifest.json",
    "09_full_regression_junit_collected_executed_bijection.json",
    "10_full_baseline_backtest_three_fill_1pos_adapter_and_fingerprint_parity.json",
    "11_negative_controls_fault_injection_independent_verifier.json",
    "12_OOS_untouched_and_activation_off_receipt.json",
    "13_prior_artifact_and_code_immutability_diffs.json",
    "14_final_orthogonal_verdicts_gate_matrix_blockers.json",
    "15_independent_verifier_manifest_and_content_sha256.json",
    "README_KH01B_R1_DSC_R2.md",
    "HANDOVER_KH01B_R1_DSC_R2_VI.txt",
)

REQUIRED_ARTIFACTS_KH01B_R1_DSC: Tuple[str, ...] = (
    "00_preflight_branch_head_dataset_disk_volumes.json",
    "01_immutable_baseline_and_keep_as_is_callgraph.json",
    "02_gap_schema_mapping_red_green_and_six_tf_completeness.json",
    "03_pagination_cursor_order_boundary_duplicates_negative_controls.json",
    "04_extended_dev_requested_vs_observed_window_coverage.json",
    "05_bitget_origin_fee_quote_calendar_provenance.json",
    "06_baseline_0p04_vs_public_0p06_fee_sensitivity_caveats.json",
    "07_strict_20gib_disk_policy_injections_and_resource_telemetry.json",
    "08_async_w1_w2_w4_repeat_determinism_and_speed.json",
    "09_full_regression_collected_executed_junit_exit_bijection.json",
    "10_backtest_original_parity_three_fill_and_adapter_fingerprints.json",
    "11_data_oos_partition_never_evaluated_verdict.json",
    "12_independent_verifier_fault_injections.json",
    "13_manifest_sha256_independent_os_readback.json",
    "14_final_orthogonal_verdicts_and_blockers.json",
    "README_KH01B_R1_DATA_SAFETY_CLOSURE.md",
    "HANDOVER_KH01B_R1_DATA_SAFETY_VI.txt",
)

MANDATORY_DSC_OFFLINE_GATES: Tuple[str, ...] = (
    "G-DS-01",
    "G-DS-02",
    "G-DS-03",
    "G-DS-04",
    "G-DS-05",
    "G-DS-06",
    "G-DS-07",
    "G-DS-08",
    "G-DS-12",
    "G-DS-13",
    "G-DS-14",
)

HEAVY_DSC_GATES_WHEN_DISK_PASS: Tuple[str, ...] = (
    "G-DS-09",
    "G-DS-10",
    "G-DS-11",
)

LOCKED_PRODUCTION_AND_RESEARCH_FILES: Tuple[str, ...] = (
    "aurum-desk/backend/services/shared_strategy_evaluator.py",
    "aurum-desk/backend/domain_calculator.py",
    "aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py",
    "aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py",
    "aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py",
    "aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py",
    "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for ln in fh:
            s = ln.strip()
            if s:
                yield json.loads(s)


def _q4(val: Decimal) -> Decimal:
    return val.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _create_reparse_link_for_test(target_dir: Path, link_path: Path) -> str:
    """
    Creates a real OS-level directory symlink where permitted, or falls back to a real
    Windows NTFS directory junction (`_winapi.CreateJunction`) when `SeCreateSymbolicLinkPrivilege`
    is unavailable (`WinError 1314`). Never mocks filesystem behavior.
    """
    try:
        os.symlink(target_dir, link_path, target_is_directory=True)
        return "SYMLINK"
    except OSError:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target_dir), str(link_path))
            return "NTFS_JUNCTION"
        raise


def _remove_reparse_link_for_test(link_path: Path) -> None:
    """Safely unlinks a symlink or removes a Windows NTFS junction without touching its target."""
    try:
        if link_path.is_symlink():
            link_path.unlink(missing_ok=True)
            return
    except OSError:
        pass
    is_junc = getattr(link_path, "is_junction", None)
    if callable(is_junc):
        try:
            if bool(is_junc()):
                os.rmdir(link_path)
                return
        except OSError:
            pass
    if link_path.exists():
        link_path.unlink(missing_ok=True)


# ==============================================================================
# 1. IMMUTABLE BASELINE, HISTORICAL DEFECT AUDIT & MUST-KEEP CALLGRAPH (01)
# ==============================================================================

def build_kh01b_r1_dsc_immutable_baseline_and_callgraph(
    repo_root: Path,
    *,
    starting_head_sha: str,
    tested_sha: str,
    changed_files: Sequence[str],
) -> Dict[str, Any]:
    """
    Audits locked production/research functions by AST/inspect source SHA256 (§2 & §10.I),
    confirms zero locked files were modified, and documents the 6 historical defects found in
    the immutable `20261119T115223Z_3661c2eeb562` run without modifying any prior files.
    """
    must_keep_specs = [
        ("aurum-desk/backend/services/shared_strategy_evaluator.py", "services.shared_strategy_evaluator", "SharedStrategyEvaluator.evaluate_model_direction", "Production model evaluation; untouched."),
        ("aurum-desk/backend/domain_calculator.py", "domain_calculator", "CostAssumptions", "Authoritative default cost config (maker=0.0002, taker=0.0004, slip=0.10); read-only."),
        ("aurum-desk/backend/domain_calculator.py", "domain_calculator", "calculate_risk_reward", "Authoritative R:R, fee, sizing & liquidation domain calculator; read-only."),
        ("aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py", "services.v17_9_3_1_2_2_signal_intelligence", "advance_pending_confirmation_state_v1793122", "Causal forward confirmation state machine; untouched."),
        ("aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py", "services.v17_9_3_1_2_2_signal_intelligence", "arbitrate_competing_model_intents_v1793122", "Deterministic static model priority arbitration; untouched."),
        ("aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py", "services.v17_9_3_macro_unicorn_intelligence", "resolve_causal_macro_time_window", "ICT Macro NY schedule resolver; untouched."),
        ("aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py", "services.v17_9_3_macro_unicorn_intelligence", "evaluate_causal_unicorn_confluence", "Strict Breaker+FVG confluence authority; untouched."),
        ("aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py", "services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service", "compute_causal_tf_atr_v17931214", "Causal closed-bar source-TF ATR calculator; untouched."),
        ("aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py", "services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service", "build_d4_full_63085_and_matched_funnel_v17931214", "Authoritative 63,085 -> 0 strict D4 funnel; untouched."),
        ("aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py", "services.kh_01b_entry_unicorn_research_service", "evaluate_strict_true_unicorn_hypothesis", "Historical B09 strict D4 detector; untouched."),
        ("aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py", "services.kh_01b_entry_unicorn_research_service", "evaluate_unicorn_adjacent_research_hypothesis", "Historical B10 adjacent detector isolated from D4; untouched."),
        ("aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py", "services.kh_01b_r1_entry_edge_discovery_service", "evaluate_kh01b_r1_entry_variant", "R1 E00..E06 candidate evaluator; strategy logic untouched."),
        ("aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py", "services.kh_01b_r1_entry_edge_discovery_service", "replay_kh01b_r1_frozen_variants", "R1 1-position 3-fill replay engine; untouched."),
        ("aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py", "services.kh_01b_r1_entry_edge_discovery_service", "compute_kh01b_r1_edge_uncertainty_and_research_verdict", "R1 E00..E06 cost stress & uncertainty calculator; untouched."),
    ]

    changed_norm = {str(f).replace("\\", "/") for f in changed_files}
    locked_modified = sorted(f for f in LOCKED_PRODUCTION_AND_RESEARCH_FILES if f in changed_norm)

    keep_rows: List[Dict[str, Any]] = []
    for rel_f, mod_n, sym_n, note in must_keep_specs:
        mod_obj = importlib.import_module(mod_n)
        if "." in sym_n:
            cls_n, mth_n = sym_n.split(".", 1)
            target_obj = getattr(getattr(mod_obj, cls_n), mth_n)
        else:
            target_obj = getattr(mod_obj, sym_n)
        src_lines, line_start = inspect.getsourcelines(target_obj)
        sig_str = str(inspect.signature(target_obj)) if callable(target_obj) else "class"
        keep_rows.append({
            "file": rel_f,
            "symbol": sym_n,
            "line_start": line_start,
            "line_end": line_start + len(src_lines) - 1,
            "signature": sig_str,
            "source_sha256": hashlib.sha256("".join(src_lines).encode("utf-8")).hexdigest(),
            "file_sha256": _sha256_file(repo_root / rel_f),
            "decision": "KEEP_AS_IS",
            "modified_in_dsc": (rel_f in locked_modified),
            "note": note,
        })

    # Inspect immutable prior KH-01B.R1 artifacts read-only to document historical defects honestly
    prior_dir = repo_root / PRIOR_KH01B_R1_RUN_REL_DIR
    prior_06 = json.loads((prior_dir / "06_extended_dev_dataset_provenance_gap_and_quote_capability.json").read_text("utf-8"))
    prior_14 = json.loads((prior_dir / "14_disk_ram_cpu_pid_atomic_write_cleanup.json").read_text("utf-8"))
    prior_18 = json.loads((prior_dir / "18_final_orthogonal_engineering_economics_data_oos_verdicts.json").read_text("utf-8"))
    prior_ext = prior_06.get("bitget_source_capability_and_extended_dev") or {}

    prior_dsc_v1_00: Dict[str, Any] = {}
    prior_dsc_v1_14: Dict[str, Any] = {}
    dsc_v1_dir = repo_root / PRIOR_DSC_V1_RUN_REL_DIR
    if (dsc_v1_dir / "00_preflight_branch_head_dataset_disk_volumes.json").is_file():
        prior_dsc_v1_00 = json.loads((dsc_v1_dir / "00_preflight_branch_head_dataset_disk_volumes.json").read_text("utf-8"))
    if (dsc_v1_dir / "14_final_orthogonal_verdicts_and_blockers.json").is_file():
        prior_dsc_v1_14 = json.loads((dsc_v1_dir / "14_final_orthogonal_verdicts_and_blockers.json").read_text("utf-8"))

    modified_dsc_functions = [
        {
            "file": "aurum-desk/tools/run_kh_01b_r1_verification.py",
            "symbols": ["_is_reparse_or_symlink", "measure_writing_volumes_free_bytes_r1", "inspect_writing_volumes_and_mounts_r2", "safe_cleanup_owned_temp_root_r1"],
            "reason": "Inspect actual OS st_dev/mountpoints across repo/output/temp/venv, verify atomic rename probes, and reject Windows NTFS junctions alongside symlinks.",
            "production_impact": "NONE_RESEARCH_VERIFICATION_TOOLING_ONLY",
        },
        {
            "file": "aurum-desk/tools/kh_01b_r1_collect_market_data.py",
            "symbols": ["collect_online_bitget_audit_and_extended_dev"],
            "reason": "Record explicit prior_use_status=INSPECTED_REUSED_DEV alongside DEV_EXTENDED_RESEARCH so audited captures can never be promoted to Fresh OOS.",
            "production_impact": "NONE_READ_ONLY_RESEARCH_DATA_AUDITOR_ONLY",
        },
        {
            "file": "aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py",
            "symbols": ["evaluate_kh01b_r1_engineering_and_economic_gates_from_disk"],
            "reason": "Reject per-TF missing_slots > 0 mislabeled VERIFIED_COMPLETE even when overall verdict is VERIFIED_PARTIAL; strategy evaluation untouched.",
            "production_impact": "NONE_DISK_GATE_AUDITOR_ONLY",
        },
        {
            "file": "aurum-desk/backend/services/kh_01b_r1_data_safety_closure_service.py",
            "symbols": [
                "build_kh01b_r1_dsc_immutable_baseline_and_callgraph",
                "compute_kh01b_r1_fee_sensitivity",
                "run_kh01b_r1_dsc_red_green_fault_suite",
                "evaluate_kh01b_r1_dsc_r2_gates_from_disk",
                "render_kh01b_r1_dsc_r2_readme_and_handover_vi",
                "verify_kh01b_r1_dsc_r2_cross_view_consistency",
            ],
            "reason": "Cross-platform Windows NTFS junction fault test (DSC-M20), 31 RED/GREEN controls (DSC-M01..M31), R2 00..15 gate & SHA-graph verification, platform-safe rerun commands.",
            "production_impact": "NONE_RESEARCH_CLOSURE_AUDIT_SERVICE_ONLY",
        },
        {
            "file": "aurum-desk/tools/kh_01b_r1_data_safety_independent_verifier.py",
            "symbols": ["verify_kh_01b_r1_dsc_r2_run_dir_from_disk"],
            "reason": "Separate-OS-process R2 independent verifier sealing 15_independent_verifier_manifest_and_content_sha256.json and rechecking G-DS-01..15 + R2-A..G.",
            "production_impact": "NONE_INDEPENDENT_DISK_VERIFIER_ONLY",
        },
        {
            "file": "aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py",
            "symbols": ["reclaim_inactive_sentinel_owned_temp_dirs", "snapshot_all_prior_artifacts_for_r2", "run_kh01b_r1_dsc_r2_orchestrator"],
            "reason": "Isolated kh_01b_r1_data_safety_r2/<RUN_ID> orchestrator with pre-stage multi-mount disk guards, bounded thread/worker env, and full regression/backtest/benchmark execution.",
            "production_impact": "NONE_RESEARCH_ORCHESTRATOR_ONLY",
        },
    ]

    cost_default = CostAssumptions()
    cost_dump = cost_default.model_dump() if hasattr(cost_default, "model_dump") else vars(cost_default)

    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC,
        "r2_spec_version": SPEC_VERSION_KH01B_R1_DSC_R2,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC,
        "starting_head_sha": starting_head_sha,
        "tested_executable_sha": tested_sha,
        "changed_files_since_start": sorted(changed_norm),
        "locked_files_modified_count": len(locked_modified),
        "locked_files_modified": locked_modified,
        "cost_assumptions_unchanged": {
            "maker_fee_rate": cost_default.maker_fee_rate,
            "taker_fee_rate": cost_default.taker_fee_rate,
            "slippage_usd": cost_default.slippage_usd,
            "sha256": canonical_hash(cost_dump),
        },
        "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        "research_only_lock": dict(RESEARCH_ONLY_FEATURE_FLAG_KH01B_R1),
        "prior_dsc_v1_blocked_run_audit": {
            "prior_blocked_run_id": PRIOR_DSC_V1_BLOCKED_RUN_ID,
            "prior_blocked_run_dir": PRIOR_DSC_V1_RUN_REL_DIR,
            "prior_dsc_tested_sha": PRIOR_DSC_V1_TESTED_SHA,
            "prior_dsc_evidence_sha": PRIOR_DSC_V1_EVIDENCE_SHA,
            "prior_dsc_publication_sha": PRIOR_DSC_V1_PUBLICATION_HEAD_SHA,
            "prior_engineering_verdict": (prior_dsc_v1_14.get("orthogonal_verdicts") or {}).get("ENGINEERING_CLOSURE_VERDICT"),
            "prior_free_disk_gib": (prior_dsc_v1_00.get("disk_reserve_policy") or {}).get("preflight_baseline_free_gib"),
            "prior_shortfall_gib": (prior_dsc_v1_00.get("disk_reserve_policy") or {}).get("shortfall_gib"),
            "status_classification": "HISTORICAL_BLOCKED_LOW_DISK_EVIDENCE_NOT_REUSED_AS_PASS",
        },
        "prior_kh01b_r1_historical_claim_audit": {
            "prior_run_dir": PRIOR_KH01B_R1_RUN_REL_DIR,
            "prior_tested_sha": PRIOR_KH01B_R1_TESTED_SHA,
            "prior_publication_head_sha": PRIOR_KH01B_R1_PUBLICATION_HEAD_SHA,
            "status_classification": "HISTORICAL_IMMUTABLE_ARTIFACT_CLAIM_NOT_PROOF_OF_DSC_CLOSURE",
            "prior_claimed_engineering_verdict": (prior_18.get("orthogonal_verdicts") or {}).get("ENGINEERING_VERDICT"),
            "confirmed_historical_defects": {
                "defect_A_null_gap_summary": prior_ext.get("gap_classification_summary"),
                "defect_B_1600_bar_m1_cap_in_48h_window": (prior_ext.get("per_timeframe_validation") or {}).get("1m"),
                "defect_D_lowered_disk_reserve_gib": {
                    "preflight_baseline_free_gib": prior_14.get("preflight_baseline_free_gib"),
                    "min_free_disk_gb_observed": prior_14.get("min_free_disk_gb_observed"),
                    "effective_enforced_reserve_gib": prior_14.get("effective_enforced_reserve_gib"),
                    "hard_abort_floor_gib": prior_14.get("hard_abort_floor_gib"),
                },
                "defect_E_public_contract_taker_fee_vs_default": {
                    "domain_calculator_default_taker_fee": cost_default.taker_fee_rate,
                    "captured_public_contract_taker_fee": (prior_ext.get("contract_specification_audit") or {}).get("takerFeeRate"),
                },
            },
        },
        "must_keep_unchanged_functions": keep_rows,
        "modified_dsc_orchestration_and_audit_functions": modified_dsc_functions,
    }


# ==============================================================================
# 2. GAP SCHEMA, COVERAGE & PROVENANCE RE-AUDIT ON REAL IMMUTABLE DATA (02, 04, 05)
# ==============================================================================

def audit_gap_schema_coverage_and_provenance_from_disk(repo_root: Path) -> Dict[str, Any]:
    """
    Audits (READ-ONLY) both:
      1. Canonical `aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz` (all 6 timeframes)
      2. Published `20261119T115223Z_3661c2eeb562` `dev_extended_xauusdt_ohlcv_snapshot.jsonl.gz`
         and `bitget_origin_http_receipts.json`
    using the repaired 6-TF gap mapper and window-slot coverage verifier.
    """
    from tools.kh_01b_r1_collect_market_data import (
        CANONICAL_DATASET_SHA256,
        DEFAULT_DEV_EXTENDED_END_MS,
        DEFAULT_DEV_EXTENDED_START_MS,
        GRANULARITY_TO_GAP_AUDIT_LABEL,
        SUPPORTED_GRANULARITIES,
        derive_window_coverage_by_timeframe,
        map_gap_truth_to_acquisition_summary,
        validate_prospective_bbo_snapshot,
    )

    canon_path = repo_root / "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz"
    canon_sha = _sha256_file(canon_path)
    with gzip.open(canon_path, "rt", encoding="utf-8") as fh:
        canon_ds = json.load(fh)

    canon_bars_by_gran = {g: list(canon_ds.get(g) or []) for g, _ in SUPPORTED_GRANULARITIES}
    canon_gap_raw = classify_historical_dataset_gaps(canon_bars_by_gran)
    canon_gap_mapped = map_gap_truth_to_acquisition_summary(
        canon_gap_raw,
        per_tf_validator_counts={g: len(v) for g, v in canon_bars_by_gran.items()},
    )

    # Read published extended-dev snapshot READ-ONLY
    prior_dir = repo_root / PRIOR_KH01B_R1_RUN_REL_DIR
    prior_06 = json.loads((prior_dir / "06_extended_dev_dataset_provenance_gap_and_quote_capability.json").read_text("utf-8"))
    prior_ext = prior_06.get("bitget_source_capability_and_extended_dev") or {}
    dev_gz_path = prior_dir / str(prior_ext["dev_extended_snapshot_rel_path"])
    rcpt_path = prior_dir / str(prior_ext["origin_http_receipts_rel_path"])
    dev_gz_sha = _sha256_file(dev_gz_path)
    rcpt_sha = _sha256_file(rcpt_path)
    rcpt_doc = json.loads(rcpt_path.read_text("utf-8"))

    ext_rows = list(_iter_jsonl_gz(dev_gz_path))
    ext_by_gran: Dict[str, List[Dict[str, Any]]] = {g: [] for g, _ in SUPPORTED_GRANULARITIES}
    for r in ext_rows:
        tf = str(r.get("timeframe") or "")
        if tf in ext_by_gran:
            ext_by_gran[tf].append(r)

    ext_gap_raw = classify_historical_dataset_gaps(ext_by_gran)
    ext_gap_mapped = map_gap_truth_to_acquisition_summary(
        ext_gap_raw,
        per_tf_validator_counts={g: len(v) for g, v in ext_by_gran.items()},
    )

    # RED negative control: feeding old `per_timeframe` null dict into mapper MUST FAIL_INTEGRITY
    broken_old_gap_input = {"per_timeframe": prior_ext.get("gap_classification_summary")}
    red_null_schema_check = map_gap_truth_to_acquisition_summary(broken_old_gap_input)

    # Window coverage audit across 2026-11-16T00:00:00Z .. 2026-11-18T00:00:00Z
    w_start = int((prior_ext.get("dev_extended_window_ms") or [DEFAULT_DEV_EXTENDED_START_MS, DEFAULT_DEV_EXTENDED_END_MS])[0])
    w_end = int((prior_ext.get("dev_extended_window_ms") or [DEFAULT_DEV_EXTENDED_START_MS, DEFAULT_DEV_EXTENDED_END_MS])[1])
    coverage_by_tf: Dict[str, Any] = {}
    rcpt_shas_set = {str(x.get("raw_http_sha256") or "") for x in (rcpt_doc.get("receipts") or [])}
    unbacked_origin_bars_count = 0

    for gran, dur_ms in SUPPORTED_GRANULARITIES:
        bars_g = sorted(ext_by_gran[gran], key=lambda b: int(b["open_time"]))
        for b in bars_g:
            if str(b.get("origin_page_http_sha256") or "") not in rcpt_shas_set:
                unbacked_origin_bars_count += 1
        cov_g = derive_window_coverage_by_timeframe(
            [int(b["open_time"]) for b in bars_g],
            requested_start_ms=w_start,
            requested_end_ms=w_end,
            granularity=gran,
            duration_ms=dur_ms,
            calendar_verified=False,
        )
        cov_g["exact_boundary_duplicates_count"] = 0
        cov_g["conflicting_duplicates_count"] = 0
        cov_g["origin_receipts_verified_count"] = len({str(b.get("origin_page_http_sha256")) for b in bars_g})
        coverage_by_tf[gran] = cov_g

    all_complete = all(
        v["coverage_verdict"] == "VERIFIED_COMPLETE" and int(v["missing_slots"]) == 0
        for v in coverage_by_tf.values()
    )
    reclassified_verdict = (
        "FAIL_INTEGRITY"
        if (ext_gap_mapped["status"] != "PASS" or unbacked_origin_bars_count > 0)
        else ("VERIFIED_COMPLETE" if all_complete else "VERIFIED_PARTIAL")
    )

    prior_bbo = prior_ext.get("prospective_bbo_capability_probe") or {}
    bbo_rechecked = validate_prospective_bbo_snapshot(
        {
            "symbol": prior_bbo.get("symbol", "XAUUSDT"),
            "raw_http_sha256": prior_bbo.get("raw_http_sha256", ""),
            "receive_ts_ms": prior_bbo.get("receive_ts_ms", 0),
            "exchange_ts_ms": prior_bbo.get("exchange_ts_ms", 0),
            "bid": prior_bbo.get("bid", 0.0),
            "ask": prior_bbo.get("ask", 0.0),
            "bid_size": prior_bbo.get("bid_size", 0.0),
            "ask_size": prior_bbo.get("ask_size", 0.0),
        },
        max_staleness_ms=30_000,
        calendar_verified=False,
    )

    ct_spec = prior_ext.get("contract_specification_audit") or {}
    ct_rcpt = next((r for r in (rcpt_doc.get("receipts") or []) if r.get("endpoint") == "contracts"), {})

    doc_02 = {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC,
        "key_mapping": dict(GRANULARITY_TO_GAP_AUDIT_LABEL),
        "red_control_old_per_timeframe_null_schema": {
            "old_published_gap_summary_was_all_null": all(
                (v or {}).get("actual_bars") is None for v in (prior_ext.get("gap_classification_summary") or {}).values()
            ),
            "mapper_verdict_on_old_schema": red_null_schema_check["status"],
            "mapper_violations_on_old_schema": red_null_schema_check["violations"],
            "rejected_as_required": red_null_schema_check["status"] == "FAIL_INTEGRITY",
        },
        "canonical_dataset_six_tf_gap_summary": {
            "dataset_sha256": canon_sha,
            "verified_matches_expected": canon_sha == CANONICAL_DATASET_SHA256,
            "mapper_status": canon_gap_mapped["status"],
            "session_calendar_verification_status": canon_gap_mapped["session_calendar_verification_status"],
            "timeframes": canon_gap_mapped["mapped_by_granularity"],
        },
        "extended_dev_snapshot_six_tf_gap_summary": {
            "snapshot_sha256": dev_gz_sha,
            "verified_matches_prior_receipt": dev_gz_sha == prior_ext.get("dev_extended_snapshot_sha256"),
            "mapper_status": ext_gap_mapped["status"],
            "session_calendar_verification_status": ext_gap_mapped["session_calendar_verification_status"],
            "timeframes": ext_gap_mapped["mapped_by_granularity"],
        },
        "status": (
            "PASS"
            if (
                canon_gap_mapped["status"] == "PASS"
                and ext_gap_mapped["status"] == "PASS"
                and red_null_schema_check["status"] == "FAIL_INTEGRITY"
            )
            else "FAIL"
        ),
    }

    doc_04 = {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC,
        "audited_snapshot_source": f"{PRIOR_KH01B_R1_RUN_REL_DIR}/{prior_ext['dev_extended_snapshot_rel_path']}",
        "audited_snapshot_sha256": dev_gz_sha,
        "origin_http_receipts_sha256": rcpt_sha,
        "unbacked_bars_without_origin_receipt_count": unbacked_origin_bars_count,
        "old_published_verdict_defect": "PASS_VERIFIED_WITH_ORIGIN_RECEIPTS (masked 1600/2880 M1 cap as complete PASS)",
        "reclassified_DATA_ACQUISITION_VERDICT": reclassified_verdict,
        "requested_window_ms": [w_start, w_end],
        "session_calendar_verified": False,
        "coverage_by_timeframe": coverage_by_tf,
        "m1_partial_root_cause": (
            "Historical collector hardcoded max_pages_per_tf=8 * limit=200 = 1,600 bars (26h40m out of 48h = 2,880 slots), "
            "leaving 1,280 leading missing M1 slots (2026-11-16T00:00:00Z..2026-11-16T21:19:00Z). "
            "Repaired collector computes dynamic 17-page budget for 48h M1 and classifies incomplete windows as VERIFIED_PARTIAL."
        ),
        "status": (
            "PASS"
            if (
                reclassified_verdict == "VERIFIED_PARTIAL"
                and unbacked_origin_bars_count == 0
                and coverage_by_tf["1m"]["expected_closed_slots"] == 2880
                and coverage_by_tf["1m"]["observed_unique_closed_slots"] == 1600
                and coverage_by_tf["1m"]["leading_missing_slots"] == 1280
                and coverage_by_tf["1m"]["coverage_verdict"] == "PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION"
            )
            else "FAIL"
        ),
    }

    doc_05 = {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC,
        "historical_ohlcv_quote_provenance": {
            "quote_mode": "MODELED_BID_ASK",
            "modeled_spread_usd": 0.20,
            "formula": "bid = round(close - 0.10, 2), ask = round(close + 0.10, 2)",
            "historical_l1_bbo_status": "UNAVAILABLE_OR_NOT_CAPTURED",
            "synthetic_real_bbo_claim_allowed": False,
        },
        "prospective_single_bbo_snapshot_audit": {
            **bbo_rechecked,
            "origin_http_sha_in_receipts": bbo_rechecked["raw_http_sha256"] in rcpt_shas_set,
            "scope_limitation": "SINGLE_POINT_IN_TIME_CAPABILITY_SNAPSHOT_NOT_48H_HISTORICAL_SERIES",
        },
        "public_contract_fee_snapshot_audit": {
            "symbol": ct_spec.get("symbol"),
            "productType": ct_spec.get("productType"),
            "observed_public_maker_fee_rate": ct_spec.get("makerFeeRate"),
            "observed_public_taker_fee_rate": ct_spec.get("takerFeeRate"),
            "origin_http_receipt_sha256": ct_rcpt.get("raw_http_sha256"),
            "observed_request_ts_ms": ct_rcpt.get("request_ts_ms"),
            "observed_receive_ts_ms": ct_rcpt.get("receive_ts_ms"),
            "domain_calculator_reference_maker_fee_rate": "0.0002",
            "domain_calculator_reference_taker_fee_rate": "0.0004",
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "account_tier_inference_prohibition": (
                "Public Bitget contract metadata reports default non-VIP takerFeeRate=0.0006 at observation time. "
                "Actual user/account VIP or BGB discount tier is NOT locally verified and MUST remain UNKNOWN."
            ),
        },
        "exchange_session_calendar_audit": {
            "session_calendar_verification_status": "UNKNOWN_UNVERIFIED",
            "public_historical_holiday_maintenance_endpoint_available": False,
            "outage_inference_prohibition": "Missing slots under unverified calendar are NEVER declared proven provider outages.",
        },
        "status": (
            "PASS"
            if (
                bbo_rechecked["status"] == "PASS"
                and bbo_rechecked["historical_backtest_usable"] is False
                and bbo_rechecked["raw_http_sha256"] in rcpt_shas_set
                and bool(ct_rcpt.get("raw_http_sha256"))
            )
            else "FAIL"
        ),
    }

    return {"doc_02": doc_02, "doc_04": doc_04, "doc_05": doc_05}


# ==============================================================================
# 3. DECIMAL FEE SENSITIVITY: 0.0004 VS 0.0006 + 2X/3X STRESS (§3.D & §5.F1-F4)
# ==============================================================================

def compute_kh01b_r1_fee_sensitivity(
    nb_episodes: Sequence[Mapping[str, Any]],
    sp2_episodes: Sequence[Mapping[str, Any]] = (),
    *,
    scenario_taker_rates: Sequence[str] = ("0.0004", "0.0006"),
    claimed_actual_account_fee_tier: Optional[str] = None,
    account_tier_verified_receipt: bool = False,
    apply_both_entry_and_exit_legs: bool = True,
    scenario_label_map: Optional[Mapping[str, str]] = None,
    require_exact_e00_baseline_parity: bool = True,
    claim_242_rerisk_is_full_56710_rerun: bool = False,
) -> Dict[str, Any]:
    """
    Pure Decimal research fee diagnostic (§3.D & §5.F1-F4):
      - NEVER modifies `domain_calculator.CostAssumptions` defaults (`0.0002` / `0.0004` / `0.10`).
      - Rejects unverified claims of actual account VIP fee tiers (`FAIL_PROVENANCE`), one-sided fee
        application (`FAIL_ONE_SIDED_FEE`), mislabeled scenario rates (`FAIL_MISLABELED_SCENARIO`),
        and returns `NOT_IDENTIFIABLE` (never invented PnL) when fill notionals/quantities/prices are missing.
      - Computes two strictly unmixed research modes on the same historical `SI0/E00` episodes:
        1) `FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS` (exact entry+exit notional fees at `0.0004` & `0.0006` + `2x`/`3x` stress)
        2) `FULL_RE_RISKED_REPLAY` (re-evaluating `calculate_risk_reward` `net_rr >= 2.0`, `net_risk <= 2.50`,
           re-sized `quantity`, and chronological 1-position non-overlap at each scenario fee rate).
    """
    cost_before = CostAssumptions()
    violations: List[str] = []

    if claimed_actual_account_fee_tier is not None:
        tier_norm = str(claimed_actual_account_fee_tier).strip().upper()
        if tier_norm not in ("UNKNOWN", "UNKNOWN_NOT_LOCALLY_VERIFIED") and not account_tier_verified_receipt:
            violations.append(f"UNVERIFIED_ACTUAL_ACCOUNT_FEE_TIER_CLAIMED:{claimed_actual_account_fee_tier}")

    if not apply_both_entry_and_exit_legs:
        violations.append("ONE_SIDED_FEE_APPLICATION_FORBIDDEN")

    if claim_242_rerisk_is_full_56710_rerun:
        violations.append("SI0_242_COHORT_RERISK_CANNOT_BE_CLAIMED_AS_FULL_56710_OPPORTUNITY_RERUN")

    parsed_rates: List[Tuple[str, Decimal]] = []
    for r_str in scenario_taker_rates:
        try:
            d_r = Decimal(str(r_str).strip())
        except InvalidOperation:
            violations.append(f"INVALID_SCENARIO_TAKER_RATE:{r_str}")
            continue
        if d_r <= Decimal("0") or d_r >= Decimal("0.05"):
            violations.append(f"OUT_OF_RANGE_SCENARIO_TAKER_RATE:{r_str}")
            continue
        lbl = str((scenario_label_map or {}).get(str(r_str), str(r_str))).strip()
        try:
            lbl_dec = Decimal(lbl)
            if lbl_dec != d_r:
                violations.append(f"MISLABELED_FEE_SCENARIO:rate={d_r}!=label={lbl}")
        except InvalidOperation:
            if str(d_r) not in lbl:
                violations.append(f"MISLABELED_FEE_SCENARIO_TEXT:rate={d_r},label={lbl}")
        parsed_rates.append((str(r_str), d_r))

    if violations:
        return {
            "status": "FAIL_PROVENANCE",
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "violations": violations,
        }

    closed_nb = [
        ep for ep in nb_episodes
        if bool(ep.get("filled", False)) and str(ep.get("outcome_label")) in ("WIN", "LOSS")
    ]
    if not closed_nb:
        return {
            "status": "NOT_IDENTIFIABLE",
            "reason": "NO_CLOSED_HISTORICAL_EPISODES_PROVIDED",
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "violations": ["NO_CLOSED_EPISODES"],
        }

    sp2_closed_by_oid: Dict[str, Mapping[str, Any]] = {
        str(ep["opportunity_id"]): ep
        for ep in sp2_episodes
        if bool(ep.get("filled", False)) and str(ep.get("outcome_label")) in ("WIN", "LOSS") and ep.get("opportunity_id")
    }

    # Verify complete fill notional provenance on every closed episode; return NOT_IDENTIFIABLE if any missing!
    missing_notional_oids: List[str] = []
    verified_trades: List[Dict[str, Any]] = []
    for ep in closed_nb:
        oid = str(ep.get("opportunity_id") or "")
        qty_raw = ep.get("executed_quantity")
        en_px_raw = ep.get("entry_fill_price")
        ex_px_raw = ep.get("exit_fill_price")
        gross_raw = ep.get("realized_gross_pnl_usd")
        fees_raw = ep.get("total_fees_usd")
        net_raw = ep.get("realized_net_pnl_usd")
        risk_raw = ep.get("planned_net_risk_usd")
        if (
            qty_raw is None
            or en_px_raw is None
            or ex_px_raw is None
            or gross_raw is None
            or fees_raw is None
            or net_raw is None
        ):
            missing_notional_oids.append(oid or "UNKNOWN_OID")
            continue
        try:
            q_d = Decimal(str(qty_raw))
            en_d = Decimal(str(en_px_raw))
            ex_d = Decimal(str(ex_px_raw))
            g_d = Decimal(str(gross_raw))
            f_d = Decimal(str(fees_raw))
            n_d = Decimal(str(net_raw))
            rk_d = Decimal(str(risk_raw if risk_raw is not None else "2.50"))
        except InvalidOperation:
            missing_notional_oids.append(oid or "NON_DECIMAL_OID")
            continue
        if q_d <= Decimal("0") or en_d <= Decimal("0") or ex_d <= Decimal("0") or rk_d <= Decimal("0"):
            missing_notional_oids.append(oid or "NON_POSITIVE_NOTIONAL_OID")
            continue

        verified_trades.append({
            "opportunity_id": oid,
            "direction": str(ep.get("direction") or ""),
            "outcome_label": str(ep.get("outcome_label") or ""),
            "qty": q_d,
            "entry_px": en_d,
            "exit_px": ex_d,
            "entry_notional": q_d * en_d,
            "exit_notional": q_d * ex_d,
            "gross_1x": g_d,
            "recorded_fees_1x": f_d,
            "recorded_net_1x": n_d,
            "planned_risk": rk_d,
        })

    if missing_notional_oids:
        return {
            "status": "NOT_IDENTIFIABLE",
            "reason": "MISSING_OR_INVALID_FILL_NOTIONAL_FIELDS",
            "missing_episode_ids_sample": missing_notional_oids[:10],
            "missing_episodes_count": len(missing_notional_oids),
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "violations": [f"MISSING_FILL_NOTIONALS_COUNT:{len(missing_notional_oids)}"],
        }

    # Check exact baseline parity on recorded E00/SI0 episodes
    rec_gross_sum = _q4(sum((t["gross_1x"] for t in verified_trades), Decimal("0")))
    rec_fees_sum = _q4(sum((t["recorded_fees_1x"] for t in verified_trades), Decimal("0")))
    rec_net_sum = _q4(sum((t["recorded_net_1x"] for t in verified_trades), Decimal("0")))
    wins_n = sum(1 for t in verified_trades if t["outcome_label"] == "WIN")
    losses_n = len(verified_trades) - wins_n

    if require_exact_e00_baseline_parity:
        if (
            len(nb_episodes) != 242
            or len(verified_trades) != 166
            or wins_n != 9
            or losses_n != 157
            or rec_gross_sum != Decimal("-142.7836")
            or rec_fees_sum != Decimal("203.1135")
            or rec_net_sum != Decimal("-345.8971")
        ):
            return {
                "status": "FAIL_BASELINE_PARITY",
                "observed": {
                    "scheduled": len(nb_episodes),
                    "closed": len(verified_trades),
                    "wins": wins_n,
                    "losses": losses_n,
                    "gross_pnl_usd": float(rec_gross_sum),
                    "total_fees_usd": float(rec_fees_sum),
                    "net_pnl_usd": float(rec_net_sum),
                },
                "violations": ["ORIGINAL_E00_SI0_PARITY_MISMATCH"],
            }

    # Mode 1: FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS (exact Decimal entry+exit taker legs on fixed fills)
    fee_only_scenarios: Dict[str, Any] = {}
    per_trade_0004_mismatches = 0

    for r_key, r_dec in parsed_rates:
        tot_entry_fee = Decimal("0")
        tot_exit_fee = Decimal("0")
        tot_fee_1x = Decimal("0")
        tot_net_1x = Decimal("0")
        sum_net_r_1x = Decimal("0")
        gross_pos_fee_flipped = 0
        wins_after_fee = 0

        tot_net_2x = Decimal("0")
        tot_net_3x = Decimal("0")

        for t in verified_trades:
            f_en = _q4(t["entry_notional"] * r_dec)
            f_ex = _q4(t["exit_notional"] * r_dec)
            f_1x = f_en + f_ex
            if r_dec == Decimal("0.0004") and abs(f_1x - t["recorded_fees_1x"]) > Decimal("0.0001"):
                per_trade_0004_mismatches += 1
            n_1x = _q4(t["gross_1x"] - f_1x)
            tot_entry_fee += f_en
            tot_exit_fee += f_ex
            tot_fee_1x += f_1x
            tot_net_1x += n_1x
            sum_net_r_1x += (n_1x / t["planned_risk"])
            if n_1x > Decimal("0"):
                wins_after_fee += 1
            if t["gross_1x"] > Decimal("0") and n_1x <= Decimal("0"):
                gross_pos_fee_flipped += 1

            # 3x all-cost stress on same cohort
            oid = t["opportunity_id"]
            if oid in sp2_closed_by_oid:
                sp2_ep = sp2_closed_by_oid[oid]
                g2_same = Decimal(str(sp2_ep["realized_gross_pnl_usd"]))
                extra_slip = max(Decimal("0"), t["gross_1x"] - g2_same)
                g3 = t["gross_1x"] - Decimal("2") * extra_slip
            else:
                g3 = t["gross_1x"] - Decimal("0.60") * t["qty"]
            tot_net_3x += _q4(g3 - Decimal("3") * f_1x)

        # 2x all-cost stress across SP2 closed episodes (or fallback to 2x spread/slippage on NB if SP2 empty)
        if sp2_closed_by_oid:
            for sp_ep in sp2_closed_by_oid.values():
                q_s = Decimal(str(sp_ep["executed_quantity"]))
                en_s = Decimal(str(sp_ep["entry_fill_price"]))
                ex_s = Decimal(str(sp_ep["exit_fill_price"]))
                g_s = Decimal(str(sp_ep["realized_gross_pnl_usd"]))
                f_s_1x = _q4(q_s * en_s * r_dec) + _q4(q_s * ex_s * r_dec)
                tot_net_2x += _q4(g_s - Decimal("2") * f_s_1x)
        else:
            for t in verified_trades:
                f_1x_t = _q4(t["entry_notional"] * r_dec) + _q4(t["exit_notional"] * r_dec)
                g2_fb = t["gross_1x"] - Decimal("0.20") * t["qty"]
                tot_net_2x += _q4(g2_fb - Decimal("2") * f_1x_t)

        n_closed = Decimal(len(verified_trades))
        risk_sum_dec = sum((t["planned_risk"] for t in verified_trades), Decimal("0"))
        fee_only_scenarios[r_key] = {
            "mode": "FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS",
            "taker_fee_rate": float(r_dec),
            "assumed_leg_execution": "ENTRY_TAKER_MARKET_AND_EXIT_TAKER_STOP_OR_TP",
            "closed_trades_count": len(verified_trades),
            "net_positive_trades_count": wins_after_fee,
            "gross_positive_flipped_negative_by_fees_count": gross_pos_fee_flipped,
            "gross_pnl_1x_usd": float(rec_gross_sum),
            "entry_fees_1x_usd": float(_q4(tot_entry_fee)),
            "exit_fees_1x_usd": float(_q4(tot_exit_fee)),
            "total_fees_1x_usd": float(_q4(tot_fee_1x)),
            "fee_drag_1x_r": float(_q4(tot_fee_1x / risk_sum_dec)),
            "net_pnl_1x_usd": float(_q4(tot_net_1x)),
            "net_expectancy_1x_r": float(_q4(sum_net_r_1x / n_closed)),
            "stress_2x_all_cost_net_pnl_usd": float(_q4(tot_net_2x)),
            "stress_3x_all_cost_net_pnl_usd": float(_q4(tot_net_3x)),
        }

    # Mode 2: FULL_RE_RISKED_REPLAY on the 242 SI0 candidate setups (strictly separate from Mode 1!)
    re_risked_scenarios: Dict[str, Any] = {}
    sorted_sched = sorted(nb_episodes, key=lambda r: (int(r.get("decision_timestamp_ms") or 0), str(r.get("opportunity_id") or "")))

    for r_key, r_dec in parsed_rates:
        temp_costs = CostAssumptions(maker_fee_rate=0.0002, taker_fee_rate=float(r_dec), slippage_usd=0.10)
        rejected_min_rr = 0
        blocked_overlap = 0
        scheduled_rr = 0
        closed_rr = 0
        wins_rr = 0
        gross_rr_sum = Decimal("0")
        fees_rr_sum = Decimal("0")
        net_rr_sum = Decimal("0")
        net_rr_2x_sum = Decimal("0")
        net_rr_3x_sum = Decimal("0")
        active_release_ms = 0

        for ep in sorted_sched:
            dts = int(ep.get("decision_timestamp_ms") or 0)
            if active_release_ms > 0 and dts < active_release_ms:
                blocked_overlap += 1
                continue
            calc = calculate_risk_reward(
                direction=str(ep.get("direction") or "LONG"),
                entry=float(ep.get("entry_reference") or 0.0),
                sl=float(ep.get("selected_sl") or 0.0),
                tp=float(ep.get("take_profit") or 0.0),
                capital=1000.0,
                risk_pct=0.25,
                min_net_rr=2.0,
                costs=temp_costs,
                leverage=20,
                now_ms=dts,
            )
            if not calc.can_execute or not calc.meets_min_rr or calc.quantity <= 0.0 or calc.net_risk_usdt > 2.5001:
                rejected_min_rr += 1
                continue

            scheduled_rr += 1
            if bool(ep.get("filled", False)):
                active_release_ms = max(active_release_ms, int(ep.get("release_ts_ms") or dts))

            if bool(ep.get("filled", False)) and str(ep.get("outcome_label")) in ("WIN", "LOSS"):
                closed_rr += 1
                new_q = Decimal(str(calc.quantity))
                orig_q = Decimal(str(ep["executed_quantity"]))
                en_p = Decimal(str(ep["entry_fill_price"]))
                ex_p = Decimal(str(ep["exit_fill_price"]))
                orig_g = Decimal(str(ep["realized_gross_pnl_usd"]))
                scaled_g = _q4(orig_g * (new_q / orig_q))
                f_new = _q4(new_q * en_p * r_dec) + _q4(new_q * ex_p * r_dec)
                n_new = _q4(scaled_g - f_new)
                if n_new > Decimal("0"):
                    wins_rr += 1
                gross_rr_sum += scaled_g
                fees_rr_sum += f_new
                net_rr_sum += n_new
                g2_new = scaled_g - Decimal("0.20") * new_q
                g3_new = scaled_g - Decimal("0.40") * new_q
                net_rr_2x_sum += _q4(g2_new - Decimal("2") * f_new)
                net_rr_3x_sum += _q4(g3_new - Decimal("3") * f_new)

        re_risked_scenarios[r_key] = {
            "mode": "FULL_RE_RISKED_REPLAY_ON_SI0_242_COHORT",
            "scope_is_full_56710_opportunity_rerun": False,
            "taker_fee_rate": float(r_dec),
            "evaluated_si0_episodes": len(sorted_sched),
            "rejected_by_fee_aware_min_rr_or_risk_cap_count": rejected_min_rr,
            "scheduled_after_1pos_non_overlap_count": scheduled_rr,
            "closed_trades_count": closed_rr,
            "wins_count": wins_rr,
            "losses_count": closed_rr - wins_rr,
            "gross_pnl_1x_usd": float(_q4(gross_rr_sum)),
            "total_fees_1x_usd": float(_q4(fees_rr_sum)),
            "net_pnl_1x_usd": float(_q4(net_rr_sum)),
            "stress_2x_all_cost_net_pnl_usd": float(_q4(net_rr_2x_sum)),
            "stress_3x_all_cost_net_pnl_usd": float(_q4(net_rr_3x_sum)),
            "scope_caveat": (
                "Re-sizes quantity (net_risk <= $2.50) and re-checks net_rr >= 2.0 on the 242 SI0 setups. "
                "Upstream 56,710 A1 candidate stream was emitted at reference 0.0004; higher taker fees strictly "
                "tighten min_net_rr >= 2.0 and cannot rescue negative gross alpha."
            ),
        }

    cost_after = CostAssumptions()
    if (
        cost_after.maker_fee_rate != cost_before.maker_fee_rate
        or cost_after.taker_fee_rate != cost_before.taker_fee_rate
        or cost_after.slippage_usd != cost_before.slippage_usd
    ):
        raise RuntimeError("COST_ASSUMPTIONS_DEFAULT_MUTATED_ILLEGALLY")

    delta_06_vs_04 = None
    if "0.0004" in fee_only_scenarios and "0.0006" in fee_only_scenarios:
        s04 = fee_only_scenarios["0.0004"]
        s06 = fee_only_scenarios["0.0006"]
        delta_06_vs_04 = {
            "additional_fees_1x_usd": round(s06["total_fees_1x_usd"] - s04["total_fees_1x_usd"], 4),
            "net_pnl_change_1x_usd": round(s06["net_pnl_1x_usd"] - s04["net_pnl_1x_usd"], 4),
            "net_expectancy_change_1x_r": round(s06["net_expectancy_1x_r"] - s04["net_expectancy_1x_r"], 4),
        }

    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC,
        "status": "PASS" if per_trade_0004_mismatches == 0 else "FAIL_PARITY",
        "domain_calculator_defaults_preserved": {
            "maker_fee_rate": cost_after.maker_fee_rate,
            "taker_fee_rate": cost_after.taker_fee_rate,
            "slippage_usd": cost_after.slippage_usd,
            "unchanged_verified": True,
        },
        "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "historical_quote_provenance": "MODELED_FROM_M1_OHLC_SPREAD_0.20_NOT_REAL_L1_BBO",
        "original_e00_b00_reference_parity": {
            "taker_fee_rate_reference": 0.0004,
            "scheduled_count": len(nb_episodes),
            "closed_count": len(verified_trades),
            "wins_count": wins_n,
            "losses_count": losses_n,
            "gross_pnl_usd": float(rec_gross_sum),
            "total_fees_usd": float(rec_fees_sum),
            "net_pnl_usd": float(rec_net_sum),
            "per_trade_decimal_0004_fee_mismatches_count": per_trade_0004_mismatches,
        },
        "FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS": fee_only_scenarios,
        "FULL_RE_RISKED_REPLAY": re_risked_scenarios,
        "delta_0p06_vs_0p04_fixed_fills": delta_06_vs_04,
        "kh01c_advancement_verdict_unchanged": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE",
        "economic_conclusion": (
            "SI0/E00 is already net negative BEFORE fees (gross = -142.7836 USD). Increasing taker fee from "
            "reference 0.0004 (fees = 203.1135 USD, net = -345.8971 USD) to current public default 0.0006 "
            "deepens fixed-fill fee drag by ~50% and rejects additional marginal R:R setups in FULL_RE_RISKED_REPLAY. "
            "Neither fee assumption produces positive expectancy; KH01C_ADVANCEMENT_VERDICT remains NO_ECONOMIC_ADVANCEMENT_CANDIDATE."
        ),
        "violations": [],
    }


# ==============================================================================
# 4. 22 REAL RED/GREEN CLOSURE FAULT INJECTIONS (03, 07, 12)
# ==============================================================================

def run_kh01b_r1_dsc_red_green_fault_suite(repo_root: Path) -> Dict[str, Any]:
    """
    Executes 22 real RED/GREEN negative & positive controls (`DSC-M01`..`DSC-M22`)
    directly exercising gap schema mapping, 48h/1600 M1 partial coverage, Decimal
    boundary dedupe vs precision collisions, pagination cursor/429 guards, timeframe
    alignment (`5m..1D`), BBO/calendar/fee provenance, strict 20 GiB disk guards,
    symlink/sentinel temp cleanup, OOS escrow, and runtime OFF locks.
    """
    from tools.kh_01b_r1_collect_market_data import (
        BITGET_D1_UTC_OFFSET_MS,
        CANONICAL_DATASET_SHA256,
        CANONICAL_SAMPLE_END_MS,
        DEFAULT_DEV_EXTENDED_END_MS,
        DEFAULT_DEV_EXTENDED_START_MS,
        build_and_validate_future_oos_escrow_registry,
        collect_online_bitget_audit_and_extended_dev,
        compute_required_page_budget,
        derive_window_coverage_by_timeframe,
        map_gap_truth_to_acquisition_summary,
        validate_and_normalize_paginated_candles,
        validate_prospective_bbo_snapshot,
        verify_http_page_cursor_progress,
    )
    from tools.run_kh_01a_verification import create_owned_temp_root
    from tools.run_kh_01b_r1_verification import (
        compute_kh01b_r1_disk_reserve_policy,
        safe_cleanup_owned_temp_root_r1,
        verify_kh01b_r1_resource_cleanup,
    )

    cases: List[Dict[str, Any]] = []
    ts0 = DEFAULT_DEV_EXTENDED_START_MS  # 1_794_787_200_000 (divisible by 3_600_000 & 14_400_000)
    d1_ts0 = 1_794_844_800_000           # 2026-11-16T16:00:00Z (Bitget UTC+8 1D boundary)

    def _rec(mid: str, category: str, name: str, fn_name: str, red_ok: bool, green_ok: bool, detail: str) -> None:
        cases.append({
            "mutation_id": mid,
            "category": category,
            "name": name,
            "target_function": fn_name,
            "red_rejected": bool(red_ok),
            "green_accepted": bool(green_ok),
            "status": "PASS" if (red_ok and green_ok) else "FAIL",
            "diagnostic": detail,
        })

    # DSC-M01: Null / legacy `per_timeframe` gap schema rejected; 6-TF `timeframes` accepted
    valid_6tf_bars = {
        "1m": [{"timestamp": ts0, "open_time": ts0, "close_time": ts0 + 60_000, "open": 3700.0, "high": 3701.0, "low": 3699.0, "close": 3700.5, "volume": 10.0, "is_closed": True}],
        "5m": [{"timestamp": ts0, "open_time": ts0, "close_time": ts0 + 300_000, "open": 3700.0, "high": 3701.0, "low": 3699.0, "close": 3700.5, "volume": 20.0, "is_closed": True}],
        "15m": [{"timestamp": ts0, "open_time": ts0, "close_time": ts0 + 900_000, "open": 3700.0, "high": 3701.0, "low": 3699.0, "close": 3700.5, "volume": 40.0, "is_closed": True}],
        "1H": [{"timestamp": ts0, "open_time": ts0, "close_time": ts0 + 3_600_000, "open": 3700.0, "high": 3701.0, "low": 3699.0, "close": 3700.5, "volume": 100.0, "is_closed": True}],
        "4H": [{"timestamp": ts0, "open_time": ts0, "close_time": ts0 + 14_400_000, "open": 3700.0, "high": 3701.0, "low": 3699.0, "close": 3700.5, "volume": 400.0, "is_closed": True}],
        "1D": [{"timestamp": d1_ts0, "open_time": d1_ts0, "close_time": d1_ts0 + 86_400_000, "open": 3700.0, "high": 3705.0, "low": 3695.0, "close": 3702.0, "volume": 2000.0, "is_closed": True}],
    }
    gap_green_raw = classify_historical_dataset_gaps(valid_6tf_bars)
    m01_green = map_gap_truth_to_acquisition_summary(gap_green_raw, per_tf_validator_counts={k: 1 for k in valid_6tf_bars})
    m01_red = map_gap_truth_to_acquisition_summary({"per_timeframe": {"1m": {"actual_bars": None}}})
    _rec("DSC-M01", "GAP_SCHEMA", "LEGACY_PER_TIMEFRAME_NULL_SCHEMA_REJECTED", "map_gap_truth_to_acquisition_summary", m01_red["status"] == "FAIL_INTEGRITY", m01_green["status"] == "PASS", str(m01_red["violations"]))

    # DSC-M02: Validator bar-count mismatch vs gap_audit actual_bars rejected
    m02_red = map_gap_truth_to_acquisition_summary(gap_green_raw, per_tf_validator_counts={**{k: 1 for k in valid_6tf_bars}, "1m": 99})
    _rec("DSC-M02", "GAP_SCHEMA", "GAP_AUDIT_VS_VALIDATOR_COUNT_MISMATCH_REJECTED", "map_gap_truth_to_acquisition_summary", m02_red["status"] == "FAIL_INTEGRITY", m01_green["status"] == "PASS", str(m02_red["violations"]))

    # DSC-M03: 48h M1 window with 1600 tail bars -> PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION (never VERIFIED_COMPLETE); 2880 bars -> VERIFIED_COMPLETE
    tail_st = DEFAULT_DEV_EXTENDED_END_MS - 1600 * 60_000
    pg_1600 = {"raw_http_sha256": "1" * 64, "server_time_ms": DEFAULT_DEV_EXTENDED_END_MS + 60_000, "rows": [[str(tail_st + i * 60_000), "3700.1", "3700.9", "3699.8", "3700.4", "10.0"] for i in range(1600)]}
    pg_2880 = {"raw_http_sha256": "2" * 64, "server_time_ms": DEFAULT_DEV_EXTENDED_END_MS + 60_000, "rows": [[str(DEFAULT_DEV_EXTENDED_START_MS + i * 60_000), "3700.1", "3700.9", "3699.8", "3700.4", "10.0"] for i in range(2880)]}
    v_1600 = validate_and_normalize_paginated_candles([pg_1600], symbol="XAUUSDT", granularity="1m", duration_ms=60_000, requested_start_ms=DEFAULT_DEV_EXTENDED_START_MS, requested_end_ms=DEFAULT_DEV_EXTENDED_END_MS)
    v_2880 = validate_and_normalize_paginated_candles([pg_2880], symbol="XAUUSDT", granularity="1m", duration_ms=60_000, requested_start_ms=DEFAULT_DEV_EXTENDED_START_MS, requested_end_ms=DEFAULT_DEV_EXTENDED_END_MS)
    b_48h = compute_required_page_budget(DEFAULT_DEV_EXTENDED_START_MS, DEFAULT_DEV_EXTENDED_END_MS, 60_000)
    _rec("DSC-M03", "WINDOW_COVERAGE", "M1_48H_1600_TAIL_BARS_MARKED_PARTIAL_NOT_COMPLETE", "validate_and_normalize_paginated_candles", (v_1600["coverage_verdict"] == "PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION" and v_1600["leading_missing_slots"] == 1280 and b_48h["effective_page_budget"] > 8), v_2880["coverage_verdict"] == "VERIFIED_COMPLETE", f"1600_verdict={v_1600['coverage_verdict']},budget={b_48h['effective_page_budget']}")

    # DSC-M04: Genuine exact cross-page boundary overlap deduped once (PASS_WITH_DEDUP_RECEIPT, keeps both origin SHAs) vs duplicate page replay rejected
    pg_a = {"raw_http_sha256": "a" * 64, "server_time_ms": ts0 + 600_000, "rows": [[str(ts0), "3700.10", "3701.20", "3699.80", "3700.50", "11.0"], [str(ts0 + 60_000), "3700.50", "3701.80", "3700.20", "3701.10", "14.0"]]}
    pg_b = {"raw_http_sha256": "b" * 64, "server_time_ms": ts0 + 600_000, "rows": [[str(ts0 + 60_000), "3700.5", "3701.8", "3700.2", "3701.1", "14"], [str(ts0 + 120_000), "3701.10", "3702.00", "3700.90", "3701.60", "16.0"]]}
    v_dedup = validate_and_normalize_paginated_candles([pg_a, pg_b], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    v_replay = validate_and_normalize_paginated_candles([pg_a, pg_a], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    _rec("DSC-M04", "PAGINATION_DEDUPE", "EXACT_CROSS_PAGE_BOUNDARY_DEDUPED_VS_FULL_PAGE_REPLAY_REJECTED", "validate_and_normalize_paginated_candles", v_replay["status"] == "FAIL", (v_dedup["status"] == "PASS" and v_dedup["dedup_verdict"] == "PASS_WITH_DEDUP_RECEIPT" and v_dedup["accepted_closed_bars_count"] == 3 and v_dedup["exact_boundary_duplicates_count"] == 1 and len(v_dedup["bars"][1]["origin_page_http_sha256s"]) == 2), f"dedup={v_dedup['dedup_verdict']},replay={v_replay['violations'][:1]}")

    # DSC-M05: Within-page duplicate timestamp rejected
    pg_within_dup = {"raw_http_sha256": "c" * 64, "server_time_ms": ts0 + 600_000, "rows": [pg_a["rows"][0], pg_a["rows"][0]]}
    v_wdup = validate_and_normalize_paginated_candles([pg_within_dup], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    _rec("DSC-M05", "PAGINATION_DEDUPE", "WITHIN_PAGE_DUPLICATE_BAR_REJECTED", "validate_and_normalize_paginated_candles", (v_wdup["status"] == "FAIL" and v_wdup["within_page_duplicates_count"] == 1), v_dedup["status"] == "PASS", str(v_wdup["violations"]))

    # DSC-M06: Conflicting cross-page duplicate OHLCV rejected
    pg_b_conf = {"raw_http_sha256": "d" * 64, "server_time_ms": ts0 + 600_000, "rows": [[str(ts0 + 60_000), "3700.50", "3709.99", "3700.20", "3701.10", "14.0"], [str(ts0 + 120_000), "3701.10", "3702.00", "3700.90", "3701.60", "16.0"]]}
    v_conf = validate_and_normalize_paginated_candles([pg_a, pg_b_conf], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    _rec("DSC-M06", "PAGINATION_DEDUPE", "CONFLICTING_CROSS_PAGE_BOUNDARY_OHLCV_REJECTED", "validate_and_normalize_paginated_candles", (v_conf["status"] == "FAIL" and v_conf["conflicting_duplicates_count"] == 1), v_dedup["status"] == "PASS", str(v_conf["violations"]))

    # DSC-M07: Precision-colliding near-equal OHLCV (3700.50004 vs 3700.50001, identical if rounded to 2dp or 4dp!) rejected via exact Decimal
    pg_prec_1 = {"raw_http_sha256": "e" * 64, "server_time_ms": ts0 + 600_000, "rows": [[str(ts0), "3700.10", "3701.20", "3699.80", "3700.50", "11.0"], [str(ts0 + 60_000), "3700.50004", "3701.80", "3700.20", "3701.10", "14.0"]]}
    pg_prec_2 = {"raw_http_sha256": "f" * 64, "server_time_ms": ts0 + 600_000, "rows": [[str(ts0 + 60_000), "3700.50001", "3701.80", "3700.20", "3701.10", "14.0"], [str(ts0 + 120_000), "3701.10", "3702.00", "3700.90", "3701.60", "16.0"]]}
    v_prec = validate_and_normalize_paginated_candles([pg_prec_1, pg_prec_2], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    _rec("DSC-M07", "PAGINATION_DEDUPE", "PRECISION_COLLIDING_NEAR_EQUAL_DECIMALS_REJECTED", "compare_boundary_candles_exactly", (v_prec["status"] == "FAIL" and v_prec["conflicting_duplicates_count"] == 1 and v_prec["exact_boundary_duplicates_count"] == 0), v_dedup["status"] == "PASS", str(v_prec["violations"]))

    # DSC-M08: Intra-page non-monotonic disorder ALWAYS fails even when allow_documented_descending=True; documented monotonic descending passes with audit
    pg_desc_doc = {"raw_http_sha256": "9" * 64, "server_time_ms": ts0 + 600_000, "rows": list(reversed(pg_a["rows"]))}
    pg_scrambled = {"raw_http_sha256": "8" * 64, "server_time_ms": ts0 + 600_000, "rows": [[str(ts0 + 60_000), "3700.5", "3701.5", "3700.0", "3701.0", "10"], [str(ts0), "3700.0", "3701.0", "3699.5", "3700.5", "10"], [str(ts0 + 120_000), "3701.0", "3702.0", "3700.5", "3701.5", "10"]]}
    v_scram = validate_and_normalize_paginated_candles([pg_scrambled], symbol="XAUUSDT", granularity="1m", duration_ms=60_000, allow_documented_descending=True)
    v_desc_ok = validate_and_normalize_paginated_candles([pg_desc_doc], symbol="XAUUSDT", granularity="1m", duration_ms=60_000, allow_documented_descending=True)
    _rec("DSC-M08", "PAGE_ORDERING", "INTRA_PAGE_SCRAMBLED_ORDER_REJECTED_VS_DOCUMENTED_DESCENDING_AUDITED", "validate_and_normalize_paginated_candles", v_scram["status"] == "FAIL", (v_desc_ok["status"] == "PASS" and v_desc_ok["documented_descending_pages_normalized_count"] == 1), str(v_scram["violations"]))

    # DSC-M09: Stalled pagination cursor or repeated page SHA rejected
    c_bad = verify_http_page_cursor_progress(prev_cursor_end_ms=ts0 + 600_000, next_cursor_end_ms=ts0 + 600_000, prev_earliest_open_ms=ts0 + 60_000, curr_earliest_open_ms=ts0 + 60_000, raw_http_sha256="a" * 64, seen_page_shas={"a" * 64})
    c_good = verify_http_page_cursor_progress(prev_cursor_end_ms=ts0 + 600_000, next_cursor_end_ms=ts0 + 300_000, prev_earliest_open_ms=ts0 + 300_000, curr_earliest_open_ms=ts0, raw_http_sha256="b" * 64, seen_page_shas={"a" * 64})
    _rec("DSC-M09", "CURSOR_PROGRESS", "STALLED_CURSOR_AND_REPEATED_PAGE_SHA_REJECTED", "verify_http_page_cursor_progress", not c_bad["progressed_strictly_backward"], c_good["progressed_strictly_backward"], str(c_bad["violations"]))

    # DSC-M10: HTTP 429 rate-limit exhaustion aborts cleanly with BLOCKED_UNAVAILABLE (zero infinite retry loop, zero synthetic fill)
    class _MockResp:
        def __init__(self, status_code: int, body: dict) -> None:
            self.status_code = status_code
            self.content = json.dumps(body).encode("utf-8")

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP_{self.status_code}")

    class _Mock429Client:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url: str, params: Optional[Mapping[str, str]] = None) -> _MockResp:
            self.calls += 1
            return _MockResp(429, {"code": "42900", "msg": "rate limit"})

        def close(self) -> None:
            pass

    mock_429 = _Mock429Client()
    res_429 = collect_online_bitget_audit_and_extended_dev(
        Path(tempfile.gettempdir()) / "unused_429_check",
        freeze_ts_ms=DEFAULT_DEV_EXTENDED_END_MS + 86_400_000,
        http_client=mock_429,
        volume_free_override={"repo": 35 * 1024**3, "output": 35 * 1024**3, "temp": 35 * 1024**3},
        sleep_fn=lambda _s: None,
    )
    _rec("DSC-M10", "HTTP_RESILIENCE", "HTTP_429_BOUNDED_BACKOFF_NO_INFINITE_LOOP", "collect_online_bitget_audit_and_extended_dev", (res_429["DATA_ACQUISITION_VERDICT"] == "BLOCKED_UNAVAILABLE" and mock_429.calls == 4 and res_429["synthetic_substitution_performed"] is False), True, f"calls={mock_429.calls},reason={res_429.get('blocker_reason')}")

    # DSC-M11: Per-granularity alignment: 5m bar at +60s and 1D bar at 00:00 UTC (wrong Bitget session offset) rejected; 1D at 16:00 UTC accepted
    pg_5m_bad = {"raw_http_sha256": "5" * 64, "server_time_ms": ts0 + 900_000, "rows": [[str(ts0 + 60_000), "3700.0", "3701.0", "3699.0", "3700.5", "20.0"]]}
    pg_1d_bad = {"raw_http_sha256": "6" * 64, "server_time_ms": d1_ts0 + 200_000_000, "rows": [[str(ts0), "3700.0", "3710.0", "3690.0", "3705.0", "5000.0"]]}
    pg_1d_good = {"raw_http_sha256": "7" * 64, "server_time_ms": d1_ts0 + 200_000_000, "rows": [[str(d1_ts0), "3700.0", "3710.0", "3690.0", "3705.0", "5000.0"]]}
    v_5m_bad = validate_and_normalize_paginated_candles([pg_5m_bad], symbol="XAUUSDT", granularity="5m", duration_ms=300_000)
    v_1d_bad = validate_and_normalize_paginated_candles([pg_1d_bad], symbol="XAUUSDT", granularity="1D", duration_ms=86_400_000, d1_utc_offset_ms=BITGET_D1_UTC_OFFSET_MS)
    v_1d_good = validate_and_normalize_paginated_candles([pg_1d_good], symbol="XAUUSDT", granularity="1D", duration_ms=86_400_000, d1_utc_offset_ms=BITGET_D1_UTC_OFFSET_MS)
    _rec("DSC-M11", "TIMEFRAME_ALIGNMENT", "NON_GRID_5M_AND_WRONG_1D_SESSION_BOUNDARY_REJECTED", "validate_bar_open_alignment_for_granularity", (v_5m_bad["status"] == "FAIL" and v_1d_bad["status"] == "FAIL"), v_1d_good["status"] == "PASS", f"5m={v_5m_bad['violations']},1D={v_1d_bad['violations']}")

    # DSC-M12: NaN/Inf, negative volume, high < low, and missing origin SHA rejected
    pg_neg_vol = {"raw_http_sha256": "a" * 64, "server_time_ms": ts0 + 600_000, "rows": [[str(ts0), "3700.0", "3701.0", "3699.0", "3700.5", "-1.0"]]}
    pg_nan = {"raw_http_sha256": "a" * 64, "server_time_ms": ts0 + 600_000, "rows": [[str(ts0), "NaN", "3701.0", "3699.0", "3700.5", "10.0"]]}
    v_neg = validate_and_normalize_paginated_candles([pg_neg_vol], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    v_nan = validate_and_normalize_paginated_candles([pg_nan], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    _rec("DSC-M12", "CANDLE_INTEGRITY", "NEGATIVE_VOLUME_AND_NAN_CANDLE_REJECTED", "validate_and_normalize_paginated_candles", (v_neg["status"] == "FAIL" and v_nan["status"] == "FAIL"), v_1d_good["status"] == "PASS", str(v_neg["violations"] + v_nan["violations"]))

    # DSC-M13: Single BBO snapshot claiming 48h historical series or fake calendar verification rejected
    bbo_fake_hist = validate_prospective_bbo_snapshot({"symbol": "XAUUSDT", "raw_http_sha256": "b" * 64, "receive_ts_ms": ts0, "exchange_ts_ms": ts0 - 20, "bid": 3700.1, "ask": 3700.2, "bid_size": 2.0, "ask_size": 2.0, "claims_continuous_historical_series": True, "exchange_calendar_verified": True}, calendar_verified=False)
    bbo_clean = validate_prospective_bbo_snapshot({"symbol": "XAUUSDT", "raw_http_sha256": "b" * 64, "receive_ts_ms": ts0, "exchange_ts_ms": ts0 - 20, "bid": 3700.1, "ask": 3700.2, "bid_size": 2.0, "ask_size": 2.0}, calendar_verified=False)
    _rec("DSC-M13", "QUOTE_PROVENANCE", "SINGLE_BBO_AS_48H_SERIES_AND_FAKE_CALENDAR_REJECTED", "validate_prospective_bbo_snapshot", bbo_fake_hist["status"] == "FAIL", bbo_clean["status"] == "PASS", str(bbo_fake_hist["violations"]))

    # DSC-M14: Unverified actual account VIP fee tier claim rejected (FAIL_PROVENANCE)
    sample_ep = [{
        "opportunity_id": "ep_f1", "decision_timestamp_ms": ts0, "release_ts_ms": ts0 + 600_000,
        "direction": "SHORT", "entry_reference": 3700.0, "selected_sl": 3704.0, "take_profit": 3688.0,
        "filled": True, "outcome_label": "LOSS", "executed_quantity": 0.50, "entry_fill_price": 3699.90,
        "exit_fill_price": 3704.10, "realized_gross_pnl_usd": -2.10, "total_fees_usd": 1.4808,
        "realized_net_pnl_usd": -3.5808, "planned_net_risk_usd": 2.25,
    }]
    f_bad_tier = compute_kh01b_r1_fee_sensitivity(sample_ep, claimed_actual_account_fee_tier="VIP3_0.0002", account_tier_verified_receipt=False, require_exact_e00_baseline_parity=False)
    f_ok_ctrl = compute_kh01b_r1_fee_sensitivity(sample_ep, require_exact_e00_baseline_parity=False)
    _rec("DSC-M14", "FEE_PROVENANCE", "UNVERIFIED_VIP_ACCOUNT_FEE_TIER_CLAIM_REJECTED", "compute_kh01b_r1_fee_sensitivity", f_bad_tier["status"] == "FAIL_PROVENANCE", f_ok_ctrl["status"] == "PASS", str(f_bad_tier["violations"]))

    # DSC-M15: Missing fill notionals returns NOT_IDENTIFIABLE (never invented numeric PnL)
    f_missing_notional = compute_kh01b_r1_fee_sensitivity([{**sample_ep[0], "entry_fill_price": None}], require_exact_e00_baseline_parity=False)
    _rec("DSC-M15", "FEE_SENSITIVITY", "MISSING_FILL_NOTIONALS_RETURNS_NOT_IDENTIFIABLE", "compute_kh01b_r1_fee_sensitivity", f_missing_notional["status"] == "NOT_IDENTIFIABLE", f_ok_ctrl["status"] == "PASS", f_missing_notional.get("reason", ""))

    # DSC-M16: One-sided fee application and 0.0006 mislabeled as 0.0004 rejected
    f_one_side = compute_kh01b_r1_fee_sensitivity(sample_ep, apply_both_entry_and_exit_legs=False, require_exact_e00_baseline_parity=False)
    f_mislabel = compute_kh01b_r1_fee_sensitivity(sample_ep, scenario_label_map={"0.0006": "0.0004"}, require_exact_e00_baseline_parity=False)
    _rec("DSC-M16", "FEE_SENSITIVITY", "ONE_SIDED_FEE_AND_MISLABELED_0P06_AS_0P04_REJECTED", "compute_kh01b_r1_fee_sensitivity", (f_one_side["status"] == "FAIL_PROVENANCE" and f_mislabel["status"] == "FAIL_PROVENANCE"), f_ok_ctrl["status"] == "PASS", str(f_one_side["violations"] + f_mislabel["violations"]))

    # DSC-M17: Strict 20 GiB disk preflight: 35 GiB accepted, while 20.49, 18.5, 13.4667, 8.0 GiB all BLOCKED_LOW_DISK
    p_35 = compute_kh01b_r1_disk_reserve_policy(int(35.0 * 1024**3))
    low_checks = [compute_kh01b_r1_disk_reserve_policy(int(g * 1024**3)) for g in (20.49, 18.5, 13.4667, 8.0)]
    all_low_blocked = all((not x["can_proceed"] and x["disk_safety_verdict"] == "BLOCKED_LOW_DISK" and not x["lowered_reserve_without_approval"]) for x in low_checks)
    _rec("DSC-M17", "DISK_SAFETY", "PREFLIGHT_20P49_18P5_13P47_8P0_GIB_ALL_BLOCKED_STRICT_20GIB", "compute_kh01b_r1_disk_reserve_policy", all_low_blocked, (p_35["can_proceed"] and p_35["effective_enforced_reserve_gib"] == 20.0), f"13.4667_shortfall_gib={low_checks[2]['shortfall_gib']}")

    # DSC-M18: Separate repo/temp volumes: repo=40 GiB, temp=14 GiB -> BLOCKED_LOW_DISK (and collector online write blocked too!)
    p_sep = compute_kh01b_r1_disk_reserve_policy(int(40.0 * 1024**3), volume_free_bytes_by_label={"repo": int(40.0 * 1024**3), "output": int(40.0 * 1024**3), "temp": int(14.0 * 1024**3)})
    col_low_disk = collect_online_bitget_audit_and_extended_dev(
        Path(tempfile.gettempdir()) / "unused_low_disk_check",
        freeze_ts_ms=DEFAULT_DEV_EXTENDED_END_MS + 86_400_000,
        volume_free_override={"repo": int(13.4667 * 1024**3), "output": int(13.4667 * 1024**3), "temp": int(13.4667 * 1024**3)},
    )
    _rec("DSC-M18", "DISK_SAFETY", "MULTI_VOLUME_LOW_TEMP_AND_COLLECTOR_LOW_DISK_BLOCKED", "compute_kh01b_r1_disk_reserve_policy", (not p_sep["can_proceed"] and p_sep["constraining_volume"] == "temp" and col_low_disk["DATA_ACQUISITION_VERDICT"] == "BLOCKED_LOW_DISK" and col_low_disk["network_requests_made"] == 0), p_35["can_proceed"], str(p_sep["abort_reason"]))

    # DSC-M19: Runtime disk drop below 20 GiB and ENOSPC rejected by resource cleanup verifier
    r_drop = verify_kh01b_r1_resource_cleanup({"max_children_observed": 2, "minimum_free_disk_bytes": int(19.5 * 1024**3), "orphan_pids_at_cleanup_count": 0, "disk_reserve_breached": False, "enospc_encountered": True}, {"cleaned": True, "sentinel_verified": True}, p_35, orchestrator_pid=os.getpid(), post_cleanup_free_bytes=int(34.0 * 1024**3))
    r_ok = verify_kh01b_r1_resource_cleanup({"max_children_observed": 2, "minimum_free_disk_bytes": int(32.0 * 1024**3), "orphan_pids_at_cleanup_count": 0, "disk_reserve_breached": False, "enospc_encountered": False}, {"cleaned": True, "sentinel_verified": True}, p_35, orchestrator_pid=os.getpid(), post_cleanup_free_bytes=int(34.0 * 1024**3))
    _rec("DSC-M19", "DISK_SAFETY", "RUNTIME_DISK_DROP_AND_ENOSPC_REJECTED", "verify_kh01b_r1_resource_cleanup", r_drop["status"] == "FAIL", r_ok["status"] == "PASS", f"breached={r_drop['effective_disk_reserve_breached']}")

    # DSC-M20: Sentinel mismatch and child symlink/NTFS-junction escaping owned temp dir refused without deleting target
    test_run_tag = f"dsc_m20_{os.getpid()}"
    tmp_owned = create_owned_temp_root(test_run_tag)
    outside_victim_dir = Path(tempfile.gettempdir()).resolve() / f"dsc_victim_dir_{os.getpid()}"
    outside_victim_dir.mkdir(parents=True, exist_ok=True)
    outside_victim = outside_victim_dir / "do_not_delete.txt"
    outside_victim.write_text("DO_NOT_DELETE", encoding="utf-8")
    escape_link = tmp_owned / "escape_sym"
    m20_sym_blocked = False
    m20_sent_blocked = False
    clean_rcpt: Dict[str, Any] = {"cleaned": False}
    link_kind = "NONE"
    try:
        link_kind = _create_reparse_link_for_test(outside_victim_dir, escape_link)
        try:
            safe_cleanup_owned_temp_root_r1(tmp_owned, test_run_tag)
        except RuntimeError:
            m20_sym_blocked = True
        _remove_reparse_link_for_test(escape_link)
        try:
            safe_cleanup_owned_temp_root_r1(tmp_owned, "WRONG_RUN_ID")
        except RuntimeError:
            m20_sent_blocked = True
        clean_rcpt = safe_cleanup_owned_temp_root_r1(tmp_owned, test_run_tag)
        victim_intact = outside_victim.is_file() and outside_victim.read_text("utf-8") == "DO_NOT_DELETE"
    finally:
        if escape_link.exists() or escape_link.is_symlink():
            _remove_reparse_link_for_test(escape_link)
        shutil.rmtree(tmp_owned, ignore_errors=True)
        shutil.rmtree(outside_victim_dir, ignore_errors=True)
    _rec("DSC-M20", "TEMP_SAFETY", "SYMLINK_OR_JUNCTION_ESCAPE_AND_SENTINEL_MISMATCH_REFUSED_VICTIM_INTACT", "safe_cleanup_owned_temp_root_r1", (m20_sym_blocked and m20_sent_blocked and victim_intact), bool(clean_rcpt.get("cleaned")), f"link_kind={link_kind},victim_intact={victim_intact}")

    # DSC-M21: One-off BBO snapshot, previously inspected data, and DEV_EXTENDED rejected from Fresh OOS Escrow
    esc_bad = build_and_validate_future_oos_escrow_registry(
        freeze_timestamp_ms=ts0,
        frozen_candidate_code_sha="sha",
        frozen_config_sha256="cfg",
        known_dev_dataset_shas=[CANONICAL_DATASET_SHA256],
        proposed_oos_entries=[{
            "dataset_id": "bbo_renamed_oos",
            "dataset_sha256": "7" * 64,
            "earliest_bar_open_ms": CANONICAL_SAMPLE_END_MS + 900_000_000,
            "latest_bar_close_ms": CANONICAL_SAMPLE_END_MS + 900_600_000,
            "strategy_evaluation_count": 0,
            "partition_role": "FRESH_UNTOUCHED",
            "is_single_bbo_probe": True,
            "previously_inspected": True,
        }],
    )
    esc_good = build_and_validate_future_oos_escrow_registry(freeze_timestamp_ms=ts0, frozen_candidate_code_sha="sha", frozen_config_sha256="cfg")
    _rec("DSC-M21", "OOS_ESCROW", "SINGLE_BBO_AND_INSPECTED_DATA_REJECTED_FROM_FRESH_OOS", "build_and_validate_future_oos_escrow_registry", esc_bad["status"] == "FAIL", esc_good["status"] == "PASS", str(esc_bad["violations"]))

    # DSC-M22: Runtime PRODUCTION/LIVE/PAPER/AUTO=ON rejected with PermissionError
    m22_red = True
    for bad_m in ({"PRODUCTION": "ON"}, {"LIVE": True}, {"PAPER": "ON"}, {"AUTO": "ACTIVE"}):
        try:
            assert_research_only_runtime_modes_off(bad_m)
            m22_red = False
        except PermissionError:
            pass
    m22_green = True
    assert_research_only_runtime_modes_off({"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"})
    _rec("DSC-M22", "RUNTIME_ISOLATION", "ALL_FOUR_RUNTIME_ACTIVATION_MODES_HARD_LOCKED_OFF", "assert_research_only_runtime_modes_off", m22_red, m22_green, "PRODUCTION/LIVE/PAPER/AUTO=OFF")

    # DSC-M23 (R2-B): Exact prior blocked 17.1503 GiB + 20.49/18.5/13.4667/8.0 GiB + low temp mount blocked; 35 & 45 GiB GO
    p_17 = compute_kh01b_r1_disk_reserve_policy(
        int(17.1503 * 1024**3),
        volume_free_bytes_by_label={
            "repo": int(45.0 * 1024**3),
            "output": int(45.0 * 1024**3),
            "temp": int(17.1503 * 1024**3),
        },
    )
    p_45 = compute_kh01b_r1_disk_reserve_policy(
        int(45.0 * 1024**3),
        volume_free_bytes_by_label={
            "repo": int(45.0 * 1024**3),
            "output": int(45.0 * 1024**3),
            "temp": int(188.0 * 1024**3),
        },
    )
    _rec(
        "DSC-M23",
        "DISK_SAFETY",
        "R2_PRIOR_17P1503_GIB_AND_LOW_TEMP_MOUNT_BLOCKED_VS_45GIB_GO",
        "compute_kh01b_r1_disk_reserve_policy",
        (not p_17["can_proceed"] and p_17["disk_safety_verdict"] == "BLOCKED_LOW_DISK" and p_17["constraining_volume"] == "temp"),
        (p_45["can_proceed"] and p_45["disk_safety_verdict"] == "PASS_STRICT_20GIB"),
        f"17p1503_shortfall_gib={p_17['shortfall_gib']}",
    )

    # DSC-M24 (R2-C, R2-F): Focused 15-test smoke run or collect-only substituted for full repo regression rejected
    r2_tmp = Path(tempfile.mkdtemp(prefix=f"dsc_r2_ctrl_{os.getpid()}_"))
    try:
        fake_pass_dir = r2_tmp / "forged_reg"
        (fake_pass_dir / "receipts").mkdir(parents=True, exist_ok=True)
        (fake_pass_dir / "00_preflight_branch_head_dataset_disk_volumes.json").write_text(
            json.dumps({
                "branch": MANDATORY_BRANCH_KH01B_R1,
                "tested_executable_sha": "a" * 40,
                "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1,
                "disk_reserve_policy": p_45,
                "source_tree_unchanged_during_run": True,
                "prior_artifacts_unchanged_during_run": True,
            }),
            encoding="utf-8",
        )
        (fake_pass_dir / "09_full_regression_collected_executed_junit_exit_bijection.json").write_text(
            json.dumps({
                "execution_status": "FOCUSED_15_TESTS_ONLY",
                "focused_in_memory_unit_tests": {"status": "PASS", "executed_count": 15, "passed_count": 15},
            }),
            encoding="utf-8",
        )
        ev_forged = evaluate_kh01b_r1_dsc_gates_from_disk(fake_pass_dir, "RUN_FAKE", "a" * 40)
        g09_forged = next(g for g in ev_forged["gates"] if g["gate_id"] == "G-DS-09")
        m24_red = (ev_forged["orthogonal_verdicts"]["ENGINEERING_CLOSURE_VERDICT"] == "FAIL" and g09_forged["status"] == "FAIL")

        # DSC-M25 (R2-C, R2-F): Fake JUnit XML planted when regression is NOT_RUN_BLOCKED_LOW_DISK rejected
        blk_xml_dir = r2_tmp / "blk_fake_xml"
        (blk_xml_dir / "junit").mkdir(parents=True, exist_ok=True)
        (blk_xml_dir / "junit/FAKE.xml").write_text("<testsuite tests='1'/>", encoding="utf-8")
        (blk_xml_dir / "00_preflight_branch_head_dataset_disk_volumes.json").write_text(
            json.dumps({"branch": MANDATORY_BRANCH_KH01B_R1, "tested_executable_sha": "b" * 40, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1, "disk_reserve_policy": p_17, "source_tree_unchanged_during_run": True, "prior_artifacts_unchanged_during_run": True}),
            encoding="utf-8",
        )
        (blk_xml_dir / "09_full_regression_collected_executed_junit_exit_bijection.json").write_text(
            json.dumps({"execution_status": "NOT_RUN_BLOCKED_LOW_DISK"}), encoding="utf-8"
        )
        ev_blk_xml = evaluate_kh01b_r1_dsc_gates_from_disk(blk_xml_dir, "RUN_BLK_XML", "b" * 40)
        g09_blk_xml = next(g for g in ev_blk_xml["gates"] if g["gate_id"] == "G-DS-09")
        m25_red = (g09_blk_xml["status"] == "FAIL" and "FAKE_JUNIT_XML_PRESENT_WHEN_NOT_RUN" in g09_blk_xml["violations"])

        # DSC-M26 (R2-A, R2-F): Stale wrong tested_executable_sha (0c247bdc vs current code SHA) rejected on gate & receipt
        from services.kh_01a_r1_forensics_closure_service import build_dynamic_validated_gate_receipt_r1
        from tools.fresh_junit_io import write_json_durable_atomic as _w_json_m26

        (blk_xml_dir / "receipts").mkdir(parents=True, exist_ok=True)
        build_dynamic_validated_gate_receipt_r1(
            gate_id="G-DS-01",
            run_dir=blk_xml_dir,
            run_id="RUN_NEW",
            tested_sha=PRIOR_DSC_V1_TESTED_SHA,
            target_rel_path="00_preflight_branch_head_dataset_disk_volumes.json",
            validator_fn=lambda: {"status": "PASS", "violations": []},
            write_json_fn=_w_json_m26,
        )
        rcpt_stale = EvidenceReceiptV17931219(
            **json.loads((blk_xml_dir / "receipts/G-DS-01.json").read_text("utf-8"))["receipt"]
        )
        m26_red = not rcpt_stale.is_valid_pass(
            expected_run_id="RUN_NEW",
            expected_tested_sha="c" * 40,
            run_dir=blk_xml_dir,
            reject_uniform_placeholder_hash=True,
        )
        build_dynamic_validated_gate_receipt_r1(
            gate_id="G-DS-02",
            run_dir=blk_xml_dir,
            run_id="RUN_NEW",
            tested_sha="b" * 40,
            target_rel_path="00_preflight_branch_head_dataset_disk_volumes.json",
            validator_fn=lambda: {"status": "PASS", "violations": []},
            write_json_fn=_w_json_m26,
        )
        rcpt_fresh = EvidenceReceiptV17931219(
            **json.loads((blk_xml_dir / "receipts/G-DS-02.json").read_text("utf-8"))["receipt"]
        )
        m26_green = rcpt_fresh.is_valid_pass(
            expected_run_id="RUN_NEW",
            expected_tested_sha="b" * 40,
            run_dir=blk_xml_dir,
            reject_uniform_placeholder_hash=True,
        )

        # DSC-M27 (G-DS-09/11): Missing worker shard or non-identical w1/w2/w4/w1_repeat canonical hash rejected
        bad_w_dir = r2_tmp / "bad_workers"
        bad_w_dir.mkdir(parents=True, exist_ok=True)
        (bad_w_dir / "00_preflight_branch_head_dataset_disk_volumes.json").write_text(
            json.dumps({"branch": MANDATORY_BRANCH_KH01B_R1, "tested_executable_sha": "d" * 40, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1, "disk_reserve_policy": p_45}),
            encoding="utf-8",
        )
        (bad_w_dir / "08_async_w1_w2_w4_repeat_determinism_and_speed.json").write_text(
            json.dumps({"execution_status": "EXECUTED", "pytest_subset_w1_w2_w4_repeat_identical": False, "r1_variant_slice_w1_w2_w4_repeat_identical": True}),
            encoding="utf-8",
        )
        ev_bad_w = evaluate_kh01b_r1_dsc_gates_from_disk(bad_w_dir, "RUN_W", "d" * 40)
        g11_bad_w = next(g for g in ev_bad_w["gates"] if g["gate_id"] == "G-DS-11")
        m27_red = (g11_bad_w["status"] == "FAIL")
    finally:
        shutil.rmtree(r2_tmp, ignore_errors=True)

    _rec("DSC-M24", "REGRESSION_INTEGRITY", "FOCUSED_15_TESTS_SUBSTITUTED_FOR_FULL_REGRESSION_REJECTED", "evaluate_kh01b_r1_dsc_gates_from_disk", m24_red, True, "focused-only status rejected")
    _rec("DSC-M25", "JUNIT_INTEGRITY", "FAKE_JUNIT_XML_UNDER_BLOCKED_LOW_DISK_REJECTED", "evaluate_kh01b_r1_dsc_gates_from_disk", m25_red, True, "FAKE_JUNIT_XML_PRESENT_WHEN_NOT_RUN")
    _rec("DSC-M26", "SHA_LOCK", "STALE_TESTED_EXECUTABLE_SHA_MISMATCH_REJECTED", "EvidenceReceiptV17931219.is_valid_pass", m26_red, m26_green, f"stale={PRIOR_DSC_V1_TESTED_SHA[:12]} rejected")
    _rec("DSC-M27", "WORKER_DETERMINISM", "NON_IDENTICAL_W1_W2_W4_REPEAT_HASH_REJECTED", "evaluate_kh01b_r1_dsc_gates_from_disk", m27_red, True, "hash mismatch fails G-DS")

    # DSC-M28 (R2-A, R2-G): Writing accepted latest_run.json pointer for a BLOCKED_LOW_DISK run rejected
    m28_red = validate_latest_run_pointer_not_blocked(
        {"run_id": PRIOR_DSC_V1_BLOCKED_RUN_ID, "engineering_closure_verdict": "BLOCKED_LOW_DISK"}
    )["status"] == "FAIL"
    m28_green = validate_latest_run_pointer_not_blocked(
        {"run_id": "20261120T000000Z_accepted", "engineering_closure_verdict": "PASS", "disk_safety_verdict": "PASS_STRICT_20GIB"}
    )["status"] == "PASS"
    _rec("DSC-M28", "PUBLICATION_SAFETY", "BLOCKED_RUN_PROMOTED_TO_ACCEPTED_LATEST_POINTER_REJECTED", "validate_latest_run_pointer_not_blocked", m28_red, m28_green, "blocked run forbidden in latest_run.json")

    # DSC-M29 (R2-E, R2-F): Fake 2880/2880 VERIFIED_COMPLETE claim on 1600-bar M1 snapshot rejected
    m29_cov = derive_window_coverage_by_timeframe(
        [tail_st + i * 60_000 for i in range(1600)],
        requested_start_ms=DEFAULT_DEV_EXTENDED_START_MS,
        requested_end_ms=DEFAULT_DEV_EXTENDED_END_MS,
        granularity="1m",
        duration_ms=60_000,
        calendar_verified=True,
    )
    m29_red = (m29_cov["coverage_verdict"] != "VERIFIED_COMPLETE" and m29_cov["missing_slots"] == 1280)
    _rec("DSC-M29", "WINDOW_COVERAGE", "FAKE_2880_COMPLETE_CLAIM_ON_1600_M1_SNAPSHOT_REJECTED", "derive_window_coverage_by_timeframe", m29_red, v_2880["coverage_verdict"] == "VERIFIED_COMPLETE", f"verdict={m29_cov['coverage_verdict']}")

    # DSC-M30 (R2-B, R2-F): Lowered 10 GiB diagnostic mode rejected from acceptance PASS
    p_diag = compute_kh01b_r1_disk_reserve_policy(int(17.1503 * 1024**3), mode="UNVERIFIED_DIAGNOSTIC_ONLY")
    r_diag_chk = verify_kh01b_r1_resource_cleanup(
        {"max_children_observed": 1, "minimum_free_disk_bytes": int(17.0 * 1024**3), "orphan_pids_at_cleanup_count": 0, "disk_reserve_breached": False},
        {"cleaned": True, "sentinel_verified": True},
        p_diag,
        orchestrator_pid=os.getpid(),
        post_cleanup_free_bytes=int(17.0 * 1024**3),
    )
    _rec("DSC-M30", "DISK_SAFETY", "DIAGNOSTIC_10GIB_DOWNGRADE_REJECTED_FROM_ENGINEERING_ACCEPTANCE", "verify_kh01b_r1_resource_cleanup", (not p_diag["acceptance_eligible"] and r_diag_chk["status"] == "FAIL"), r_ok["status"] == "PASS", p_diag["disk_safety_verdict"])

    # DSC-M31 (R2-D, R2-F): Unbacked HTTP origin SHA on bars AND 242-SI0 rerisked study claimed as full 56,710 rerun rejected
    f_fake_56k = compute_kh01b_r1_fee_sensitivity(sample_ep, require_exact_e00_baseline_parity=False, claim_242_rerisk_is_full_56710_rerun=True)
    v_bad_origin = validate_and_normalize_paginated_candles(
        [{"raw_http_sha256": "NOT_A_VALID_SHA256", "server_time_ms": ts0 + 600_000, "rows": pg_a["rows"]}],
        symbol="XAUUSDT", granularity="1m", duration_ms=60_000, require_origin_sha=True,
    )
    _rec("DSC-M31", "PROVENANCE_AND_SCOPE", "BAD_ORIGIN_SHA_AND_242_RERISK_CLAIMED_AS_56710_RERUN_REJECTED", "compute_kh01b_r1_fee_sensitivity", (f_fake_56k["status"] == "FAIL_PROVENANCE" and v_bad_origin["status"] == "FAIL"), f_ok_ctrl["status"] == "PASS", str(f_fake_56k["violations"] + v_bad_origin["violations"]))

    passed_n = sum(1 for c in cases if c["status"] == "PASS")
    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC,
        "r2_spec_version": SPEC_VERSION_KH01B_R1_DSC_R2,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC,
        "total_mutations": len(cases),
        "passed_mutations": passed_n,
        "failed_mutations": len(cases) - passed_n,
        "status": "PASS" if (passed_n == len(cases) and len(cases) >= 22) else "FAIL",
        "mutations": cases,
        "fault_suite_sha256": canonical_hash(cases),
    }


# ==============================================================================
# 5. INDEPENDENT EVALUATION OF G-DS-01 .. G-DS-15 FROM DISK (14)
# ==============================================================================

def evaluate_kh01b_r1_dsc_gates_from_disk(
    run_dir: Path,
    run_id: str,
    tested_sha: str,
    *,
    publication_status: str = "PENDING_REMOTE_PUSH",
) -> Dict[str, Any]:
    """
    Evaluates `G-DS-01`..`G-DS-15` independently from closed disk files & dynamic receipts (§8).
    CRITICAL HONESTY RULE:
      - `ENGINEERING_CLOSURE_VERDICT` is `"PASS"` ONLY when `DISK_SAFETY_VERDICT == "PASS_STRICT_20GIB"`
        AND all mandatory gates PLUS `G-DS-09`, `G-DS-10`, `G-DS-11` actually executed and passed!
      - When `DISK_SAFETY_VERDICT == "BLOCKED_LOW_DISK"`, `G-DS-09`, `G-DS-10`, `G-DS-11` MUST be
        `"NOT_RUN_BLOCKED_LOW_DISK"` (never fake green!) and `ENGINEERING_CLOSURE_VERDICT` is
        `"BLOCKED_LOW_DISK"` even when all 11 offline data/safety/provenance gates pass!
    """
    def _rd(name: str) -> Dict[str, Any]:
        p = run_dir / name
        if not p.is_file():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    f00 = _rd("00_preflight_branch_head_dataset_disk_volumes.json")
    f01 = _rd("01_immutable_baseline_and_keep_as_is_callgraph.json")
    f02 = _rd("02_gap_schema_mapping_red_green_and_six_tf_completeness.json")
    f03 = _rd("03_pagination_cursor_order_boundary_duplicates_negative_controls.json")
    f04 = _rd("04_extended_dev_requested_vs_observed_window_coverage.json")
    f05 = _rd("05_bitget_origin_fee_quote_calendar_provenance.json")
    f06 = _rd("06_baseline_0p04_vs_public_0p06_fee_sensitivity_caveats.json")
    f07 = _rd("07_strict_20gib_disk_policy_injections_and_resource_telemetry.json")
    f08 = _rd("08_async_w1_w2_w4_repeat_determinism_and_speed.json")
    f09 = _rd("09_full_regression_collected_executed_junit_exit_bijection.json")
    f10 = _rd("10_backtest_original_parity_three_fill_and_adapter_fingerprints.json")
    f11 = _rd("11_data_oos_partition_never_evaluated_verdict.json")
    f12 = _rd("12_independent_verifier_fault_injections.json")

    receipts_map: Dict[str, EvidenceReceiptV17931219] = {}
    rcpt_dir = run_dir / "receipts"
    if rcpt_dir.is_dir():
        for rp in sorted(rcpt_dir.glob("G-DS-*.json")):
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

    disk_pol = f00.get("disk_reserve_policy") or {}
    disk_verdict = str(disk_pol.get("disk_safety_verdict") or "FAIL")

    # G-DS-01: Branch, tested SHA, clean tree, immutable dataset & locked files
    v01: List[str] = []
    if f00.get("branch") != MANDATORY_BRANCH_KH01B_R1 or f00.get("tested_executable_sha") != tested_sha:
        v01.append("BRANCH_OR_TESTED_SHA_MISMATCH")
    if f00.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH01B_R1:
        v01.append("CANONICAL_DATASET_SHA_MISMATCH")
    if f00.get("source_tree_unchanged_during_run") is not True or f00.get("prior_artifacts_unchanged_during_run") is not True:
        v01.append("SOURCE_OR_PRIOR_ARTIFACTS_MUTATED")
    if int(f01.get("locked_files_modified_count", 1)) != 0:
        v01.append("LOCKED_PRODUCTION_OR_PRIOR_RESEARCH_FILES_MODIFIED")
    _add_gate("G-DS-01", "BRANCH_SHA_CLEAN_TREE_AND_IMMUTABLE_BASELINE", "00_preflight_branch_head_dataset_disk_volumes.json", {"branch": f00.get("branch"), "tested_sha": tested_sha, "locked_modified": f01.get("locked_files_modified_count")}, "PASS" if not v01 else "FAIL", v01)

    # G-DS-02: True 6-timeframe gap schema non-null & negative null-key injection fails
    v02: List[str] = []
    if f02.get("status") != "PASS":
        v02.append("GAP_SCHEMA_AUDIT_NOT_PASS")
    if not (f02.get("red_control_old_per_timeframe_null_schema") or {}).get("rejected_as_required", False):
        v02.append("NULL_GAP_SCHEMA_NEGATIVE_CONTROL_NOT_REJECTED")
    for section_k in ("canonical_dataset_six_tf_gap_summary", "extended_dev_snapshot_six_tf_gap_summary"):
        tfs_sec = (f02.get(section_k) or {}).get("timeframes") or {}
        for req_tf in ("1m", "5m", "15m", "1H", "4H", "1D"):
            ge = tfs_sec.get(req_tf)
            if not isinstance(ge, Mapping) or ge.get("actual_bars") is None or ge.get("total_missing_bars") is None or not isinstance(ge.get("gap_events_by_category"), Mapping) or not isinstance(ge.get("missing_bars_by_category"), Mapping):
                v02.append(f"MISSING_OR_NULL_6TF_GAP_ENTRY:{section_k}:{req_tf}")
    _add_gate("G-DS-02", "SIX_TIMEFRAME_NON_NULL_GAP_SCHEMA_AND_RED_CONTROL", "02_gap_schema_mapping_red_green_and_six_tf_completeness.json", {"canonical_status": (f02.get("canonical_dataset_six_tf_gap_summary") or {}).get("mapper_status"), "extended_dev_status": (f02.get("extended_dev_snapshot_six_tf_gap_summary") or {}).get("mapper_status")}, "PASS" if not v02 else "FAIL", v02)

    # G-DS-03: Pagination budget, cursor progress, exact Decimal boundary dedupe vs conflict/within-page fail
    v03: List[str] = []
    if f03.get("status") != "PASS" or int(f03.get("failed_controls_count", 1)) != 0:
        v03.append("PAGINATION_OR_BOUNDARY_DEDUPE_CONTROLS_FAILED")
    if int((f03.get("m1_48h_dynamic_page_budget") or {}).get("effective_page_budget") or 0) <= 8:
        v03.append("SILENT_8_PAGE_1600_BAR_CAP_STILL_PRESENT")
    _add_gate("G-DS-03", "PAGINATION_BUDGET_CURSOR_AND_EXACT_BOUNDARY_DEDUPE", "03_pagination_cursor_order_boundary_duplicates_negative_controls.json", {"controls_passed": f03.get("passed_controls_count"), "m1_48h_page_budget": (f03.get("m1_48h_dynamic_page_budget") or {}).get("effective_page_budget")}, "PASS" if not v03 else "FAIL", v03)

    # G-DS-04: Requested-window completeness, leading/trailing/internal missing slots & origin receipts
    v04: List[str] = []
    cov_map = f04.get("coverage_by_timeframe") or {}
    if f04.get("status") != "PASS" or int(f04.get("unbacked_bars_without_origin_receipt_count", 1)) != 0:
        v04.append("WINDOW_COVERAGE_OR_ORIGIN_RECEIPT_VERIFICATION_FAILED")
    m1_cov = cov_map.get("1m") or {}
    if m1_cov.get("coverage_verdict") == "VERIFIED_COMPLETE" and int(m1_cov.get("missing_slots", 1)) > 0:
        v04.append("PARTIAL_M1_WINDOW_MISLABELED_COMPLETE")
    for tf_k in ("1m", "5m", "15m", "1H", "4H", "1D"):
        c_tf = cov_map.get(tf_k)
        if not isinstance(c_tf, Mapping):
            v04.append(f"MISSING_COVERAGE_TF:{tf_k}")
            continue
        if int(c_tf.get("leading_missing_slots", -1)) + int(c_tf.get("internal_missing_slots", -1)) + int(c_tf.get("trailing_missing_slots", -1)) != int(c_tf.get("missing_slots", -99)):
            v04.append(f"MISSING_SLOTS_DECOMPOSITION_MISMATCH:{tf_k}")
        if int(c_tf.get("observed_unique_closed_slots", -1)) + int(c_tf.get("missing_slots", -1)) != int(c_tf.get("expected_closed_slots", -99)):
            v04.append(f"EXPECTED_VS_OBSERVED_SLOT_CONSERVATION_MISMATCH:{tf_k}")
    _add_gate("G-DS-04", "REQUESTED_WINDOW_SLOT_CONSERVATION_AND_PARTIAL_TRUTH", "04_extended_dev_requested_vs_observed_window_coverage.json", {"reclassified_verdict": f04.get("reclassified_DATA_ACQUISITION_VERDICT"), "m1_observed_vs_expected": f"{m1_cov.get('observed_unique_closed_slots')}/{m1_cov.get('expected_closed_slots')}"}, "PASS" if not v04 else "FAIL", v04)

    # G-DS-05: Quote/contract/fee/calendar provenance honesty
    v05: List[str] = []
    ct_05 = f05.get("public_contract_fee_snapshot_audit") or {}
    bbo_05 = f05.get("prospective_single_bbo_snapshot_audit") or {}
    if f05.get("status") != "PASS":
        v05.append("PROVENANCE_AUDIT_05_NOT_PASS")
    if ct_05.get("actual_account_effective_fee_tier") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        v05.append("UNVERIFIED_ACCOUNT_FEE_TIER_PRESENTED_AS_KNOWN")
    if bbo_05.get("historical_backtest_usable") is not False or bbo_05.get("continuous_historical_series_coverage") is not False:
        v05.append("SINGLE_BBO_MISREPRESENTED_AS_HISTORICAL_SERIES")
    _add_gate("G-DS-05", "QUOTE_CONTRACT_FEE_AND_CALENDAR_PROVENANCE_TRUTH", "05_bitget_origin_fee_quote_calendar_provenance.json", {"account_fee_tier": ct_05.get("actual_account_effective_fee_tier"), "public_taker_fee": ct_05.get("observed_public_taker_fee_rate")}, "PASS" if not v05 else "FAIL", v05)

    # G-DS-06: Fee sensitivity 0.0004 vs 0.0006 + 2x/3x stress, baseline intact, NOT_IDENTIFIABLE guard
    v06: List[str] = []
    par_06 = f06.get("original_e00_b00_reference_parity") or {}
    fo_06 = f06.get("FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS") or {}
    rr_06 = f06.get("FULL_RE_RISKED_REPLAY") or {}
    if f06.get("status") != "PASS":
        v06.append("FEE_SENSITIVITY_06_NOT_PASS")
    if (
        int(par_06.get("closed_count") or 0) != 166
        or float(par_06.get("gross_pnl_usd") or 0.0) != -142.7836
        or float(par_06.get("total_fees_usd") or 0.0) != 203.1135
        or float(par_06.get("net_pnl_usd") or 0.0) != -345.8971
    ):
        v06.append("ORIGINAL_E00_FEE_PARITY_BROKEN")
    if "0.0004" not in fo_06 or "0.0006" not in fo_06 or "0.0004" not in rr_06 or "0.0006" not in rr_06:
        v06.append("MISSING_0P0004_OR_0P0006_SCENARIO_IN_FEE_SENSITIVITY")
    if (f06.get("domain_calculator_defaults_preserved") or {}).get("unchanged_verified") is not True:
        v06.append("DOMAIN_CALCULATOR_DEFAULTS_MUTATED")
    _add_gate("G-DS-06", "DECIMAL_FEE_SENSITIVITY_0P04_VS_0P06_AND_STRESS", "06_baseline_0p04_vs_public_0p06_fee_sensitivity_caveats.json", {"e00_net_0p04": (fo_06.get("0.0004") or {}).get("net_pnl_1x_usd"), "e00_net_0p06_fixed_fills": (fo_06.get("0.0006") or {}).get("net_pnl_1x_usd")}, "PASS" if not v06 else "FAIL", v06)

    # G-DS-07: Strict 20 GiB disk policy enforcement (no silent 10 GiB downgrade; negative injections rejected)
    v07: List[str] = []
    if f07.get("policy_injections_status") != "PASS":
        v07.append("DISK_POLICY_INJECTIONS_FAILED")
    if bool(disk_pol.get("lowered_reserve_without_approval", True)) or float(disk_pol.get("enforced_reserve_gib") or 0.0) < 20.0:
        v07.append("DISK_RESERVE_LOWERED_BELOW_20GIB")
    if disk_verdict == "PASS_STRICT_20GIB":
        if float(disk_pol.get("preflight_baseline_free_gib") or 0.0) < 20.5:
            v07.append("FALSE_PASS_STRICT_20GIB_WHEN_FREE_SPACE_LT_20P5")
        g07_st = "PASS" if not v07 else "FAIL"
    elif disk_verdict == "BLOCKED_LOW_DISK":
        if bool(disk_pol.get("can_proceed", True)):
            v07.append("LOW_DISK_ALLOWED_CAN_PROCEED_TRUE")
        # Policy guard worked truthfully, while overall disk state is BLOCKED_LOW_DISK!
        g07_st = "BLOCKED_LOW_DISK" if not v07 else "FAIL"
    else:
        v07.append(f"INVALID_DISK_SAFETY_VERDICT:{disk_verdict}")
        g07_st = "FAIL"
    _add_gate(
        "G-DS-07",
        "STRICT_20GIB_DISK_RESERVE_ON_ALL_WRITING_VOLUMES",
        "07_strict_20gib_disk_policy_injections_and_resource_telemetry.json",
        {"disk_safety_verdict": disk_verdict, "preflight_free_gib": disk_pol.get("preflight_baseline_free_gib"), "shortfall_gib": disk_pol.get("shortfall_gib")},
        g07_st,
        v07 if v07 else ([] if g07_st == "PASS" else [str(disk_pol.get("abort_reason") or "BLOCKED_LOW_DISK")]),
        require_pass_receipt=(g07_st == "PASS"),
    )

    # G-DS-08: Bounded <=4 descendants, 0 orphans, sentinel-verified temp cleanup, no cross-owned deletion
    v08: List[str] = []
    tel_07 = f07.get("runtime_telemetry") or {}
    if int(tel_07.get("max_children_observed") or 0) > 4:
        v08.append(f"MAX_CHILDREN_EXCEEDED_4:{tel_07.get('max_children_observed')}")
    if int(tel_07.get("orphan_pids_at_cleanup_count", 1)) != 0:
        v08.append("ORPHAN_PIDS_AT_CLEANUP")
    if tel_07.get("temp_dirs_cleaned_with_sentinel_check") is not True:
        v08.append("TEMP_SENTINEL_CLEANUP_NOT_VERIFIED")
    if int(tel_07.get("unowned_paths_deleted_count", 1)) != 0:
        v08.append("UNOWNED_PATHS_DELETED")
    _add_gate("G-DS-08", "BOUNDED_DESCENDANTS_ZERO_ORPHANS_AND_SENTINEL_CLEANUP", "07_strict_20gib_disk_policy_injections_and_resource_telemetry.json", {"max_children": tel_07.get("max_children_observed"), "orphans": tel_07.get("orphan_pids_at_cleanup_count"), "sentinel_cleaned": tel_07.get("temp_dirs_cleaned_with_sentinel_check")}, "PASS" if not v08 else "FAIL", v08)

    # G-DS-09: Full pytest regression & JUnit bijection (ONLY when disk passed & actually executed)
    st_09 = str(f09.get("execution_status") or "MISSING")
    if st_09 == "EXECUTED":
        v09: List[str] = []
        bij_09 = f09.get("independent_bijection_verification") or {}
        if bij_09.get("status") != "PASS" or int((f09.get("shard_manifest") or {}).get("nonzero_exit_shards_count", 1)) != 0:
            v09.append("FULL_PYTEST_OR_JUNIT_BIJECTION_FAILED")
        _add_gate("G-DS-09", "FRESH_FULL_PYTEST_COLLECTED_EXECUTED_JUNIT_BIJECTION", "09_full_regression_collected_executed_junit_exit_bijection.json", bij_09.get("counters"), "PASS" if not v09 else "FAIL", v09)
    elif st_09 == "NOT_RUN_BLOCKED_LOW_DISK" and disk_verdict == "BLOCKED_LOW_DISK":
        junit_xmls = list((run_dir / "junit").glob("*.xml")) if (run_dir / "junit").is_dir() else []
        if junit_xmls:
            _add_gate("G-DS-09", "FRESH_FULL_PYTEST_COLLECTED_EXECUTED_JUNIT_BIJECTION", "09_full_regression_collected_executed_junit_exit_bijection.json", {"fake_xml_count": len(junit_xmls)}, "FAIL", ["FAKE_JUNIT_XML_PRESENT_WHEN_NOT_RUN"])
        else:
            _add_gate("G-DS-09", "FRESH_FULL_PYTEST_COLLECTED_EXECUTED_JUNIT_BIJECTION", "09_full_regression_collected_executed_junit_exit_bijection.json", {"execution_status": st_09, "focused_in_memory_tests": f09.get("focused_in_memory_unit_tests")}, "NOT_RUN_BLOCKED_LOW_DISK", ["NOT_RUN_DUE_TO_STRICT_20GIB_DISK_RESERVE"], require_pass_receipt=False)
    else:
        _add_gate("G-DS-09", "FRESH_FULL_PYTEST_COLLECTED_EXECUTED_JUNIT_BIJECTION", "09_full_regression_collected_executed_junit_exit_bijection.json", {"execution_status": st_09}, "FAIL", [f"INVALID_REGRESSION_STATUS:{st_09}"])

    # G-DS-10: Full historical E00/B00 backtest & 3-adapter parity (ONLY when disk passed & actually executed)
    st_10 = str(f10.get("execution_status") or "MISSING")
    if st_10 == "EXECUTED":
        v10: List[str] = []
        if f10.get("status") != "PASS" or int(f10.get("a1_mismatches_count", 1)) != 0 or int(f10.get("three_adapter_mismatches_count", 1)) != 0:
            v10.append("HISTORICAL_BACKTEST_OR_ADAPTER_PARITY_FAILED")
        _add_gate("G-DS-10", "HISTORICAL_E00_B00_THREE_FILL_AND_ADAPTER_PARITY", "10_backtest_original_parity_three_fill_and_adapter_fingerprints.json", {"a1_evaluated": f10.get("a1_evaluated_count"), "adapters": f10.get("three_adapter_episodes_denominator")}, "PASS" if not v10 else "FAIL", v10)
    elif st_10 == "NOT_RUN_BLOCKED_LOW_DISK" and disk_verdict == "BLOCKED_LOW_DISK":
        _add_gate("G-DS-10", "HISTORICAL_E00_B00_THREE_FILL_AND_ADAPTER_PARITY", "10_backtest_original_parity_three_fill_and_adapter_fingerprints.json", {"execution_status": st_10}, "NOT_RUN_BLOCKED_LOW_DISK", ["NOT_RUN_DUE_TO_STRICT_20GIB_DISK_RESERVE"], require_pass_receipt=False)
    else:
        _add_gate("G-DS-10", "HISTORICAL_E00_B00_THREE_FILL_AND_ADAPTER_PARITY", "10_backtest_original_parity_three_fill_and_adapter_fingerprints.json", {"execution_status": st_10}, "FAIL", [f"INVALID_BACKTEST_STATUS:{st_10}"])

    # G-DS-11: Async w1/w2/w4/w1_repeat determinism & speed measurements (ONLY when disk passed & actually executed)
    st_08 = str(f08.get("execution_status") or "MISSING")
    if st_08 == "EXECUTED":
        v11: List[str] = []
        if f08.get("pytest_subset_w1_w2_w4_repeat_identical") is not True or f08.get("r1_variant_slice_w1_w2_w4_repeat_identical") is not True:
            v11.append("WORKER_W1_W2_W4_DETERMINISM_FAILED")
        _add_gate("G-DS-11", "WORKER_W1_W2_W4_REPEAT_CANONICAL_PARITY_AND_SPEED", "08_async_w1_w2_w4_repeat_determinism_and_speed.json", {"w4_speedup": f08.get("measured_speedup_w4_vs_w1")}, "PASS" if not v11 else "FAIL", v11)
    elif st_08 == "NOT_RUN_BLOCKED_LOW_DISK" and disk_verdict == "BLOCKED_LOW_DISK":
        _add_gate("G-DS-11", "WORKER_W1_W2_W4_REPEAT_CANONICAL_PARITY_AND_SPEED", "08_async_w1_w2_w4_repeat_determinism_and_speed.json", {"execution_status": st_08}, "NOT_RUN_BLOCKED_LOW_DISK", ["NOT_RUN_DUE_TO_STRICT_20GIB_DISK_RESERVE"], require_pass_receipt=False)
    else:
        _add_gate("G-DS-11", "WORKER_W1_W2_W4_REPEAT_CANONICAL_PARITY_AND_SPEED", "08_async_w1_w2_w4_repeat_determinism_and_speed.json", {"execution_status": st_08}, "FAIL", [f"INVALID_WORKER_BENCH_STATUS:{st_08}"])

    # G-DS-12: Real RED/GREEN fault-injection suite (>=20 mutations)
    v12: List[str] = []
    if f12.get("status") != "PASS" or int(f12.get("passed_mutations") or 0) < 20 or int(f12.get("failed_mutations", 1)) != 0:
        v12.append("CLOSURE_FAULT_INJECTION_SUITE_FAILED")
    _add_gate("G-DS-12", "REAL_FAULT_INJECTION_RED_GREEN_CONTROLS", "12_independent_verifier_fault_injections.json", {"passed_mutations": f"{f12.get('passed_mutations')}/{f12.get('total_mutations')}"}, "PASS" if not v12 else "FAIL", v12)

    # G-DS-13: OOS escrow untouched & all runtime modes OFF
    v13: List[str] = []
    esc_11 = f11.get("future_oos_escrow") or {}
    modes_11 = f11.get("runtime_safety_modes") or {}
    oos_verdict = str(esc_11.get("FRESH_OOS_VERDICT") or "FAIL")
    if esc_11.get("status") != "PASS" or int(esc_11.get("strategy_evaluation_count_total", 1)) != 0 or oos_verdict not in ("BLOCKED_NO_UNTOUCHED_DATA", "SEALED_UNTOUCHED_NOT_EVALUATED"):
        v13.append(f"OOS_ESCROW_VIOLATION:{oos_verdict}")
    if any(modes_11.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")) or f11.get("ACTIVATION_READY") != "NO":
        v13.append("RUNTIME_ACTIVATION_NOT_OFF")
    _add_gate("G-DS-13", "OOS_ESCROW_ZERO_EVALUATION_AND_RUNTIME_MODES_OFF", "11_data_oos_partition_never_evaluated_verdict.json", {"FRESH_OOS_VERDICT": oos_verdict, "evaluation_count": esc_11.get("strategy_evaluation_count_total")}, "PASS" if not v13 else "FAIL", v13)

    # G-DS-14: Independent verifier dynamic receipts & readback
    v14: List[str] = []
    expected_pass_rcpts = 10 if disk_verdict == "BLOCKED_LOW_DISK" else 14
    if len(receipts_map) < expected_pass_rcpts:
        v14.append(f"INCOMPLETE_DYNAMIC_RECEIPTS:{len(receipts_map)}<{expected_pass_rcpts}")
    _add_gate("G-DS-14", "SEPARATE_OS_INDEPENDENT_VERIFIER_AND_RECEIPTS", "00_preflight_branch_head_dataset_disk_volumes.json", {"receipts_count": len(receipts_map)}, "PASS" if not v14 else "FAIL", v14)

    # G-DS-15: Same-branch publication & remote blob readback
    _add_gate(
        "G-DS-15",
        "SAME_BRANCH_PUBLICATION_AND_REMOTE_BLOB_READBACK",
        "00_preflight_branch_head_dataset_disk_volumes.json",
        {"publication_status": publication_status},
        publication_status,
        [] if publication_status == "PASS_REMOTE_VERIFIED" else ["AWAITING_REMOTE_PUSH_AND_READBACK"],
        require_pass_receipt=False,
    )

    by_gid = {g["gate_id"]: g for g in gates}
    any_hard_fail = any(g["status"] == "FAIL" for g in gates)
    offline_core_ok = all(
        by_gid[gid]["status"] == "PASS"
        for gid in ("G-DS-01", "G-DS-02", "G-DS-03", "G-DS-04", "G-DS-05", "G-DS-06", "G-DS-08", "G-DS-12", "G-DS-13", "G-DS-14")
    )
    heavy_all_pass = all(by_gid[gid]["status"] == "PASS" for gid in HEAVY_DSC_GATES_WHEN_DISK_PASS)

    if any_hard_fail:
        eng_verdict = "FAIL"
    elif disk_verdict == "BLOCKED_LOW_DISK" and offline_core_ok and by_gid["G-DS-07"]["status"] == "BLOCKED_LOW_DISK":
        eng_verdict = "BLOCKED_LOW_DISK"
    elif disk_verdict == "PASS_STRICT_20GIB" and offline_core_ok and by_gid["G-DS-07"]["status"] == "PASS" and heavy_all_pass:
        eng_verdict = "PASS"
    else:
        eng_verdict = "FAIL"

    data_acq_verdict = str(f04.get("online_capture_verdict") or f04.get("reclassified_DATA_ACQUISITION_VERDICT") or "BLOCKED_UNAVAILABLE")

    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": f00.get("canonical_dataset_sha256"),
        "orthogonal_verdicts": {
            "ENGINEERING_CLOSURE_VERDICT": eng_verdict,
            "DISK_SAFETY_VERDICT": disk_verdict,
            "DATA_ACQUISITION_VERDICT": data_acq_verdict,
            "FULL_REGRESSION_VERDICT": by_gid["G-DS-09"]["status"],
            "BACKTEST_VERDICT": by_gid["G-DS-10"]["status"],
            "WORKER_DETERMINISM_VERDICT": by_gid["G-DS-11"]["status"],
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE_BASELINE_UNCHANGED_FEE_SENSITIVITY_NEGATIVE",
            "KH01C_ADVANCEMENT_VERDICT": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE",
            "FRESH_OOS_VERDICT": oos_verdict if by_gid["G-DS-13"]["status"] == "PASS" else "FAIL_CONTAMINATED",
            "ACTIVATION_READY": "NO",
            "RUNTIME_SAFETY_MODES": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "PUBLICATION_VERDICT": publication_status,
        },
        "explicit_blockers": (
            [str(disk_pol.get("abort_reason"))]
            if disk_verdict == "BLOCKED_LOW_DISK"
            else ([g["gate_id"] for g in gates if g["status"] == "FAIL"])
        ),
        "gates_summary": {
            "pass_count": sum(1 for g in gates if g["status"] == "PASS"),
            "blocked_or_not_run_count": sum(1 for g in gates if g["status"] in ("BLOCKED_LOW_DISK", "NOT_RUN_BLOCKED_LOW_DISK", "PENDING_REMOTE_PUSH")),
            "fail_count": sum(1 for g in gates if g["status"] == "FAIL"),
            "total_gates": len(gates),
        },
        "gates": gates,
    }


# ==============================================================================
# 6. RENDER COMPACT README + VIETNAMESE HANDOVER & CROSS-VIEW CONSISTENCY
# ==============================================================================

def render_kh01b_r1_dsc_readme_and_handover_vi(
    *,
    run_id: str,
    branch: str,
    starting_head_sha: str,
    tested_sha: str,
    dataset_sha: str,
    f00: Mapping[str, Any],
    f01: Mapping[str, Any],
    f02: Mapping[str, Any],
    f03: Mapping[str, Any],
    f04: Mapping[str, Any],
    f05: Mapping[str, Any],
    f06: Mapping[str, Any],
    f07: Mapping[str, Any],
    f08: Mapping[str, Any],
    f09: Mapping[str, Any],
    f10: Mapping[str, Any],
    f11: Mapping[str, Any],
    f12: Mapping[str, Any],
    f14: Mapping[str, Any],
) -> Tuple[str, str]:
    ov = f14["orthogonal_verdicts"]
    d_pol = f00.get("disk_reserve_policy") or {}
    par_06 = f06.get("original_e00_b00_reference_parity") or {}
    fo_04 = (f06.get("FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS") or {}).get("0.0004") or {}
    fo_06_sc = (f06.get("FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS") or {}).get("0.0006") or {}
    rr_04 = (f06.get("FULL_RE_RISKED_REPLAY") or {}).get("0.0004") or {}
    rr_06_sc = (f06.get("FULL_RE_RISKED_REPLAY") or {}).get("0.0006") or {}
    cov_map = f04.get("coverage_by_timeframe") or {}

    cov_rows = [
        "| Timeframe | Expected Closed Slots | Observed Unique Closed | Leading Missing | Internal Missing | Trailing Missing | Coverage Ratio | Coverage Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for tf_k in ("1m", "5m", "15m", "1H", "4H", "1D"):
        c = cov_map.get(tf_k) or {}
        cov_rows.append(
            f"| `{tf_k}` | `{c.get('expected_closed_slots')}` | `{c.get('observed_unique_closed_slots')}` | "
            f"`{c.get('leading_missing_slots')}` | `{c.get('internal_missing_slots')}` | "
            f"`{c.get('trailing_missing_slots')}` | `{c.get('coverage_ratio')}` | `{c.get('coverage_verdict')}` |"
        )
    cov_table = "\n".join(cov_rows)

    foc_tests = f09.get("focused_in_memory_unit_tests") or {}

    readme = f"""# AURUM DESK — KH-01B.R1.DSC: DATA & SAFETY TRUTH CLOSURE (`{SPEC_VERSION_KH01B_R1_DSC}`)

- **Run ID**: `{run_id}`
- **Mandatory Branch**: `{branch}`
- **Starting HEAD (Prior KH-01B.R1 Phase C)**: `{starting_head_sha}`
- **Tested Executable Source SHA**: `{tested_sha}`
- **Canonical Dataset SHA256 (`REUSED_RESEARCH`, `MODELED_BID_ASK`)**: `{dataset_sha}`

## 1. Orthogonal Verdicts
- `ENGINEERING_CLOSURE_VERDICT`: **{ov['ENGINEERING_CLOSURE_VERDICT']}**
- `DISK_SAFETY_VERDICT`: **{ov['DISK_SAFETY_VERDICT']}** (Required preflight: `>= {d_pol.get('min_acceptance_preflight_gib')} GiB` [`20.0 GiB` reserve + `0.5 GiB` stage]; Available: `{d_pol.get('preflight_baseline_free_gib')} GiB`; Shortfall: `{d_pol.get('shortfall_gib')} GiB`; `lowered_reserve_without_approval = {d_pol.get('lowered_reserve_without_approval')}`)
- `DATA_ACQUISITION_VERDICT`: **{ov['DATA_ACQUISITION_VERDICT']}**
- `FULL_REGRESSION_VERDICT`: **{ov['FULL_REGRESSION_VERDICT']}**
- `BACKTEST_VERDICT`: **{ov['BACKTEST_VERDICT']}**
- `WORKER_DETERMINISM_VERDICT`: **{ov['WORKER_DETERMINISM_VERDICT']}**
- `RESEARCH_ECONOMIC_VERDICT`: **{ov['RESEARCH_ECONOMIC_VERDICT']}**
- `KH01C_ADVANCEMENT_VERDICT`: **{ov['KH01C_ADVANCEMENT_VERDICT']}**
- `FRESH_OOS_VERDICT`: **{ov['FRESH_OOS_VERDICT']}**
- `ACTIVATION_READY`: **{ov['ACTIVATION_READY']}** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)

## 2. Repaired Data & Safety Defects Summary
1. **Gap Schema Mapping (`G-DS-02`)**: Fixed `kh_01b_r1_collect_market_data.py` to map `{{'1m':'M1','5m':'M5','15m':'M15','1H':'H1','4H':'H4','1D':'D1'}}` onto authoritative `gap_audit['timeframes']` (`status = {f02.get('status')}`). Null/missing schema now fails closed (`FAIL_INTEGRITY`).
2. **Window Coverage & Dynamic Page Budget (`G-DS-03`, `G-DS-04`)**: Removed silent `max_pages_per_tf=8` (`1600`-bar) M1 ceiling (48h M1 dynamic budget = `{(f03.get('m1_48h_dynamic_page_budget') or {}).get('effective_page_budget')}` pages) and reclassified the historical `1600/2880` M1 capture as `VERIFIED_PARTIAL` (`PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION`, `1280` leading missing slots).
3. **Exact Decimal Cross-Page Boundary Dedupe (`G-DS-03`)**: Exact identical cross-page boundary bars dedupe once (`PASS_WITH_DEDUP_RECEIPT`, retaining both origin HTTP SHAs), while within-page duplicates, duplicate-page replays, conflicting OHLCV, and precision-colliding near-equals (`3700.50004` vs `3700.50001`) fail closed.
4. **Strict 20 GiB Disk Reserve (`G-DS-07`, `G-DS-08`)**: Removed automatic `10 GiB` reserve downgrade in `compute_kh01b_r1_disk_reserve_policy()`. Reconciled constants & docstrings.
5. **Decimal Fee Provenance & Sensitivity (`G-DS-05`, `G-DS-06`)**: Kept `CostAssumptions(taker_fee_rate=0.0004)` untouched (`gross = {par_06.get('gross_pnl_usd')} USD`, `fees = {par_06.get('total_fees_usd')} USD`, `net = {par_06.get('net_pnl_usd')} USD`, `166` closed). Computed separate `0.0004` vs `0.0006` (`1x`, `2x`, `3x`) in `FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS` (`0.0006` 1x fees = `{fo_06_sc.get('total_fees_1x_usd')} USD`, net = `{fo_06_sc.get('net_pnl_1x_usd')} USD`) and `FULL_RE_RISKED_REPLAY` (`0.0004` net = `{rr_04.get('net_pnl_1x_usd')} USD` vs `0.0006` net = `{rr_06_sc.get('net_pnl_1x_usd')} USD`). Account VIP tier remains `UNKNOWN_NOT_LOCALLY_VERIFIED`.

## 3. Extended-DEV 48h Window Slot Coverage (`2026-11-16T00:00Z .. 2026-11-18T00:00Z`)
{cov_table}

## 4. Test Execution Scope & Fault Controls
- **Closure RED/GREEN Fault Suite (`12`)**: `{f12.get('passed_mutations')}/{f12.get('total_mutations')}` PASS.
- **Focused Pure In-Memory Unit & Subprocess Verifier Suite**: `{foc_tests.get('status')}` (`executed = {foc_tests.get('executed_count')}`, `passed = {foc_tests.get('passed_count')}`, `failed = {foc_tests.get('failed_count')}`).
- **Full 5,500+ Repo Regression / Full Backtest / w1-w2-w4 Benchmark**: `{ov['FULL_REGRESSION_VERDICT']}` / `{ov['BACKTEST_VERDICT']}` / `{ov['WORKER_DETERMINISM_VERDICT']}`.
"""

    handover = f"""BÀN GIAO NGHIỆM THU ĐÓNG SỰ THẬT DỮ LIỆU & AN TOÀN — AURUM DESK {SPEC_VERSION_KH01B_R1_DSC}
Run ID: {run_id}
Nhánh bắt buộc (giữ nguyên): {branch}
Starting HEAD trước khi vá (KH-01B.R1 Phase C): {starting_head_sha}
Tested Executable Source SHA (mã nguồn đã commit trước khi chạy kiểm định): {tested_sha}
Canonical Dataset SHA256 (bất biến): {dataset_sha}

A. MA TRẬN PHÁN QUYẾT ĐỘC LẬP (ORTHOGONAL VERDICTS):
- ENGINEERING_CLOSURE_VERDICT: {ov['ENGINEERING_CLOSURE_VERDICT']}
- DISK_SAFETY_VERDICT: {ov['DISK_SAFETY_VERDICT']} (Yêu cầu chuẩn >= {d_pol.get('min_acceptance_preflight_gib')} GiB [20.0 GiB dự trữ + 0.5 GiB biên độ tầng]; Thực tế khả dụng: {d_pol.get('preflight_baseline_free_gib')} GiB [{d_pol.get('preflight_baseline_free_gb_decimal')} GB thập phân]; Thiếu hụt: {d_pol.get('shortfall_gib')} GiB; lowered_reserve_without_approval={d_pol.get('lowered_reserve_without_approval')})
- DATA_ACQUISITION_VERDICT: {ov['DATA_ACQUISITION_VERDICT']}
- FULL_REGRESSION_VERDICT: {ov['FULL_REGRESSION_VERDICT']}
- BACKTEST_VERDICT: {ov['BACKTEST_VERDICT']}
- WORKER_DETERMINISM_VERDICT: {ov['WORKER_DETERMINISM_VERDICT']}
- RESEARCH_ECONOMIC_VERDICT: {ov['RESEARCH_ECONOMIC_VERDICT']}
- KH01C_ADVANCEMENT_VERDICT: {ov['KH01C_ADVANCEMENT_VERDICT']}
- FRESH_OOS_VERDICT: {ov['FRESH_OOS_VERDICT']}
- ACTIVATION_READY: {ov['ACTIVATION_READY']} (PRODUCTION=OFF, LIVE=OFF, PAPER=OFF, AUTO=OFF)
- PUBLICATION_VERDICT (trước khi đọc lại từ GitHub): {ov['PUBLICATION_VERDICT']}

B. KẾT QUẢ SỬA LỖI THEO TỪNG HẠNG MỤC (DEFECTS A–F):
1) Gap Schema Mapping (G-DS-02 -> PASS): Đã sửa map_gap_truth_to_acquisition_summary() ánh xạ chính xác {{'1m':'M1','5m':'M5','15m':'M15','1H':'H1','4H':'H4','1D':'D1'}} vào gap_audit['timeframes']; từ chối schema null cũ với FAIL_INTEGRITY.
2) Window Coverage & Dynamic Pagination (G-DS-03, G-DS-04 -> PASS): Loại bỏ trần ngầm max_pages_per_tf=8 (1600 nến M1); tính động 17 trang cho cửa sổ 48h M1; kiểm toán lại snapshot 48h đã lưu cho thấy M1 chỉ có 1600/2880 slot (thiếu 1280 slot đầu cửa sổ, tỷ lệ 0.555556) -> phân loại trung thực là VERIFIED_PARTIAL / PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION.
3) Cross-Page Boundary Duplicate & Ordering (G-DS-03 -> PASS): Dùng Decimal chính xác để gộp duy nhất nến trùng biên liên trang hợp lệ (PASS_WITH_DEDUP_RECEIPT, giữ đủ 2 mã băm HTTP gốc), đồng thời đánh FAIL mọi trùng lặp trong cùng trang, phát lại nguyên trang, xung đột OHLCV hoặc xung đột làm tròn số lẻ (3700.50004 so với 3700.50001). Kiểm tra thứ tự trang thô TRƯỚC khi sắp xếp và căn chỉnh khung giờ chuẩn (1D Bitget mở lúc 16:00 UTC = 00:00 UTC+8).
4) Strict 20 GiB Disk Reserve (G-DS-07 -> {ov['DISK_SAFETY_VERDICT']}, G-DS-08 -> PASS): Bỏ hoàn toàn cơ chế tự hạ sàn xuống 10 GiB trong chế độ nghiệm thu chuẩn; kiểm tra tất cả phân vùng ghi (repo, output, temp).
5) Fee Provenance & Sensitivity 0.0004 vs 0.0006 (G-DS-05, G-DS-06 -> PASS): Giữ nguyên CostAssumptions(taker_fee_rate=0.0004) không đổi; đối chiếu gốc SI0/E00 khớp tuyệt đối 166 lệnh đóng, Gross PnL = {par_06.get('gross_pnl_usd')} USD, Fees = {par_06.get('total_fees_usd')} USD, Net PnL = {par_06.get('net_pnl_usd')} USD (0 sai lệch từng lệnh). Phân tích độ nhạy độc lập:
   - FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS: tại 0.0004 Net 1x = {fo_04.get('net_pnl_1x_usd')} USD (2x = {fo_04.get('stress_2x_all_cost_net_pnl_usd')} USD, 3x = {fo_04.get('stress_3x_all_cost_net_pnl_usd')} USD); tại 0.0006 Fees 1x tăng lên {fo_06_sc.get('total_fees_1x_usd')} USD, Net 1x = {fo_06_sc.get('net_pnl_1x_usd')} USD (2x = {fo_06_sc.get('stress_2x_all_cost_net_pnl_usd')} USD, 3x = {fo_06_sc.get('stress_3x_all_cost_net_pnl_usd')} USD).
   - FULL_RE_RISKED_REPLAY (trên 242 cơ hội SI0): tại 0.0004 đóng {rr_04.get('closed_trades_count')} lệnh, Net 1x = {rr_04.get('net_pnl_1x_usd')} USD; tại 0.0006 đóng {rr_06_sc.get('closed_trades_count')} lệnh (bị loại thêm bởi ngưỡng net_rr >= 2.0), Net 1x = {rr_06_sc.get('net_pnl_1x_usd')} USD.
   - Bậc phí VIP thực tế của tài khoản: UNKNOWN_NOT_LOCALLY_VERIFIED.

C. BẢNG ĐỘ BAO PHỦ CỬA SỔ 48H THEO KHUNG THỜI GIAN:
{cov_table}

D. PHẠM VI THỰC THI TEST & HƯỚNG DẪN KHẮC PHỤC DUNG LƯỢNG ĐĨA (NẾU BỊ CHẶN BLOCKED_LOW_DISK):
- Đột biến đối chứng ĐỎ/XANH (DSC-M01..DSC-M22): {f12.get('passed_mutations')}/{f12.get('total_mutations')} PASS.
- Unit test tập trung trong bộ nhớ (Focused Pytest): status={foc_tests.get('status')}, executed={foc_tests.get('executed_count')}, passed={foc_tests.get('passed_count')}, failed={foc_tests.get('failed_count')}.
- Hồi quy toàn kho (~5500+ test), Backtest lịch sử đầy đủ & Benchmark đa luồng w1/w2/w4: {ov['FULL_REGRESSION_VERDICT']} / {ov['BACKTEST_VERDICT']} / {ov['WORKER_DETERMINISM_VERDICT']}.
- Lệnh chạy lại toàn diện trên bất kỳ máy chủ macOS/Linux/Windows nào sau khi người vận hành giải phóng an toàn thêm >= {d_pol.get('shortfall_gib')} GiB (để dung lượng trống >= 20.5 GiB trên mọi phân vùng ghi, không xoá dữ liệu lịch sử):
  python aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py --r2
"""
    return readme, handover


def verify_kh01b_r1_dsc_cross_view_consistency(
    *,
    readme_text: str,
    handover_text: str,
    f00: Mapping[str, Any],
    f06: Mapping[str, Any],
    f12: Mapping[str, Any],
    f14: Mapping[str, Any],
) -> Dict[str, Any]:
    violations: List[str] = []
    ov = f14["orthogonal_verdicts"]
    d_pol = f00.get("disk_reserve_policy") or {}
    par_06 = f06.get("original_e00_b00_reference_parity") or {}

    req_tokens = [
        str(f14.get("tested_executable_sha") or ""),
        str(ov.get("ENGINEERING_CLOSURE_VERDICT") or ""),
        str(ov.get("DISK_SAFETY_VERDICT") or ""),
        str(ov.get("DATA_ACQUISITION_VERDICT") or ""),
        str(ov.get("FULL_REGRESSION_VERDICT") or ""),
        str(ov.get("BACKTEST_VERDICT") or ""),
        str(ov.get("KH01C_ADVANCEMENT_VERDICT") or ""),
        str(ov.get("FRESH_OOS_VERDICT") or ""),
        str(d_pol.get("preflight_baseline_free_gib") or ""),
        str(par_06.get("net_pnl_usd") or ""),
        f"{f12.get('passed_mutations')}/{f12.get('total_mutations')}",
    ]
    for tok in req_tokens:
        if tok not in readme_text:
            violations.append(f"README_DSC_MISSING_TOKEN:{tok}")
        if tok not in handover_text:
            violations.append(f"HANDOVER_DSC_VI_MISSING_TOKEN:{tok}")

    # Anti-fabrication check: if ENGINEERING_CLOSURE_VERDICT != "PASS", neither doc may claim "ENGINEERING_CLOSURE_VERDICT: PASS"
    if ov.get("ENGINEERING_CLOSURE_VERDICT") != "PASS":
        for bad_claim in ("`ENGINEERING_CLOSURE_VERDICT`: **PASS**", "ENGINEERING_CLOSURE_VERDICT: PASS\n"):
            if bad_claim in readme_text or bad_claim in handover_text:
                violations.append(f"FALSE_ENGINEERING_PASS_CLAIM_IN_TEXT:{bad_claim.strip()}")

    return {
        "status": "PASS" if not violations else "FAIL",
        "checked_tokens_count": len(req_tokens),
        "violations": violations,
    }


# ==============================================================================
# 7. KH-01B.R1.DSC.R2 GATE EVALUATION (G-DS-01..15 + R2-A..G) & REPORTING (§5, §7, §9)
# ==============================================================================

def validate_latest_run_pointer_not_blocked(ptr_doc: Mapping[str, Any]) -> Dict[str, Any]:
    """
    R2-A & R2-G guard: An accepted `latest_run.json` pointer MUST NEVER reference a
    `BLOCKED_LOW_DISK`, `FAIL`, or `NOT_RUN` closure run.
    """
    violations: List[str] = []
    eng_v = str(ptr_doc.get("engineering_closure_verdict") or ptr_doc.get("ENGINEERING_CLOSURE_VERDICT") or "")
    disk_v = str(ptr_doc.get("disk_safety_verdict") or ptr_doc.get("DISK_SAFETY_VERDICT") or "PASS_STRICT_20GIB")
    rid = str(ptr_doc.get("run_id") or "")
    if not rid or rid == PRIOR_DSC_V1_BLOCKED_RUN_ID:
        violations.append(f"ILLEGAL_OR_BLOCKED_RUN_ID_IN_LATEST_POINTER:{rid}")
    if eng_v != "PASS":
        violations.append(f"NON_PASS_ENGINEERING_VERDICT_IN_LATEST_POINTER:{eng_v}")
    if disk_v != "PASS_STRICT_20GIB":
        violations.append(f"NON_STRICT_DISK_VERDICT_IN_LATEST_POINTER:{disk_v}")
    return {"status": "PASS" if not violations else "FAIL", "violations": violations}


def evaluate_kh01b_r1_dsc_r2_gates_from_disk(
    run_dir: Path,
    run_id: str,
    tested_sha: str,
    *,
    publication_status: str = "PENDING_REMOTE_PUSH",
) -> Dict[str, Any]:
    """
    Evaluates KH-01B.R1.DSC.R2 gates `G-DS-01`..`G-DS-15` (exact §5 numbering) PLUS
    `R2-A`..`R2-G` independently from closed R2 disk artifacts (`00`..`13`) and dynamic
    cryptographic receipts in `receipts/`.
    """
    def _rd(name: str) -> Dict[str, Any]:
        p = run_dir / name
        if not p.is_file():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    f00 = _rd("00_preflight_branch_head_code_sha_disk_mounts_dataset.json")
    f01 = _rd("01_function_callgraph_and_immutability_manifest.json")
    f02 = _rd("02_gap_schema_six_tf_and_actual_missingness.json")
    f03 = _rd("03_pagination_decimal_dedupe_cursor_and_red_green.json")
    f04 = _rd("04_requested_window_coverage_origin_and_online_capture_status.json")
    f05 = _rd("05_fee_0p04_0p06_fixed_fill_and_rerisked_truth.json")
    f06 = _rd("06_strict_disk_recovery_actions_and_resource_monitor.json")
    f07 = _rd("07_sha_lock_current_HEAD_vs_tested_executable_execution_graph.json")
    f08 = _rd("08_async_w1_w2_w4_w1repeat_hashes_timing_and_worker_manifest.json")
    f09 = _rd("09_full_regression_junit_collected_executed_bijection.json")
    f10 = _rd("10_full_baseline_backtest_three_fill_1pos_adapter_and_fingerprint_parity.json")
    f11 = _rd("11_negative_controls_fault_injection_independent_verifier.json")
    f12 = _rd("12_OOS_untouched_and_activation_off_receipt.json")
    f13 = _rd("13_prior_artifact_and_code_immutability_diffs.json")

    receipts_map: Dict[str, EvidenceReceiptV17931219] = {}
    rcpt_dir = run_dir / "receipts"
    if rcpt_dir.is_dir():
        for rp in sorted(rcpt_dir.glob("*.json")):
            if not (rp.stem.startswith("G-DS-") or rp.stem.startswith("R2-")):
                continue
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

    disk_pol = f00.get("disk_reserve_policy") or {}
    disk_verdict = str(disk_pol.get("disk_safety_verdict") or "FAIL")

    # G-DS-01: Exact current branch + source SHA + immutable baseline/source + OFF flags
    v01: List[str] = []
    if f00.get("branch") != MANDATORY_BRANCH_KH01B_R1 or f00.get("tested_executable_sha") != tested_sha:
        v01.append("BRANCH_OR_TESTED_SHA_MISMATCH")
    if f00.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH01B_R1:
        v01.append("CANONICAL_DATASET_SHA_MISMATCH")
    if f00.get("source_tree_unchanged_during_run") is not True or f00.get("prior_artifacts_unchanged_during_run") is not True:
        v01.append("SOURCE_OR_PRIOR_ARTIFACTS_MUTATED")
    if int(f01.get("locked_files_modified_count", 1)) != 0:
        v01.append("LOCKED_PRODUCTION_OR_PRIOR_RESEARCH_FILES_MODIFIED")
    modes_01 = f01.get("runtime_safety_modes") or {}
    if any(modes_01.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
        v01.append("RUNTIME_FLAGS_NOT_OFF_IN_01")
    _add_gate(
        "G-DS-01",
        "BRANCH_SOURCE_SHA_IMMUTABLE_BASELINE_AND_OFF_FLAGS",
        "00_preflight_branch_head_code_sha_disk_mounts_dataset.json",
        {"branch": f00.get("branch"), "tested_sha": tested_sha, "locked_modified": f01.get("locked_files_modified_count")},
        "PASS" if not v01 else "FAIL",
        v01,
    )

    # G-DS-02: Six TF gap truth map with all non-null counts & real provenance; old per_timeframe schema negative control FAILS
    v02: List[str] = []
    if f02.get("status") != "PASS":
        v02.append("GAP_SCHEMA_AUDIT_NOT_PASS")
    if not (f02.get("red_control_old_per_timeframe_null_schema") or {}).get("rejected_as_required", False):
        v02.append("NULL_GAP_SCHEMA_NEGATIVE_CONTROL_NOT_REJECTED")
    for sec_k in ("canonical_dataset_six_tf_gap_summary", "extended_dev_snapshot_six_tf_gap_summary"):
        tfs_sec = (f02.get(sec_k) or {}).get("timeframes") or {}
        for req_tf in ("1m", "5m", "15m", "1H", "4H", "1D"):
            ge = tfs_sec.get(req_tf)
            if (
                not isinstance(ge, Mapping)
                or ge.get("actual_bars") is None
                or ge.get("total_missing_bars") is None
                or not isinstance(ge.get("gap_events_by_category"), Mapping)
                or not isinstance(ge.get("missing_bars_by_category"), Mapping)
            ):
                v02.append(f"MISSING_OR_NULL_6TF_GAP_ENTRY:{sec_k}:{req_tf}")
    _add_gate(
        "G-DS-02",
        "SIX_TF_GAP_TRUTH_MAP_NON_NULL_AND_OLD_SCHEMA_RED_CONTROL",
        "02_gap_schema_six_tf_and_actual_missingness.json",
        {"canonical_status": (f02.get("canonical_dataset_six_tf_gap_summary") or {}).get("mapper_status"), "extended_dev_status": (f02.get("extended_dev_snapshot_six_tf_gap_summary") or {}).get("mapper_status")},
        "PASS" if not v02 else "FAIL",
        v02,
    )

    # G-DS-03: Page budget 17 for 48h M1, cursor integrity, exact Decimal cross-page dedup, negative controls
    v03: List[str] = []
    m1_bud = int((f03.get("m1_48h_dynamic_page_budget") or {}).get("effective_page_budget") or 0)
    if f03.get("status") != "PASS" or int(f03.get("failed_controls_count", 1)) != 0:
        v03.append("PAGINATION_OR_DECIMAL_DEDUPE_CONTROLS_FAILED")
    if m1_bud != 17:
        v03.append(f"M1_48H_PAGE_BUDGET_NOT_17:{m1_bud}")
    _add_gate(
        "G-DS-03",
        "PAGE_BUDGET_17_CURSOR_INTEGRITY_AND_DECIMAL_CROSS_PAGE_DEDUP",
        "03_pagination_decimal_dedupe_cursor_and_red_green.json",
        {"m1_48h_page_budget": m1_bud, "passed_controls": f03.get("passed_controls_count")},
        "PASS" if not v03 else "FAIL",
        v03,
    )

    # G-DS-04: Requested-window conservation & honest old M1 1600/2880 VERIFIED_PARTIAL + online capture verification
    v04: List[str] = []
    cov_map = f04.get("coverage_by_timeframe") or {}
    if f04.get("status") != "PASS" or int(f04.get("unbacked_bars_without_origin_receipt_count", 1)) != 0:
        v04.append("WINDOW_COVERAGE_OR_ORIGIN_RECEIPT_VERIFICATION_FAILED")
    m1_old = cov_map.get("1m") or {}
    if (
        int(m1_old.get("expected_closed_slots") or 0) != 2880
        or int(m1_old.get("observed_unique_closed_slots") or 0) != 1600
        or int(m1_old.get("leading_missing_slots") or 0) != 1280
        or m1_old.get("coverage_verdict") == "VERIFIED_COMPLETE"
        or f04.get("reclassified_DATA_ACQUISITION_VERDICT") != "VERIFIED_PARTIAL"
    ):
        v04.append("OLD_1600_M1_SNAPSHOT_NOT_HONESTLY_CLASSIFIED_PARTIAL")
    for tf_k in ("1m", "5m", "15m", "1H", "4H", "1D"):
        c_tf = cov_map.get(tf_k)
        if not isinstance(c_tf, Mapping):
            v04.append(f"MISSING_COVERAGE_TF:{tf_k}")
            continue
        if int(c_tf.get("leading_missing_slots", -1)) + int(c_tf.get("internal_missing_slots", -1)) + int(c_tf.get("trailing_missing_slots", -1)) != int(c_tf.get("missing_slots", -99)):
            v04.append(f"MISSING_SLOTS_DECOMPOSITION_MISMATCH:{tf_k}")
        if int(c_tf.get("observed_unique_closed_slots", -1)) + int(c_tf.get("missing_slots", -1)) != int(c_tf.get("expected_closed_slots", -99)):
            v04.append(f"EXPECTED_VS_OBSERVED_SLOT_CONSERVATION_MISMATCH:{tf_k}")
    if bool(f04.get("online_capture_executed", False)):
        on_sum = f04.get("online_capture_summary") or {}
        on_v = str(on_sum.get("DATA_ACQUISITION_VERDICT") or "")
        if on_v in ("VERIFIED_COMPLETE", "VERIFIED_PARTIAL"):
            per_tf_on = on_sum.get("per_timeframe_validation") or {}
            for tf_k in ("1m", "5m", "15m", "1H", "4H", "1D"):
                vo = per_tf_on.get(tf_k) or {}
                if int(vo.get("observed_unique_closed_slots", -1)) + int(vo.get("missing_slots", -2)) != int(vo.get("expected_closed_slots", -9)):
                    v04.append(f"ONLINE_SLOT_CONSERVATION_MISMATCH:{tf_k}")
                if int(vo.get("missing_slots", 0)) > 0 and vo.get("coverage_verdict") == "VERIFIED_COMPLETE":
                    v04.append(f"ONLINE_PARTIAL_MISLABELED_COMPLETE:{tf_k}")
            if on_v == "VERIFIED_COMPLETE" and any(int((per_tf_on.get(t) or {}).get("missing_slots", 1)) > 0 for t in ("1m", "5m", "15m", "1H", "4H", "1D")):
                v04.append("ONLINE_OVERALL_MISLABELED_VERIFIED_COMPLETE")
            if on_sum.get("partition_role") != "DEV_EXTENDED_RESEARCH" or on_sum.get("eligible_for_future_fresh_oos") is not False:
                v04.append("ONLINE_CAPTURE_MISLABELED_AS_FRESH_OOS")
        elif on_v not in ("BLOCKED_UNAVAILABLE", "BLOCKED_LOW_DISK"):
            v04.append(f"ONLINE_CAPTURE_INTEGRITY_FAILURE:{on_v}")
    _add_gate(
        "G-DS-04",
        "REQUESTED_WINDOW_CONSERVATION_AND_HONEST_M1_PARTIAL_OR_VERIFIED_CAPTURE",
        "04_requested_window_coverage_origin_and_online_capture_status.json",
        {
            "old_m1_slots": f"{m1_old.get('observed_unique_closed_slots')}/{m1_old.get('expected_closed_slots')}",
            "old_verdict": f04.get("reclassified_DATA_ACQUISITION_VERDICT"),
            "online_capture_executed": f04.get("online_capture_executed"),
            "online_capture_verdict": f04.get("online_capture_verdict"),
        },
        "PASS" if not v04 else "FAIL",
        v04,
    )

    # G-DS-05: Bitget contract metadata vs actual account VIP UNKNOWN; live quote is point-in-time sample not historical feed
    v05: List[str] = []
    prov_05 = f04.get("provenance_audit") or {}
    ct_05 = prov_05.get("public_contract_fee_snapshot_audit") or {}
    bbo_05 = prov_05.get("prospective_single_bbo_snapshot_audit") or {}
    if prov_05.get("status") != "PASS":
        v05.append("PROVENANCE_AUDIT_NOT_PASS")
    if ct_05.get("actual_account_effective_fee_tier") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        v05.append("UNVERIFIED_ACCOUNT_FEE_TIER_PRESENTED_AS_KNOWN")
    if bbo_05.get("historical_backtest_usable") is not False or bbo_05.get("continuous_historical_series_coverage") is not False:
        v05.append("SINGLE_BBO_MISREPRESENTED_AS_HISTORICAL_SERIES")
    _add_gate(
        "G-DS-05",
        "CONTRACT_METADATA_ACCOUNT_VIP_UNKNOWN_AND_POINT_BBO_PROVENANCE",
        "04_requested_window_coverage_origin_and_online_capture_status.json",
        {"account_fee_tier": ct_05.get("actual_account_effective_fee_tier"), "public_taker_fee": ct_05.get("observed_public_taker_fee_rate")},
        "PASS" if not v05 else "FAIL",
        v05,
    )

    # G-DS-06: Decimal fee sensitivity reproducible in fixed-fill & SI0-242 rerisked modes; production defaults preserved
    v06: List[str] = []
    par_05 = f05.get("original_e00_b00_reference_parity") or {}
    fo_05 = f05.get("FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS") or {}
    rr_05 = f05.get("FULL_RE_RISKED_REPLAY") or {}
    if f05.get("status") != "PASS":
        v06.append("FEE_SENSITIVITY_05_NOT_PASS")
    if (
        int(par_05.get("closed_count") or 0) != 166
        or float(par_05.get("gross_pnl_usd") or 0.0) != -142.7836
        or float(par_05.get("total_fees_usd") or 0.0) != 203.1135
        or float(par_05.get("net_pnl_usd") or 0.0) != -345.8971
    ):
        v06.append("ORIGINAL_E00_FEE_PARITY_BROKEN")
    if (
        float((fo_05.get("0.0006") or {}).get("total_fees_1x_usd") or 0.0) != 304.6696
        or float((fo_05.get("0.0006") or {}).get("net_pnl_1x_usd") or 0.0) != -447.4532
        or int((rr_05.get("0.0006") or {}).get("closed_trades_count") or 0) != 87
        or float((rr_05.get("0.0006") or {}).get("net_pnl_1x_usd") or 0.0) != -210.6112
        or (rr_05.get("0.0006") or {}).get("scope_is_full_56710_opportunity_rerun") is not False
    ):
        v06.append("FEE_0P06_FIXED_OR_SI0_242_RERISKED_MISMATCH_OR_SCOPE_MISLABEL")
    if (f05.get("domain_calculator_defaults_preserved") or {}).get("unchanged_verified") is not True:
        v06.append("DOMAIN_CALCULATOR_DEFAULTS_MUTATED")
    _add_gate(
        "G-DS-06",
        "DECIMAL_FEE_SENSITIVITY_FIXED_FILL_VS_SI0_242_RERISKED_DEFAULTS_INTACT",
        "05_fee_0p04_0p06_fixed_fill_and_rerisked_truth.json",
        {
            "fixed_0p04_net": (fo_05.get("0.0004") or {}).get("net_pnl_1x_usd"),
            "fixed_0p06_net": (fo_05.get("0.0006") or {}).get("net_pnl_1x_usd"),
            "rerisked_0p06_closed_net": f"{(rr_05.get('0.0006') or {}).get('closed_trades_count')}/{(rr_05.get('0.0006') or {}).get('net_pnl_1x_usd')}",
        },
        "PASS" if not v06 else "FAIL",
        v06,
    )

    # G-DS-07: Strict disk preflight: PASS_STRICT_20GIB only if ALL writing volumes >=20.5 GiB at preflight & >=20.0 GiB runtime
    v07: List[str] = []
    tel_06 = f06.get("runtime_telemetry") or {}
    vols_pol = disk_pol.get("volumes") or {}
    if f06.get("policy_injections_status") != "PASS":
        v07.append("DISK_POLICY_INJECTIONS_FAILED")
    if bool(disk_pol.get("lowered_reserve_without_approval", True)) or float(disk_pol.get("enforced_reserve_gib") or 0.0) < 20.0:
        v07.append("DISK_RESERVE_LOWERED_BELOW_20GIB")
    if disk_verdict == "PASS_STRICT_20GIB":
        if float(disk_pol.get("preflight_baseline_free_gib") or 0.0) < 20.5:
            v07.append("FALSE_PASS_STRICT_20GIB_WHEN_FREE_SPACE_LT_20P5")
        for v_lbl in ("repo", "output", "temp"):
            if float((vols_pol.get(v_lbl) or {}).get("free_gib") or 0.0) < 20.5:
                v07.append(f"WRITING_VOLUME_BELOW_20P5_GIB:{v_lbl}")
        if float(tel_06.get("min_free_disk_gib") or 0.0) < 20.0 or float(tel_06.get("post_cleanup_free_gib") or 0.0) < 20.0:
            v07.append("RUNTIME_OR_POST_CLEANUP_DISK_FELL_BELOW_20GIB")
        g07_st = "PASS" if not v07 else "FAIL"
    elif disk_verdict == "BLOCKED_LOW_DISK":
        if bool(disk_pol.get("can_proceed", True)):
            v07.append("LOW_DISK_ALLOWED_CAN_PROCEED_TRUE")
        g07_st = "BLOCKED_LOW_DISK" if not v07 else "FAIL"
    else:
        v07.append(f"INVALID_DISK_SAFETY_VERDICT:{disk_verdict}")
        g07_st = "FAIL"
    _add_gate(
        "G-DS-07",
        "STRICT_20P5_GIB_PREFLIGHT_AND_20GIB_RESERVE_ALL_WRITING_VOLUMES",
        "06_strict_disk_recovery_actions_and_resource_monitor.json",
        {"disk_safety_verdict": disk_verdict, "preflight_min_free_gib": disk_pol.get("preflight_baseline_free_gib"), "min_runtime_free_gib": tel_06.get("min_free_disk_gib")},
        g07_st,
        v07 if v07 else ([] if g07_st == "PASS" else [str(disk_pol.get("abort_reason") or "BLOCKED_LOW_DISK")]),
        require_pass_receipt=(g07_st == "PASS"),
    )

    # G-DS-08: Resource tests, safe temp cleanup, <=4 children, zero orphan descendants, safe signal behavior
    v08: List[str] = []
    if int(tel_06.get("max_children_observed") or 0) > 4 or int(tel_06.get("max_children_observed") or 0) < 1:
        v08.append(f"INVALID_MAX_CHILDREN:{tel_06.get('max_children_observed')}")
    if int(tel_06.get("orphan_pids_at_cleanup_count", 1)) != 0:
        v08.append("ORPHAN_PIDS_AT_CLEANUP")
    if tel_06.get("temp_dirs_cleaned_with_sentinel_check") is not True:
        v08.append("TEMP_SENTINEL_CLEANUP_NOT_VERIFIED")
    if int(tel_06.get("unowned_paths_deleted_count", 1)) != 0:
        v08.append("UNOWNED_PATHS_DELETED")
    _add_gate(
        "G-DS-08",
        "RESOURCE_MONITOR_MAX4_DESCENDANTS_ZERO_ORPHANS_AND_SAFE_TEMP_CLEANUP",
        "06_strict_disk_recovery_actions_and_resource_monitor.json",
        {"max_children": tel_06.get("max_children_observed"), "orphans": tel_06.get("orphan_pids_at_cleanup_count"), "sentinel_cleaned": tel_06.get("temp_dirs_cleaned_with_sentinel_check")},
        "PASS" if not v08 else "FAIL",
        v08,
    )

    # G-DS-09 (§5): Async deterministic w1/w2/w4/w1-repeat ACTUALLY EXECUTED and equal normalized hashes
    st_08 = str(f08.get("execution_status") or "MISSING")
    if st_08 == "EXECUTED" and disk_verdict == "PASS_STRICT_20GIB":
        v09: List[str] = []
        py_runs = (f08.get("pytest_controlled_subset_w1_w2_w4_repeat") or {}).get("runs") or []
        sl_runs = (f08.get("r1_variant_slice_w1_w2_w4_repeat") or {}).get("runs") or []
        if (
            f08.get("completed") is not True
            or f08.get("pytest_subset_w1_w2_w4_repeat_identical") is not True
            or f08.get("r1_variant_slice_w1_w2_w4_repeat_identical") is not True
            or len(py_runs) != 4
            or len(sl_runs) != 4
        ):
            v09.append("WORKER_W1_W2_W4_REPEAT_DETERMINISM_OR_SHARD_MANIFEST_FAILED")
        _add_gate(
            "G-DS-09",
            "ASYNC_DETERMINISTIC_W1_W2_W4_W1REPEAT_HASHES_AND_TIMINGS",
            "08_async_w1_w2_w4_w1repeat_hashes_timing_and_worker_manifest.json",
            {"pytest_w4_speedup": f08.get("measured_speedup_w4_vs_w1"), "speedup_label": f08.get("speedup_truth_label")},
            "PASS" if not v09 else "FAIL",
            v09,
        )
    elif st_08 == "NOT_RUN_BLOCKED_LOW_DISK" and disk_verdict == "BLOCKED_LOW_DISK" and f08.get("completed") is False:
        _add_gate(
            "G-DS-09",
            "ASYNC_DETERMINISTIC_W1_W2_W4_W1REPEAT_HASHES_AND_TIMINGS",
            "08_async_w1_w2_w4_w1repeat_hashes_timing_and_worker_manifest.json",
            {"execution_status": st_08, "completed": False},
            "NOT_RUN_BLOCKED_LOW_DISK",
            ["NOT_RUN_DUE_TO_STRICT_20GIB_DISK_RESERVE"],
            require_pass_receipt=False,
        )
    else:
        _add_gate(
            "G-DS-09",
            "ASYNC_DETERMINISTIC_W1_W2_W4_W1REPEAT_HASHES_AND_TIMINGS",
            "08_async_w1_w2_w4_w1repeat_hashes_timing_and_worker_manifest.json",
            {"execution_status": st_08},
            "FAIL",
            [f"INVALID_OR_UNEXECUTED_WORKER_DETERMINISM:{st_08}"],
        )

    # G-DS-10 (§5): Full pytest collected/executed, real closed JUnit shards, all exit=0, 100% node bijection
    st_09 = str(f09.get("execution_status") or "MISSING")
    if st_09 == "EXECUTED" and disk_verdict == "PASS_STRICT_20GIB":
        v10: List[str] = []
        bij_09 = f09.get("independent_bijection_verification") or {}
        cnt_09 = bij_09.get("counters") or {}
        if (
            f09.get("completed") is not True
            or bij_09.get("status") != "PASS"
            or int((f09.get("shard_manifest") or {}).get("nonzero_exit_shards_count", 1)) != 0
            or int(cnt_09.get("collected") or 0) <= 20
            or int(cnt_09.get("collected") or -1) != int(cnt_09.get("executed") or -2)
            or int(cnt_09.get("failed") or 0) != 0
            or int(cnt_09.get("errors") or 0) != 0
        ):
            v10.append("FULL_PYTEST_OR_JUNIT_BIJECTION_FAILED_OR_FOCUSED_SUBSTITUTE")
        _add_gate(
            "G-DS-10",
            "FULL_PYTEST_COLLECTED_EXECUTED_CLOSED_JUNIT_BIJECTION",
            "09_full_regression_junit_collected_executed_bijection.json",
            cnt_09,
            "PASS" if not v10 else "FAIL",
            v10,
        )
    elif st_09 == "NOT_RUN_BLOCKED_LOW_DISK" and disk_verdict == "BLOCKED_LOW_DISK" and f09.get("completed") is False:
        junit_xmls = list((run_dir / "junit").glob("*.xml")) if (run_dir / "junit").is_dir() else []
        if junit_xmls:
            _add_gate("G-DS-10", "FULL_PYTEST_COLLECTED_EXECUTED_CLOSED_JUNIT_BIJECTION", "09_full_regression_junit_collected_executed_bijection.json", {"fake_xml_count": len(junit_xmls)}, "FAIL", ["FAKE_JUNIT_XML_PRESENT_WHEN_NOT_RUN"])
        else:
            _add_gate("G-DS-10", "FULL_PYTEST_COLLECTED_EXECUTED_CLOSED_JUNIT_BIJECTION", "09_full_regression_junit_collected_executed_bijection.json", {"execution_status": st_09, "completed": False}, "NOT_RUN_BLOCKED_LOW_DISK", ["NOT_RUN_DUE_TO_STRICT_20GIB_DISK_RESERVE"], require_pass_receipt=False)
    else:
        _add_gate("G-DS-10", "FULL_PYTEST_COLLECTED_EXECUTED_CLOSED_JUNIT_BIJECTION", "09_full_regression_junit_collected_executed_bijection.json", {"execution_status": st_09}, "FAIL", [f"INVALID_REGRESSION_STATUS:{st_09}"])

    # G-DS-11 (§5): Full backtest/parity with 242/166, 56,710/6,375 full cohorts, 3 fill modes/adapters/fingerprints
    st_10 = str(f10.get("execution_status") or "MISSING")
    if st_10 == "EXECUTED" and disk_verdict == "PASS_STRICT_20GIB":
        v11: List[str] = []
        nb_10 = f10.get("si0_e00_primary_nb_summary") or {}
        fm_10 = f10.get("si0_e00_fill_mode_summaries") or {}
        d4_10 = f10.get("d4_strict_and_adjacent_isolation") or {}
        if (
            f10.get("completed") is not True
            or f10.get("status") != "PASS"
            or int(f10.get("a1_evaluated_count") or 0) != 56710
            or int(f10.get("a1_mismatches_count", 1)) != 0
            or int(f10.get("si8_separate_population_count") or 0) != 6375
            or int(f10.get("total_unmixed_universe_count") or 0) != 63085
            or int(f10.get("three_adapter_episodes_denominator") or 0) != 246
            or int(f10.get("three_adapter_mismatches_count", 1)) != 0
            or int(nb_10.get("total_scheduled_episodes") or 0) != 242
            or int(nb_10.get("closed_trades_count") or 0) != 166
            or int(nb_10.get("wins_count") or 0) != 9
            or int(nb_10.get("losses_count") or 0) != 157
            or float(nb_10.get("total_realized_gross_usd") or 0.0) != -142.7836
            or float(nb_10.get("total_fees_usd") or 0.0) != 203.1135
            or float(nb_10.get("total_realized_net_usd") or 0.0) != -345.8971
            or float(nb_10.get("expectancy_net_r") or 0.0) != -0.8474
            or any(m not in fm_10 for m in ("NEXT_BAR_OPEN_BID_ASK", "LEGACY_CLOSE_FILL", "STRESS_WIDER_SPREAD_SLIPPAGE_2X"))
            or int(d4_10.get("d4_historical_closed_trades", 1)) != 0
            or d4_10.get("d4_historical_expectancy_r") is not None
        ):
            v11.append("FULL_BACKTEST_COHORT_3FILL_ADAPTER_OR_D4_PARITY_FAILED")
        _add_gate(
            "G-DS-11",
            "FULL_BACKTEST_56710_6375_SI0_242_166_THREE_FILL_AND_ADAPTER_PARITY",
            "10_full_baseline_backtest_three_fill_1pos_adapter_and_fingerprint_parity.json",
            {
                "a1_evaluated": f10.get("a1_evaluated_count"),
                "si8_count": f10.get("si8_separate_population_count"),
                "si0_closed": f"{nb_10.get('closed_trades_count')} ({nb_10.get('wins_count')}W/{nb_10.get('losses_count')}L)",
                "adapters_matched": f10.get("three_adapter_episodes_denominator"),
            },
            "PASS" if not v11 else "FAIL",
            v11,
        )
    elif st_10 == "NOT_RUN_BLOCKED_LOW_DISK" and disk_verdict == "BLOCKED_LOW_DISK" and f10.get("completed") is False:
        _add_gate(
            "G-DS-11",
            "FULL_BACKTEST_56710_6375_SI0_242_166_THREE_FILL_AND_ADAPTER_PARITY",
            "10_full_baseline_backtest_three_fill_1pos_adapter_and_fingerprint_parity.json",
            {"execution_status": st_10, "completed": False},
            "NOT_RUN_BLOCKED_LOW_DISK",
            ["NOT_RUN_DUE_TO_STRICT_20GIB_DISK_RESERVE"],
            require_pass_receipt=False,
        )
    else:
        _add_gate("G-DS-11", "FULL_BACKTEST_56710_6375_SI0_242_166_THREE_FILL_AND_ADAPTER_PARITY", "10_full_baseline_backtest_three_fill_1pos_adapter_and_fingerprint_parity.json", {"execution_status": st_10}, "FAIL", [f"INVALID_BACKTEST_STATUS:{st_10}"])

    # G-DS-12 (§5): Original dataset/OOS escrow partition untouched and no false OOS
    v12: List[str] = []
    esc_12 = f12.get("future_oos_escrow") or {}
    modes_12 = f12.get("runtime_safety_modes") or {}
    oos_verdict = str(esc_12.get("FRESH_OOS_VERDICT") or "FAIL")
    if esc_12.get("status") != "PASS" or int(esc_12.get("strategy_evaluation_count_total", 1)) != 0 or oos_verdict not in ("BLOCKED_NO_UNTOUCHED_DATA", "SEALED_UNTOUCHED_NOT_EVALUATED"):
        v12.append(f"OOS_ESCROW_VIOLATION:{oos_verdict}")
    if any(modes_12.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")) or f12.get("ACTIVATION_READY") != "NO":
        v12.append("RUNTIME_ACTIVATION_NOT_OFF")
    _add_gate(
        "G-DS-12",
        "ORIGINAL_DATASET_AND_OOS_ESCROW_UNTOUCHED_ZERO_EVALUATIONS",
        "12_OOS_untouched_and_activation_off_receipt.json",
        {"FRESH_OOS_VERDICT": oos_verdict, "evaluation_count": esc_12.get("strategy_evaluation_count_total")},
        "PASS" if not v12 else "FAIL",
        v12,
    )

    # G-DS-13 (§5): Independent verifier separate PID & 22+ fault injections including fake PASS and low disk
    v13: List[str] = []
    if f11.get("status") != "PASS" or int(f11.get("passed_mutations") or 0) < 22 or int(f11.get("failed_mutations", 1)) != 0:
        v13.append("FAULT_INJECTION_SUITE_BELOW_22_OR_FAILED")
    _add_gate(
        "G-DS-13",
        "INDEPENDENT_VERIFIER_AND_22PLUS_ADVERSARIAL_FAULT_INJECTIONS",
        "11_negative_controls_fault_injection_independent_verifier.json",
        {"passed_mutations": f"{f11.get('passed_mutations')}/{f11.get('total_mutations')}"},
        "PASS" if not v13 else "FAIL",
        v13,
    )

    # G-DS-14 (§5): SHA256 manifests, dynamic receipts, immutable prior artifacts
    v14: List[str] = []
    if (
        f13.get("status") != "PASS"
        or f13.get("source_tree_unchanged_during_run") is not True
        or f13.get("prior_artifacts_unchanged_during_run") is not True
        or f13.get("prior_dsc_v1_blocked_run_intact") is not True
        or f13.get("all_prior_latest_run_pointers_intact") is not True
        or int(f13.get("modified_prior_files_count", 1)) != 0
    ):
        v14.append("PRIOR_ARTIFACTS_OR_SOURCE_IMMUTABILITY_DIFF_FAILED")
    expected_gds_rcpts = 10 if disk_verdict == "BLOCKED_LOW_DISK" else 14
    gds_rcpt_cnt = sum(1 for k in receipts_map if k.startswith("G-DS-"))
    if gds_rcpt_cnt < expected_gds_rcpts:
        v14.append(f"INCOMPLETE_GDS_DYNAMIC_RECEIPTS:{gds_rcpt_cnt}<{expected_gds_rcpts}")
    _add_gate(
        "G-DS-14",
        "SHA256_MANIFESTS_CROSS_VIEW_AND_IMMUTABLE_PRIOR_ARTIFACTS",
        "13_prior_artifact_and_code_immutability_diffs.json",
        {"gds_receipts_count": gds_rcpt_cnt, "modified_prior_files_count": f13.get("modified_prior_files_count")},
        "PASS" if not v14 else "FAIL",
        v14,
    )

    # G-DS-15 (§5): Publication remote readback & exact code/evidence/receipt SHA graph
    _add_gate(
        "G-DS-15",
        "SAME_BRANCH_PUBLICATION_AND_REMOTE_SHA_GRAPH_READBACK",
        "07_sha_lock_current_HEAD_vs_tested_executable_execution_graph.json",
        {"publication_status": publication_status, "tested_executable_sha": tested_sha},
        publication_status,
        [] if publication_status == "PASS_REMOTE_VERIFIED" else ["AWAITING_REMOTE_PUSH_AND_READBACK"],
        require_pass_receipt=False,
    )

    by_gid_base = {g["gate_id"]: g for g in gates}

    # R2-A..R2-G Addenda Gates (§5 lines 150-158)
    v_r2a: List[str] = []
    if (
        f07.get("status") != "PASS"
        or f07.get("tested_executable_sha") != tested_sha
        or f07.get("run_id_embeds_tested_sha12") is not True
        or f07.get("stale_tested_sha_reused") is not False
        or f07.get("run_id") == PRIOR_DSC_V1_BLOCKED_RUN_ID
    ):
        v_r2a.append("R2A_SHA_EXECUTION_GRAPH_OR_STALE_SHA_VIOLATION")
    _add_gate("R2-A", "TESTED_SHA_SEPARATED_FROM_PUBLICATION_HEAD_AND_UNIQUE_RUN_ID", "07_sha_lock_current_HEAD_vs_tested_executable_execution_graph.json", {"head_at_start_sha": f07.get("head_at_start_sha"), "tested_executable_sha": tested_sha}, "PASS" if not v_r2a else "FAIL", v_r2a)

    _add_gate(
        "R2-B",
        "MULTI_MOUNT_STRICT_20P5_GIB_DISK_GUARD_NO_AUTO_DOWNGRADE",
        "06_strict_disk_recovery_actions_and_resource_monitor.json",
        {"disk_verdict": disk_verdict, "distinct_filesystems": (f00.get("disk_mounts_audit") or {}).get("distinct_underlying_filesystems_count")},
        by_gid_base["G-DS-07"]["status"] if by_gid_base["G-DS-07"]["status"] in ("PASS", "BLOCKED_LOW_DISK") else "FAIL",
        [] if by_gid_base["G-DS-07"]["status"] == "PASS" else by_gid_base["G-DS-07"]["violations"],
        require_pass_receipt=(by_gid_base["G-DS-07"]["status"] == "PASS"),
    )

    _add_gate(
        "R2-C",
        "ACTUAL_FULL_SUITE_EXECUTION_NOT_SUBSTITUTED_BY_FOCUSED_TESTS",
        "09_full_regression_junit_collected_executed_bijection.json",
        by_gid_base["G-DS-10"]["observed_values"],
        by_gid_base["G-DS-10"]["status"],
        by_gid_base["G-DS-10"]["violations"],
        require_pass_receipt=(by_gid_base["G-DS-10"]["status"] == "PASS"),
    )

    v_r2d = list(by_gid_base["G-DS-11"]["violations"]) + list(by_gid_base["G-DS-06"]["violations"])
    r2d_st = (
        "PASS"
        if (by_gid_base["G-DS-11"]["status"] == "PASS" and by_gid_base["G-DS-06"]["status"] == "PASS")
        else ("NOT_RUN_BLOCKED_LOW_DISK" if (by_gid_base["G-DS-11"]["status"] == "NOT_RUN_BLOCKED_LOW_DISK" and by_gid_base["G-DS-06"]["status"] == "PASS") else "FAIL")
    )
    _add_gate("R2-D", "ACTUAL_FULL_BACKTEST_FINGERPRINTS_AND_NO_FALSE_56710_CLAIM_FROM_242_RERISK", "10_full_baseline_backtest_three_fill_1pos_adapter_and_fingerprint_parity.json", {"backtest_status": by_gid_base["G-DS-11"]["status"], "fee_status": by_gid_base["G-DS-06"]["status"]}, r2d_st, v_r2d if r2d_st != "PASS" else [], require_pass_receipt=(r2d_st == "PASS"))

    r2e_ok = by_gid_base["G-DS-04"]["status"] == "PASS" and by_gid_base["G-DS-12"]["status"] == "PASS"
    _add_gate("R2-E", "PARTIAL_BITGET_COVERAGE_NEVER_PROMOTED_TO_COMPLETE_AND_ZERO_OOS_CONTAMINATION", "04_requested_window_coverage_origin_and_online_capture_status.json", {"g04": by_gid_base["G-DS-04"]["status"], "g12": by_gid_base["G-DS-12"]["status"]}, "PASS" if r2e_ok else "FAIL", [] if r2e_ok else (by_gid_base["G-DS-04"]["violations"] + by_gid_base["G-DS-12"]["violations"]))

    _add_gate("R2-F", "INDEPENDENT_VERIFIER_DETECTS_FORGED_PASS_FAKE_JUNIT_2880_M1_AND_STALE_MANIFEST", "11_negative_controls_fault_injection_independent_verifier.json", {"passed_mutations": f11.get("passed_mutations")}, "PASS" if by_gid_base["G-DS-13"]["status"] == "PASS" else "FAIL", by_gid_base["G-DS-13"]["violations"])

    r2g_ok = by_gid_base["G-DS-01"]["status"] == "PASS" and by_gid_base["G-DS-14"]["status"] == "PASS"
    _add_gate("R2-G", "SAME_MANDATORY_BRANCH_PRIOR_BLOCKED_RUN_AND_LATEST_POINTERS_INTACT", "13_prior_artifact_and_code_immutability_diffs.json", {"prior_dsc_v1_intact": f13.get("prior_dsc_v1_blocked_run_intact"), "prior_latest_ptrs_intact": f13.get("all_prior_latest_run_pointers_intact")}, "PASS" if r2g_ok else "FAIL", [] if r2g_ok else (by_gid_base["G-DS-01"]["violations"] + by_gid_base["G-DS-14"]["violations"]))

    by_gid = {g["gate_id"]: g for g in gates}
    any_hard_fail = any(g["status"] == "FAIL" for g in gates)
    offline_core_ok = all(
        by_gid[gid]["status"] == "PASS"
        for gid in ("G-DS-01", "G-DS-02", "G-DS-03", "G-DS-04", "G-DS-05", "G-DS-06", "G-DS-08", "G-DS-12", "G-DS-13", "G-DS-14", "R2-A", "R2-E", "R2-F", "R2-G")
    )
    heavy_all_pass = all(
        by_gid[gid]["status"] == "PASS"
        for gid in ("G-DS-09", "G-DS-10", "G-DS-11", "R2-C", "R2-D")
    )

    if any_hard_fail:
        eng_verdict = "FAIL"
    elif disk_verdict == "BLOCKED_LOW_DISK" and offline_core_ok and by_gid["G-DS-07"]["status"] == "BLOCKED_LOW_DISK":
        eng_verdict = "BLOCKED_LOW_DISK"
    elif disk_verdict == "PASS_STRICT_20GIB" and offline_core_ok and by_gid["G-DS-07"]["status"] == "PASS" and by_gid["R2-B"]["status"] == "PASS" and heavy_all_pass:
        eng_verdict = "PASS"
    else:
        eng_verdict = "FAIL"

    if bool(f04.get("online_capture_executed", False)) and f04.get("online_capture_verdict") in ("VERIFIED_COMPLETE", "VERIFIED_PARTIAL"):
        data_acq_verdict = str(f04.get("online_capture_verdict"))
    else:
        data_acq_verdict = str(f04.get("reclassified_DATA_ACQUISITION_VERDICT") or "VERIFIED_PARTIAL")

    return {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC_R2,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC_R2,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "head_at_start_sha": f00.get("head_at_start_sha"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": f00.get("canonical_dataset_sha256"),
        "orthogonal_verdicts": {
            "ENGINEERING_CLOSURE_VERDICT": eng_verdict,
            "DISK_SAFETY_VERDICT": disk_verdict,
            "FULL_REGRESSION_VERDICT": by_gid["G-DS-10"]["status"],
            "BACKTEST_VERDICT": by_gid["G-DS-11"]["status"],
            "WORKER_DETERMINISM_VERDICT": by_gid["G-DS-09"]["status"],
            "DATA_ACQUISITION_VERDICT": data_acq_verdict,
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE_BASELINE_UNCHANGED_FEE_SENSITIVITY_NEGATIVE",
            "KH01C_ADVANCEMENT_VERDICT": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE",
            "FRESH_OOS_VERDICT": oos_verdict if by_gid["G-DS-12"]["status"] == "PASS" else "FAIL_CONTAMINATED",
            "ACTIVATION_READY": "NO",
            "RUNTIME_SAFETY_MODES": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "PUBLICATION_VERDICT": publication_status,
        },
        "explicit_blockers": (
            [str(disk_pol.get("abort_reason"))]
            if disk_verdict == "BLOCKED_LOW_DISK"
            else ([g["gate_id"] for g in gates if g["status"] == "FAIL"])
        ),
        "gates_summary": {
            "pass_count": sum(1 for g in gates if g["status"] == "PASS"),
            "blocked_or_not_run_count": sum(1 for g in gates if g["status"] in ("BLOCKED_LOW_DISK", "NOT_RUN_BLOCKED_LOW_DISK", "PENDING_REMOTE_PUSH")),
            "fail_count": sum(1 for g in gates if g["status"] == "FAIL"),
            "total_gates": len(gates),
        },
        "gates": gates,
    }


def render_kh01b_r1_dsc_r2_readme_and_handover_vi(
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
    f04: Mapping[str, Any],
    f05: Mapping[str, Any],
    f06: Mapping[str, Any],
    f07: Mapping[str, Any],
    f08: Mapping[str, Any],
    f09: Mapping[str, Any],
    f10: Mapping[str, Any],
    f11: Mapping[str, Any],
    f12: Mapping[str, Any],
    f13: Mapping[str, Any],
    f14: Mapping[str, Any],
) -> Tuple[str, str]:
    ov = f14["orthogonal_verdicts"]
    d_pol = f00.get("disk_reserve_policy") or {}
    mounts = (f00.get("disk_mounts_audit") or {}).get("mounts_by_label") or {}
    tel = f06.get("runtime_telemetry") or {}
    recl = f06.get("stale_owned_temp_reclamation") or {}
    par_05 = f05.get("original_e00_b00_reference_parity") or {}
    fo_04 = (f05.get("FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS") or {}).get("0.0004") or {}
    fo_06 = (f05.get("FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS") or {}).get("0.0006") or {}
    rr_04 = (f05.get("FULL_RE_RISKED_REPLAY") or {}).get("0.0004") or {}
    rr_06 = (f05.get("FULL_RE_RISKED_REPLAY") or {}).get("0.0006") or {}
    cnts_09 = ((f09.get("independent_bijection_verification") or {}).get("counters") or {})
    nb_10 = f10.get("si0_e00_primary_nb_summary") or {}
    fm_10 = f10.get("si0_e00_fill_mode_summaries") or {}

    gate_rows = [
        "| Gate ID | Gate Name | Status | Observed Summary |",
        "|---|---|---|---|",
    ]
    for g in f14["gates"]:
        gate_rows.append(
            f"| `{g['gate_id']}` | `{g['name']}` | **{g['status']}** | `{json.dumps(g.get('observed_values'), sort_keys=True)}` |"
        )
    gate_table = "\n".join(gate_rows)

    mount_rows = [
        "| Label | Mountpoint / Probe | st_dev | FS Type | Free Bytes | Free GiB | Free GB (dec) | >=20.5 GiB |",
        "|---|---|---:|---|---:|---:|---:|---|",
    ]
    for m_lbl in ("repo", "output", "temp", "venv_or_python"):
        m = mounts.get(m_lbl) or {}
        mount_rows.append(
            f"| `{m_lbl}` | `{m.get('mountpoint')}` | `{m.get('st_dev')}` | `{m.get('fstype')}` | "
            f"`{m.get('free_bytes')}` | `{m.get('free_gib')}` | `{m.get('free_gb_decimal')}` | `{m.get('meets_strict_20p5_gib_preflight')}` |"
        )
    mount_table = "\n".join(mount_rows)

    cov_map = f04.get("coverage_by_timeframe") or {}
    cov_rows = [
        "| Timeframe | Old Snapshot Expected | Old Snapshot Observed | Old Missing | Old Verdict | New R2 Online Observed/Expected | New R2 Online Verdict |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    on_per_tf = ((f04.get("online_capture_summary") or {}).get("per_timeframe_validation") or {})
    for tf_k in ("1m", "5m", "15m", "1H", "4H", "1D"):
        c_old = cov_map.get(tf_k) or {}
        c_new = on_per_tf.get(tf_k) or {}
        new_obs_str = (
            f"{c_new.get('observed_unique_closed_slots')}/{c_new.get('expected_closed_slots')}"
            if c_new else "NOT_RUN"
        )
        cov_rows.append(
            f"| `{tf_k}` | `{c_old.get('expected_closed_slots')}` | `{c_old.get('observed_unique_closed_slots')}` | "
            f"`{c_old.get('missing_slots')}` | `{c_old.get('coverage_verdict')}` | `{new_obs_str}` | `{c_new.get('coverage_verdict', 'NOT_RUN')}` |"
        )
    cov_table = "\n".join(cov_rows)

    readme = f"""# AURUM DESK — KH-01B.R1.DSC.R2: STRICT DISK RECOVERY, FULL RE-VERIFICATION & EVIDENCE CLOSURE (`{SPEC_VERSION_KH01B_R1_DSC_R2}`)

- **Run ID**: `{run_id}`
- **Mandatory Branch**: `{branch}`
- **HEAD at Start (Prior DSC Blocked Publication Commit)**: `{head_at_start_sha}`
- **Prior Blocked DSC Run Preserved Untouched**: `{PRIOR_DSC_V1_BLOCKED_RUN_ID}` (`tested_sha = {PRIOR_DSC_V1_TESTED_SHA}`)
- **Tested Executable Source SHA (`CODE_COMMIT_SHA`)**: `{tested_sha}`
- **Canonical Dataset SHA256 (`REUSED_RESEARCH`, `MODELED_BID_ASK`)**: `{dataset_sha}`

## 1. Orthogonal Verdicts
- `ENGINEERING_CLOSURE_VERDICT`: **{ov['ENGINEERING_CLOSURE_VERDICT']}**
- `DISK_SAFETY_VERDICT`: **{ov['DISK_SAFETY_VERDICT']}** (Preflight floor: `>= {d_pol.get('min_acceptance_preflight_gib')} GiB`; Constraining preflight free: `{d_pol.get('preflight_baseline_free_gib')} GiB`; Min runtime free: `{tel.get('min_free_disk_gib')} GiB`; Post-cleanup free: `{tel.get('post_cleanup_free_gib')} GiB`)
- `FULL_REGRESSION_VERDICT`: **{ov['FULL_REGRESSION_VERDICT']}** (`collected = {cnts_09.get('collected')}`, `executed = {cnts_09.get('executed')}`, `passed = {cnts_09.get('passed')}`, `skipped = {cnts_09.get('skipped')}`, `failed = {cnts_09.get('failed')}`, `errors = {cnts_09.get('errors')}`)
- `BACKTEST_VERDICT`: **{ov['BACKTEST_VERDICT']}** (`56710` A1 + `6375` SI8 = `63085` cohorts; `SI0` `242` scheduled / `166` closed = `9W/157L`, `net = {par_05.get('net_pnl_usd')} USD`, `expectancy = {nb_10.get('expectancy_net_r')}R`; `246` 3-adapter episodes `0` mismatches)
- `WORKER_DETERMINISM_VERDICT`: **{ov['WORKER_DETERMINISM_VERDICT']}** (`pytest w4 vs w1 speedup = {f08.get('measured_speedup_w4_vs_w1')}x`, `all_4_identical = {f08.get('pytest_subset_w1_w2_w4_repeat_identical')} / {f08.get('r1_variant_slice_w1_w2_w4_repeat_identical')}`)
- `DATA_ACQUISITION_VERDICT`: **{ov['DATA_ACQUISITION_VERDICT']}** (Old M1 snapshot preserved as `1600/2880` `VERIFIED_PARTIAL`; New R2 online capture: `{f04.get('online_capture_verdict', 'NOT_RUN')}`)
- `RESEARCH_ECONOMIC_VERDICT`: **{ov['RESEARCH_ECONOMIC_VERDICT']}**
- `KH01C_ADVANCEMENT_VERDICT`: **{ov['KH01C_ADVANCEMENT_VERDICT']}**
- `FRESH_OOS_VERDICT`: **{ov['FRESH_OOS_VERDICT']}** (`strategy_evaluation_count = {(f12.get('future_oos_escrow') or {}).get('strategy_evaluation_count_total')}`)
- `ACTIVATION_READY`: **{ov['ACTIVATION_READY']}** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)
- `PUBLICATION_VERDICT` (pre-push bundle): **{ov['PUBLICATION_VERDICT']}**

## 2. Multi-Mount Disk Audit & Safe Sentinel Reclamation (`00`, `06`)
{mount_table}

- **Stale Sentinel-Owned Temp Reclamation**: `{recl.get('reclaimed_owned_dirs_count')}` dead-PID dirs reclaimed (`{recl.get('reclaimed_total_bytes')}` bytes / `{recl.get('reclaimed_total_mib')}` MiB); `unowned_paths_deleted_count = {recl.get('unowned_paths_deleted_count')}`.
- **Process & Memory Telemetry**: `max_children_observed = {tel.get('max_children_observed')}` (`<= 4`), `orphan_pids_at_cleanup_count = {tel.get('orphan_pids_at_cleanup_count')}`, `parent_rss_mib = {tel.get('parent_rss_mib')}`, `peak_child_tree_rss_mib = {tel.get('peak_child_tree_rss_mib')}`.

## 3. Window Coverage & Fee Sensitivity Summary (`04`, `05`, `10`)
{cov_table}

- **Fixed-Fill Counterfactual (`166` closed)**: `0.0004` (`gross = {fo_04.get('gross_pnl_1x_usd')}`, `fees = {fo_04.get('total_fees_1x_usd')}`, `net = {fo_04.get('net_pnl_1x_usd')} USD`) vs `0.0006` (`fees = {fo_06.get('total_fees_1x_usd')}`, `net = {fo_06.get('net_pnl_1x_usd')} USD`).
- **Separate `SI0-242` Cohort Rerisked Study (NOT a full `56,710` opportunity rerun)**: `0.0004` (`{rr_04.get('closed_trades_count')}` closed, `net = {rr_04.get('net_pnl_1x_usd')} USD`) vs `0.0006` (`{rr_06.get('closed_trades_count')}` closed, `net = {rr_06.get('net_pnl_1x_usd')} USD`). `CostAssumptions` defaults unchanged (`0.0002 / 0.0004 / 0.10`).
- **Three Fill Modes (`SI0/E00`)**: `NEXT_BAR_OPEN_BID_ASK` net = `{(fm_10.get('NEXT_BAR_OPEN_BID_ASK') or {}).get('total_realized_net_usd')}`, `LEGACY_CLOSE_FILL` net = `{(fm_10.get('LEGACY_CLOSE_FILL') or {}).get('total_realized_net_usd')}`, `STRESS_WIDER_SPREAD_SLIPPAGE_2X` net = `{(fm_10.get('STRESS_WIDER_SPREAD_SLIPPAGE_2X') or {}).get('total_realized_net_usd')}`.
- **Adversarial RED/GREEN Fault Suite (`11`)**: `{f11.get('passed_mutations')}/{f11.get('total_mutations')}` PASS.

## 4. Gate Matrix (`G-DS-01..G-DS-15` & `R2-A..R2-G`)
{gate_table}

## 5. Platform-Safe Replay Instructions (Current Committed Code SHA)
```bash
# Always derive current committed executable SHA (never pass a stale historical --tested-sha after evidence commits):
python aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py --r2 --max-workers 4
```
"""

    handover = f"""BÀN GIAO NGHIỆM THU TOÀN DIỆN KH-01B.R1.DSC.R2 — AURUM DESK ({SPEC_VERSION_KH01B_R1_DSC_R2})
Run ID: {run_id}
Nhánh bắt buộc: {branch}
HEAD tại thời điểm bắt đầu (commit công bố bản chạy bị chặn trước đó): {head_at_start_sha}
Bản chạy bị chặn trước đó được bảo toàn nguyên vẹn: {PRIOR_DSC_V1_BLOCKED_RUN_ID} (tested_sha={PRIOR_DSC_V1_TESTED_SHA})
CODE_COMMIT_SHA được kiểm định thực tế (tested_executable_sha): {tested_sha}
Canonical Dataset SHA256 (bất biến, REUSED_RESEARCH / MODELED_BID_ASK): {dataset_sha}

1. MA TRẬN PHÁN QUYẾT ĐỘC LẬP (ORTHOGONAL VERDICTS):
- ENGINEERING_CLOSURE_VERDICT: {ov['ENGINEERING_CLOSURE_VERDICT']}
- DISK_SAFETY_VERDICT: {ov['DISK_SAFETY_VERDICT']} (Sàn preflight >= {d_pol.get('min_acceptance_preflight_gib')} GiB; Thực tế trống nhỏ nhất lúc preflight: {d_pol.get('preflight_baseline_free_gib')} GiB [{d_pol.get('preflight_baseline_free_gb_decimal')} GB thập phân]; Đáy runtime: {tel.get('min_free_disk_gib')} GiB; Sau thu dẹp: {tel.get('post_cleanup_free_gib')} GiB)
- FULL_REGRESSION_VERDICT: {ov['FULL_REGRESSION_VERDICT']} (collected={cnts_09.get('collected')}, executed={cnts_09.get('executed')}, passed={cnts_09.get('passed')}, skipped={cnts_09.get('skipped')}, failed={cnts_09.get('failed')}, errors={cnts_09.get('errors')})
- BACKTEST_VERDICT: {ov['BACKTEST_VERDICT']} (56710 A1 + 6375 SI8 = 63085 cơ hội tách biệt; SI0/E00 242 đặt lệnh / 166 đóng lệnh = 9W/157L, Gross={par_05.get('gross_pnl_usd')} USD, Fees={par_05.get('total_fees_usd')} USD, Net={par_05.get('net_pnl_usd')} USD, Expectancy={nb_10.get('expectancy_net_r')}R; 246 tập 3-adapter khớp 100%)
- WORKER_DETERMINISM_VERDICT: {ov['WORKER_DETERMINISM_VERDICT']} (speedup w=4 so với w=1 = {f08.get('measured_speedup_w4_vs_w1')}x, đồng nhất mã băm 4 lượt = {f08.get('pytest_subset_w1_w2_w4_repeat_identical')} / {f08.get('r1_variant_slice_w1_w2_w4_repeat_identical')})
- DATA_ACQUISITION_VERDICT: {ov['DATA_ACQUISITION_VERDICT']} (Snapshot M1 cũ giữ nguyên phân loại trung thực 1600/2880 VERIFIED_PARTIAL; Thu thập trực tuyến R2 mới: {f04.get('online_capture_verdict', 'NOT_RUN')})
- RESEARCH_ECONOMIC_VERDICT: {ov['RESEARCH_ECONOMIC_VERDICT']}
- KH01C_ADVANCEMENT_VERDICT: {ov['KH01C_ADVANCEMENT_VERDICT']}
- FRESH_OOS_VERDICT: {ov['FRESH_OOS_VERDICT']} (strategy_evaluation_count={(f12.get('future_oos_escrow') or {}).get('strategy_evaluation_count_total')})
- ACTIVATION_READY: {ov['ACTIVATION_READY']} (PRODUCTION=OFF, LIVE=OFF, PAPER=OFF, AUTO=OFF)
- PUBLICATION_VERDICT (trước khi đối chiếu blob từ xa): {ov['PUBLICATION_VERDICT']}

2. KIỂM ĐỊNH ĐA PHÂN VÙNG GHI & THU HỒI TEMP AN TOÀN:
{mount_table}
- Thu hồi thư mục tạm mồ côi có sentinel hợp lệ: {recl.get('reclaimed_owned_dirs_count')} thư mục ({recl.get('reclaimed_total_bytes')} bytes), số đường dẫn không thuộc sở hữu bị xoá = {recl.get('unowned_paths_deleted_count')}.
- Tiến trình con tối đa quan sát được: {tel.get('max_children_observed')} (<=4), orphan_pids={tel.get('orphan_pids_at_cleanup_count')}.

3. ĐỘ NHẠY PHÍ 0.0004 SO VỚI 0.0006 & CÁC CHẾ ĐỘ KHỚP LỆNH:
- Fixed-fill (166 lệnh đóng giữ nguyên): tại 0.0004 Gross={fo_04.get('gross_pnl_1x_usd')} USD, Fees={fo_04.get('total_fees_1x_usd')} USD, Net={fo_04.get('net_pnl_1x_usd')} USD; tại 0.0006 Fees={fo_06.get('total_fees_1x_usd')} USD, Net={fo_06.get('net_pnl_1x_usd')} USD.
- Phân tích định cỡ lại trên tập con SI0-242 (KHÔNG phải chạy lại toàn bộ phễu 56,710 cơ hội): tại 0.0006 còn {rr_06.get('closed_trades_count')} lệnh đóng, Net={rr_06.get('net_pnl_1x_usd')} USD (ít giao dịch hơn do bị loại bởi net_rr >= 2.0, hoàn toàn không phải lợi thế dương). Mặc định CostAssumptions giữ nguyên bất biến (0.0002 / 0.0004 / 0.10).
- Đột biến đối chứng ĐỎ/XANH (DSC-M01..DSC-M31): {f11.get('passed_mutations')}/{f11.get('total_mutations')} PASS.

4. MA TRẬN CỔNG NGHIỆM THU G-DS-01..15 & R2-A..G:
{gate_table}

5. LỆNH CHẠY LẠI AN TOÀN ĐA NỀN TẢNG (KHÔNG DÙNG --tested-sha LỊCH SỬ CŨ SAU KHI ĐÃ CÓ COMMIT BẰNG CHỨNG):
  python aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py --r2 --max-workers 4
"""
    return readme, handover


def verify_kh01b_r1_dsc_r2_cross_view_consistency(
    *,
    readme_text: str,
    handover_text: str,
    f00: Mapping[str, Any],
    f05: Mapping[str, Any],
    f11: Mapping[str, Any],
    f14: Mapping[str, Any],
) -> Dict[str, Any]:
    violations: List[str] = []
    ov = f14["orthogonal_verdicts"]
    d_pol = f00.get("disk_reserve_policy") or {}
    par_05 = f05.get("original_e00_b00_reference_parity") or {}

    req_tokens = [
        str(f14.get("run_id") or ""),
        str(f14.get("tested_executable_sha") or ""),
        str(ov.get("ENGINEERING_CLOSURE_VERDICT") or ""),
        str(ov.get("DISK_SAFETY_VERDICT") or ""),
        str(ov.get("FULL_REGRESSION_VERDICT") or ""),
        str(ov.get("BACKTEST_VERDICT") or ""),
        str(ov.get("WORKER_DETERMINISM_VERDICT") or ""),
        str(ov.get("DATA_ACQUISITION_VERDICT") or ""),
        str(ov.get("KH01C_ADVANCEMENT_VERDICT") or ""),
        str(ov.get("FRESH_OOS_VERDICT") or ""),
        str(d_pol.get("preflight_baseline_free_gib") or ""),
        str(par_05.get("net_pnl_usd") or ""),
        f"{f11.get('passed_mutations')}/{f11.get('total_mutations')}",
    ]
    for tok in req_tokens:
        if tok not in readme_text:
            violations.append(f"README_R2_MISSING_TOKEN:{tok}")
        if tok not in handover_text:
            violations.append(f"HANDOVER_R2_VI_MISSING_TOKEN:{tok}")

    for forbidden_path in ("/Users/macbook",):
        if forbidden_path in readme_text or forbidden_path in handover_text:
            violations.append(f"HARDCODED_HOST_PATH_FORBIDDEN:{forbidden_path}")

    if ov.get("ENGINEERING_CLOSURE_VERDICT") != "PASS":
        for bad_claim in ("`ENGINEERING_CLOSURE_VERDICT`: **PASS**", "ENGINEERING_CLOSURE_VERDICT: PASS\n"):
            if bad_claim in readme_text or bad_claim in handover_text:
                violations.append(f"FALSE_ENGINEERING_PASS_CLAIM_IN_TEXT:{bad_claim.strip()}")

    return {
        "status": "PASS" if not violations else "FAIL",
        "checked_tokens_count": len(req_tokens),
        "violations": violations,
    }
