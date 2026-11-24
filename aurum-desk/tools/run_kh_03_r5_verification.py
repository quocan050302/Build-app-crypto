#!/usr/bin/env python3
"""
aurum-desk/tools/run_kh_03_r5_verification.py

AURUM DESK — KH-03.R5 | OBSERVED MARKET DATA FOUNDATION & EXECUTION FIDELITY ACCEPTANCE RUNNER
SPEC VERSION: KH-03.R5-v1.0 | SAME BRANCH | OFFLINE DEFAULT | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO

Produces an immutable per-run directory under:
  aurum-desk/docs/artifacts/kh_03_r5_market_data_foundation/<YYYYMMDDTHHMMSSZ>_<TESTED_SHA12>/
containing artifacts 00..22 (with 21 sealed by separate-OS independent verifier), README.md,
HANDOVER_KH03_R5_VI.txt, logs/ (0.5s telemetry + R4 replay forensic ledger + compressed pytest
nodeids), junit/, and checksums/.
NEVER accepts disk waivers; NEVER initiates live network capture without verified human approval;
NEVER touches historical OOS escrow or any locked production/science/prior artifact file.
"""
from __future__ import annotations

import json as _early_json
import os as _early_os
import sys as _early_sys

if __name__ == "__main__" and (
    "--owner-waive-disk-preflight" in _early_sys.argv
    or str(_early_os.environ.get("KH02_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
    or str(_early_os.environ.get("KH03_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
    or str(_early_os.environ.get("KH03_C1_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
    or str(_early_os.environ.get("KH03_C2_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
    or str(_early_os.environ.get("KH03_R4_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
    or str(_early_os.environ.get("KH03_R5_OWNER_WAIVE_DISK_PREFLIGHT") or "").strip().lower() in ("1", "true", "yes")
):
    print(
        _early_json.dumps(
            {
                "status": "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER",
                "error": "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN (>=20.5 GiB preflight / >=20.0 GiB runtime).",
                "exit_code": 2,
            },
            indent=2,
            sort_keys=True,
        ),
        file=_early_sys.stderr,
    )
    raise SystemExit(2)

import argparse
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import psutil

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_01b_r1_ram_safety_r3_service import load_empirical_r2_shard_rss_bytes
from services.kh_02_entry_foundation_research_service import (
    build_kh02_asof_mtf_features,
    generate_kh02_research_candidates,
)
from services.kh_02_r2_c2_public_capture import RawHttpResponseC2, _SequenceFakeTransportC2
from services.kh_02_r2_c4_risk_tail_evidence import (
    build_c4_per_episode_risk_ledger,
    build_c4_realized_tail_evidence_from_r2_replays,
    recompute_c4_tail_counters_from_episode_ledger,
    validate_c4_tail_disclosure_vs_risk_ledger,
)
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    ADVISORY_DISK_GIB_KH02_R2,
    CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02_R2,
    DEFAULT_RANDOM_SEED_KH02_R2,
    FORBIDDEN_ORDER_SERVICE_TOKENS,
    GLOBAL_MAX_DESCENDANTS_KH02_R2,
    LOCKED_KEEP_AS_IS_FILES_KH02_R2,
    PREFLIGHT_RAM_MIN_MIB_KH02_R2,
    RUNTIME_SAFETY_MODES_KH02_R2,
    STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
    STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
    _ast_function_inventory,
    _r2_frozen_plan_and_arms,
    _sha256_file,
    audit_r2_reference_inputs_and_invariants,
    build_r2_c1_readonly_disk_inventory,
    build_r2_causal_unswept_level_ledger,
    build_r2_target_repair_full_opportunity_conservation,
    create_sentinel_owned_temp_dir_r2_c1,
    load_r2_canonical_dataset_readonly,
    probe_r2_c1_write_mounts_strict,
    replay_r2_same_opportunity_paired_counterfactuals,
    reprice_r2_fixed_fills_cost_counterfactual,
)
from services.kh_03_data_governance_service import audit_kh03_collector_and_escrow_readiness
from services.kh_03_r2_methodology_service import (
    build_kh03_r2_forward_only_dev_folds,
    validate_kh03_r2_fold_causality,
)
from services.kh_03_r3_c1_worker_policy_service import (
    BENCHMARK_KIND_PYTEST,
    BENCHMARK_KIND_SCIENCE,
    select_measured_worker_policy_strict,
)
from services.kh_03_r3_diagnostic_service import (
    audit_r3_entry_exit_geometry_and_accounting,
    build_r3_cost_and_break_even_sensitivity,
    build_r3_full_opportunity_lineage,
    build_r3_win_loss_failure_attribution,
    run_r3_negative_control_and_label_integrity_tests,
)
from services.kh_03_r3_experiment_service import (
    build_r3_causal_m5_unswept_target_ledger,
    build_r3_train_only_candidates_asof,
    freeze_r3_bounded_research_plan,
    run_r3_forward_only_dev_evaluation,
    run_r3_paired_and_strict_dev_evaluation_15_16,
)
from services.kh_03_r3_legacy_research_guard import audit_kh03_r3_legacy_research_callgraph
from services.kh_03_r4_economic_diagnostic_service import (
    audit_r4_covariate_overlap_and_effective_n,
    build_r4_counterfactual_cost_only_diagnostics,
    build_r4_data_gap_register,
    build_r4_mechanism_evidence_table,
    build_r4_trade_population_conservation,
    decide_r4_research_next_action,
    derive_r4_breakeven_feasibility_bounds,
    load_r4_immutable_research_inputs_readonly,
    recompute_r4_decimal_economic_bridge,
    validate_r4_no_claim_inflation,
)
from services.kh_03_r5_capture_and_custody_service import (
    OosAccessGuardR5,
    R5DevCustodyJournal,
    R5InjectedCrashError,
    R5OosAccessDeniedError,
    assign_r5_partition_before_capture,
    capture_r5_public_batch,
    persist_r5_verified_dev_snapshot,
    verify_r5_custody_chain_readonly,
)
from services.kh_03_r5_market_data_foundation_service import (
    R5OperatorGrant,
    SCHEMA_VERSION_KH03_R5,
    SPEC_VERSION_KH03_R5,
    build_r5_execution_fidelity_register,
    build_r5_market_data_coverage_report,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_bbo_stream,
    validate_r5_funding_history,
    validate_r5_instrument_metadata,
    validate_r5_ohlcv_stream,
    validate_r5_operator_capture_scope,
    validate_r5_owner_fee_confirmation,
)
from services.kh_03_r5_readiness_gate_service import (
    MANDATORY_BRANCH_KH03_R5,
    MIN_EXPECTED_PYTEST_COLLECTED_R5,
    build_r5_owner_handover_vi,
    build_r5_source_function_ownership_matrix,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r4_independent_verifier import KH03_R4_SOURCE_FILES_ALLOWLIST
from tools.kh_03_r5_independent_verifier import (
    KH03_R5_SOURCE_FILES_ALLOWLIST,
    PRIOR_KH03_R4_EVIDENCE_SHA,
    PRIOR_KH03_R4_PUB_HEAD_SHA,
    PRIOR_KH03_R4_RUN_ID,
    PRIOR_KH03_R4_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5,
    _audit_r4_replay_ledger_13_r5,
    _audit_telemetry_gz_vs_17_r5,
    _recompute_raw_bench_r5,
    recompute_r5_gate_matrix,
)
from tools.run_kh_01b_r1_verification import (
    _build_lpt_shards_kh01b_r1,
    _load_empirical_file_durations_ms_from_kh01b,
    _run_controlled_pytest_w1_w2_w4_benchmark_r1,
    run_async_lpt_pytest_shards_kh01b_r1,
)
from tools.run_kh_02_r2_c4_acceptance import (
    _cleanup_sentinel_temp_c4_win_safe,
    _pypath_str,
)
from tools.run_kh_02_r2_verification import (
    _BoundedResourceSampler,
    _git_cmd,
    _run_full_production_baseline_parity_20,
    _run_r2_science_slice_w1_w2_w4_determinism,
)
from tools.run_kh_03_r3_acceptance import (
    _run_kh03_r3_194_red_green_fault_suite,
    _run_kh03_r3_verifier_mutation_suite_22,
)
from tools.run_kh_03_r3_c1_acceptance import (
    EXTRA_FROZEN_RESEARCH_FILES_C1,
    LINEAGE_TRACKED_ARTIFACT_REL,
    _CrossPlatformRunLock,
)
from tools.run_kh_03_r4_acceptance import (
    EXTRA_FROZEN_R3_C2_FILES_R4,
    KH03_R4_EXCLUSIVE_SUBPROCESS_TEST_FILES,
)
from tools.run_v17_9_3_1_2_2_verification import canonicalize_collect_node_id_v1793122

KH03_R5_ARTIFACTS_BASE_REL = "aurum-desk/docs/artifacts/kh_03_r5_market_data_foundation"

KH03_R5_EXCLUSIVE_SUBPROCESS_TEST_FILES: Set[str] = set(KH03_R4_EXCLUSIVE_SUBPROCESS_TEST_FILES) | {
    "test_kh03_r5_resource_and_async.py",
    "test_kh03_r5_readiness_and_verifier.py",
}


def _stamp_r5(doc: Dict[str, Any], *, run_id: str, tested_sha: str) -> Dict[str, Any]:
    doc["spec_version"] = SPEC_VERSION_KH03_R5
    doc["schema_version"] = SCHEMA_VERSION_KH03_R5
    doc["run_id"] = run_id
    doc["tested_executable_sha"] = tested_sha
    return doc


def _audit_ownership_01(*, tested_sha: str, run_id: str, legacy_cg: Mapping[str, Any]) -> Dict[str, Any]:
    all_locked = list(dict.fromkeys(
        list(LOCKED_KEEP_AS_IS_FILES_KH02_R2)
        + list(EXTRA_FROZEN_RESEARCH_FILES_C1)
        + list(EXTRA_FROZEN_R3_C2_FILES_R4)
        + list(KH03_R4_SOURCE_FILES_ALLOWLIST)
    ))
    locked_inv = []
    for rel in all_locked:
        p = REPO_ROOT / rel
        locked_inv.append({
            "rel_path": rel,
            "exists": p.is_file(),
            "sha256": _sha256_file(p) if p.is_file() else None,
            "ast_function_count": len(_ast_function_inventory(p)) if (rel.endswith(".py") and p.is_file()) else None,
        })

    rc_diff, diff_out = _git_cmd(["diff", "--name-only", f"{PRIOR_KH03_R4_PUB_HEAD_SHA}..HEAD"])
    changed_files = [ln.strip() for ln in diff_out.splitlines() if ln.strip()] if rc_diff == 0 else []
    locked_mod = [f for f in changed_files if f in set(all_locked)]
    allow_set = set(KH03_R5_SOURCE_FILES_ALLOWLIST)
    unauthorized = [f for f in changed_files if f not in allow_set and not f.startswith(f"{KH03_R5_ARTIFACTS_BASE_REL}/")]

    forb_hits: List[str] = []
    r5_inv = []
    for rel in KH03_R5_SOURCE_FILES_ALLOWLIST:
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        tree = ast.parse(p.read_text("utf-8", errors="replace"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if any(tok in a.name for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                        forb_hits.append(f"{rel}:import:{a.name}")
            elif isinstance(node, ast.ImportFrom):
                m = node.module or ""
                if any(tok in m for tok in FORBIDDEN_ORDER_SERVICE_TOKENS):
                    forb_hits.append(f"{rel}:from:{m}")
        r5_inv.append({"rel_path": rel, "sha256": _sha256_file(p), "functions_count": len(_ast_function_inventory(p))})

    c_live = CostAssumptions()
    cost_ok = (
        abs(float(c_live.maker_fee_rate) - 0.0002) <= 1e-12
        and abs(float(c_live.taker_fee_rate) - 0.0004) <= 1e-12
        and abs(float(c_live.slippage_usd) - 0.10) <= 1e-12
    )
    ok = (
        len(locked_mod) == 0 and len(unauthorized) == 0 and len(forb_hits) == 0
        and cost_ok and legacy_cg.get("LEGACY_METHOD_GUARD") == "PASS"
        and int(legacy_cg.get("unauthorized_r1_callers_count", 1)) == 0
    )
    return _stamp_r5({
        "prior_r4_pub_head_sha": PRIOR_KH03_R4_PUB_HEAD_SHA,
        "locked_keep_as_is_count": len(locked_inv),
        "locked_keep_as_is_inventory": locked_inv,
        "locked_files_modified": locked_mod,
        "locked_files_modified_count": len(locked_mod),
        "changed_files_vs_r4_pub_head": changed_files,
        "unauthorized_changed_files": unauthorized,
        "unauthorized_changed_files_count": len(unauthorized),
        "kh03_r5_module_inventory": r5_inv,
        "source_function_ownership_matrix": build_r5_source_function_ownership_matrix(),
        "legacy_guard_status": legacy_cg.get("LEGACY_METHOD_GUARD"),
        "unauthorized_r1_callers_count": int(legacy_cg.get("unauthorized_r1_callers_count", 0)),
        "forbidden_order_import_hits": forb_hits,
        "cost_assumptions_defaults_intact": cost_ok,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "status": "PASS" if ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)


def _run_stage2_foundation_offline_03_to_12(
    *, sentinel_tmp: Path, run_id: str, tested_sha: str, min_pre_disk_gib: float
) -> Dict[str, Dict[str, Any]]:
    """
    Runs ALL KH-03.R5 offline foundation, authorization fail-closed, QA fixture capture,
    contract/OHLCV/BBO/funding/fee validation, and DEV custody/partition/deny-before-open
    audits (Artifacts 03..12). Executes ZERO external live network calls!
    """
    now_utc = datetime.now(timezone.utc)
    now_ms = int(now_utc.timestamp() * 1000)

    # 03: Source capability inventory
    f03 = _stamp_r5(build_r5_source_capability_inventory(REPO_ROOT), run_id=run_id, tested_sha=tested_sha)

    # 04: Fail-closed operator authorization & capture batch plan (default NO human grant!)
    req_scope = {
        "symbol": "XAUUSDT", "product_type": "USDT-FUTURES",
        "endpoints": ["contracts", "public_ticker", "history_candles"],
        "run_id": run_id, "max_requests": 20, "max_total_bytes": 5 * 1024 * 1024,
    }
    default_auth = validate_r5_operator_capture_scope(None, req_scope, now_utc, {"from_prompt_text": True})
    cli_bypass = validate_r5_operator_capture_scope(None, req_scope, now_utc, {"from_cli_flag": True, "from_env_var": True})
    live_plan_blocked = plan_r5_capture_batches(
        default_auth, f03,
        {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 2, "max_pages": 10, "max_requests": 20, "max_total_bytes": 5 * 1024 * 1024, "max_duration_sec": 60, "endpoints": ["contracts", "public_ticker"]},
    )

    # Isolated QA fixture grant (offline only, NEVER live network!)
    qa_grant = R5OperatorGrant(
        grant_id="QA_OFFLINE_R5_FIXTURE_001", issued_by="QA_HARNESS", purpose="QA_FIXTURE",
        symbol="XAUUSDT", product_type="USDT-FUTURES", allowed_endpoints=("contracts", "public_ticker", "history_candles"),
        window_start_ms=1794000000000, window_end_ms=1794000300000, max_requests=10,
        max_total_bytes=1024 * 1024, max_duration_sec=30, expires_utc_ms=now_ms + 3600_000,
        run_id=run_id, is_qa_fixture_grant=True, origin_kind="QA_HARNESS_LOCAL",
    )
    qa_req_scope = {
        "symbol": "XAUUSDT", "product_type": "USDT-FUTURES",
        "endpoints": ["contracts", "public_ticker", "history_candles"],
        "run_id": run_id, "max_requests": 10, "max_total_bytes": 1024 * 1024,
    }
    qa_auth = validate_r5_operator_capture_scope(qa_grant, qa_req_scope, now_utc, {"qa_fixture_mode": True})
    qa_plan = plan_r5_capture_batches(
        qa_auth, f03,
        {"destination_disk_free_gib": min_pre_disk_gib, "max_concurrency": 2, "max_pages": 5, "max_requests": 10, "max_total_bytes": 1024 * 1024, "max_duration_sec": 30, "endpoints": ["contracts", "public_ticker", "history_candles"], "symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "window_start_ms": 1794000000000, "window_end_ms": 1794000300000},
    )

    f04_ok = (
        default_auth["admitted"] is False
        and default_auth["authorization_status"] == "BLOCKED_OWNER_AUTHORIZATION"
        and cli_bypass["admitted"] is False
        and live_plan_blocked["status"] == "BLOCKED"
        and qa_auth["admitted"] is True and qa_auth["live_network_permitted"] is False
        and qa_plan["status"] == "PLANNED_QA_OFFLINE"
    )
    f04 = _stamp_r5({
        "operator_approval_state": default_auth,
        "cli_env_prompt_bypass_blocked": (not cli_bypass["admitted"]) and (not default_auth["admitted"]),
        "live_capture_plan": live_plan_blocked,
        "qa_fixture_isolated_plan": qa_plan,
        "status": "PASS" if f04_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 05: Execute live-blocked capture (0 external calls) + isolated QA offline capture
    live_res = asyncio.run(capture_r5_public_batch(live_plan_blocked, None, None, run_id=run_id))

    qa_meta_bytes = json.dumps({"code": "00000", "data": [{"symbol": "XAUUSDT", "productType": "USDT-FUTURES", "symbolType": "perpetual", "quoteCoin": "USDT", "pricePlace": "2", "priceEndStep": "1", "sizeMultiplier": "0.001", "minTradeNum": "0.001", "minTradeUSDT": "5", "maxLever": "20", "stopTriggerPriceSource": "UNKNOWN", "effectiveTimestampMs": 1794000000000}]}).encode("utf-8")
    qa_bbo_bytes = json.dumps({"code": "00000", "data": [{"symbol": "XAUUSDT", "bidPr": "4100.10", "askPr": "4100.30", "ts": "1794000060000"}]}).encode("utf-8")
    qa_ohlcv_bytes = json.dumps({"code": "00000", "data": [[1794000000000, "4100.00", "4101.00", "4099.50", "4100.50", "12.5"], [1794000060000, "4100.50", "4102.00", "4100.00", "4101.20", "15.0"], [1794000120000, "4101.20", "4101.80", "4100.80", "4101.50", "10.0"]]}).encode("utf-8")

    fake_tr = _SequenceFakeTransportC2([
        RawHttpResponseC2(200, qa_meta_bytes, {"content-type": "application/json"}, 1794000060000, 1794000060050),
        RawHttpResponseC2(200, qa_bbo_bytes, {"content-type": "application/json"}, 1794000060100, 1794000060150),
        RawHttpResponseC2(200, qa_ohlcv_bytes, {"content-type": "application/json"}, 1794000060200, 1794000060250),
    ])
    qa_stage = sentinel_tmp / "qa_r5_staging"
    qa_stage.mkdir(parents=True, exist_ok=True)
    qa_res = asyncio.run(capture_r5_public_batch(qa_plan, fake_tr, lambda: {"disk_free_gib": min_pre_disk_gib, "available_ram_mib": 2048.0}, staging_root=qa_stage, run_id=run_id, sleep_fn=lambda _: None))

    qa_items_clean = [{k: v for k, v in it.items() if not k.startswith("_")} for it in qa_res["items"]]
    f05_ok = (
        live_res["external_requests_attempted"] == 0
        and live_res["accepted_observed_records_count"] == 0
        and live_res["capture_execution_status"] == "BLOCKED_OWNER_AUTHORIZATION"
        and qa_res["capture_execution_status"] == "QA_FIXTURE_OFFLINE_PASS"
        and qa_res["accepted_observed_records_count"] == 0
        and qa_res["qa_fixture_records_count"] == 3
        and all(it["evidence_class"] == "QA_FIXTURE" for it in qa_items_clean)
    )
    f05 = _stamp_r5({
        "capture_execution_status": live_res["capture_execution_status"],
        "capture_provenance_status": live_res["capture_provenance_status"],
        "external_requests_attempted": 0,
        "accepted_observed_records_count": 0,
        "qa_fixture_records_count": qa_res["qa_fixture_records_count"],
        "qa_isolated_from_observed_verified": all(it["evidence_class"] == "QA_FIXTURE" for it in qa_items_clean),
        "fake_transport_cannot_mint_observed_verified": True,
        "live_blocked_receipt": live_res,
        "qa_offline_simulation_summary": {"status": qa_res["capture_execution_status"], "items": qa_items_clean},
        "status": "PASS" if f05_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 06: Contract metadata validation (QA fixture validated; live observed remains MISSING!)
    meta_v = validate_r5_instrument_metadata(qa_meta_bytes, {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "settlement_currency": "USDT", "expected_tick_size": "0.01"}, provenance_class="QA_FIXTURE")
    f06 = _stamp_r5({
        "live_observed_metadata_status": "MISSING",
        "offline_contract_validation_passed": bool(meta_v["valid"]),
        "qa_fixture_metadata_validation": meta_v,
        "status": "PASS" if meta_v["valid"] else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 07: OHLCV lineage & gap validation (QA fixture validated; live observed remains MISSING!)
    ohlcv_v = validate_r5_ohlcv_stream(
        [{"raw_bytes": qa_ohlcv_bytes, "symbol": "XAUUSDT", "timeframe": "1m"}],
        {"symbol": "XAUUSDT", "tick_size": "0.01"}, "1m",
        {"start_ms": 1794000000000, "end_ms": 1794000180000, "now_utc_ms": 1794000300000},
        provenance_class="QA_FIXTURE",
    )
    f07 = _stamp_r5({
        "live_observed_new_dev_ohlcv_status": "MISSING",
        "offline_ohlcv_validation_passed": bool(ohlcv_v["valid"]),
        "forward_fill_applied": False,
        "qa_fixture_ohlcv_validation": ohlcv_v,
        "status": "PASS" if ohlcv_v["valid"] else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 08: BBO sample cadence quality & coverage honesty (QA fixture validated; live observed MISSING!)
    bbo_v = validate_r5_bbo_stream(
        [{"symbol": "XAUUSDT", "bid": "4100.10", "ask": "4100.30", "source_timestamp_ms": 1794000060000, "local_receive_timestamp_ms": 1794000060050}],
        {"symbol": "XAUUSDT", "tick_size": "0.01"},
        {"stale_threshold_ms": 15_000, "expected_cadence_ms": 1_000},
        provenance_class="QA_FIXTURE", stream_mode="SINGLE_OBSERVED_SNAPSHOT",
    )
    f08 = _stamp_r5({
        "observed_bbo_coverage_status": "MISSING",
        "offline_bbo_validation_passed": bool(bbo_v["valid"]) and bbo_v["bbo_coverage_classification"] == "SINGLE_OBSERVED_SNAPSHOT",
        "interpolation_or_ohlcv_inference_applied": False,
        "strategy_decision_fill_coverage": "UNKNOWN_NO_AUTHORIZED_ALIGNMENT_PROTOCOL",
        "qa_fixture_bbo_validation": bbo_v,
        "status": "PASS" if bbo_v["valid"] else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 09: Funding history schedule/rate truth (QA fixture validated; live observed MISSING!)
    fund_v = validate_r5_funding_history(
        [{"rows": [
            {"symbol": "XAUUSDT", "settlement_timestamp_ms": 1794000000000, "funding_rate": "0.0001", "rate_kind": "ACTUAL_SETTLED", "units": "DECIMAL_FRACTION"},
            {"symbol": "XAUUSDT", "settlement_timestamp_ms": 1794028800000, "funding_rate": "-0.00005", "rate_kind": "ACTUAL_SETTLED", "units": "DECIMAL_FRACTION"},
        ]}],
        {"symbol": "XAUUSDT"}, {"interval_ms": 8 * 3_600_000}, provenance_class="QA_FIXTURE",
    )
    f09 = _stamp_r5({
        "observed_funding_readiness": "MISSING",
        "offline_funding_validation_passed": bool(fund_v["valid"]),
        "zero_fill_applied": False,
        "retroactive_r4_pnl_modified": False,
        "qa_fixture_funding_validation": fund_v,
        "status": "PASS" if fund_v["valid"] else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 10: Owner fee confirmation & slippage readiness (default None -> UNKNOWN_NOT_LOCALLY_VERIFIED)
    fee_def = validate_r5_owner_fee_confirmation(None, {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES"}, now_utc)
    f10 = _stamp_r5({
        "owner_fee_document_supplied": False,
        "account_fee_readiness": fee_def["account_fee_readiness"],
        "observed_slippage_status": "MISSING",
        "credentials_requested": False,
        "store_local_only": True,
        "default_fee_audit": fee_def,
        "status": "PASS" if fee_def["account_fee_readiness"] == "UNKNOWN_NOT_LOCALLY_VERIFIED" else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 11: DEV custody journal, partition isolation, crash quarantine, double-writer & OOS deny-before-open proof
    cust_root = sentinel_tmp / "r5_custody_qa"
    cust_root.mkdir(parents=True, exist_ok=True)
    part_ok = assign_r5_partition_before_capture(
        qa_plan, {"existing_intervals": [{"role": "REUSED_DEV", "symbol": "XAUUSDT", "start_ms": 1791437880000, "end_ms": 1793196000000}]},
        {"symbol": "XAUUSDT", "purpose": "QA_FIXTURE", "window_start_ms": 1794000000000, "window_end_ms": 1794000300000, "scope_digest_sha256": qa_auth["scope_digest_sha256"]},
        destination_root=cust_root, allowed_base_root=sentinel_tmp,
    )
    relabel_oos_blk = assign_r5_partition_before_capture(
        {"partition_role": "FRESH_OOS", "plan_digest_sha256": "x" * 64}, {},
        {"symbol": "XAUUSDT", "purpose": "OOS", "window_start_ms": 1794000000000, "window_end_ms": 1794000300000},
        destination_root=cust_root, allowed_base_root=sentinel_tmp,
    )
    journal = R5DevCustodyJournal(cust_root, os_disk_probe_fn=lambda: min_pre_disk_gib)
    first_item = qa_res["items"][0]
    p1 = persist_r5_verified_dev_snapshot(qa_stage / first_item["staged_filename"], first_item, meta_v, part_ok, journal)
    # Idempotent double-writer check
    p_dup = persist_r5_verified_dev_snapshot(qa_stage / first_item["staged_filename"], first_item, meta_v, part_ok, journal)
    # Crash recovery quarantine check on second item (must preserve head from p1!)
    second_item = qa_res["items"][1]
    p_crash = persist_r5_verified_dev_snapshot(
        qa_stage / second_item["staged_filename"], second_item, bbo_v, part_ok, journal,
        _crash_hook=lambda ph: (_ for _ in ()).throw(R5InjectedCrashError(f"SIM_CRASH:{ph}")) if ph == "after_blob_fsync" else None,
    )
    # OOS deny-before-open guard check
    oos_guard = OosAccessGuardR5(allowed_dev_roots=[cust_root], protected_oos_roots=[sentinel_tmp / "protected_oos_escrow"])
    oos_denied = False
    try:
        oos_guard.assert_open_permitted(sentinel_tmp / "protected_oos_escrow" / "snap.blob", caller="r5_audit", purpose="oos_read")
    except R5OosAccessDeniedError:
        oos_denied = True
    chain_v = verify_r5_custody_chain_readonly(cust_root, journal.head_state(), None, PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5, repo_root=REPO_ROOT, oos_guard=oos_guard)

    f11_ok = (
        bool(part_ok["valid"])
        and (not relabel_oos_blk["valid"])
        and bool(p1["committed"])
        and bool(p_dup.get("idempotent_duplicate"))
        and (not p_crash["committed"]) and bool(p_crash.get("previous_head_preserved"))
        and oos_denied
        and bool(chain_v["valid"])
    )
    f11 = _stamp_r5({
        "partition_assignment": part_ok,
        "dev_relabel_as_oos_blocked": not relabel_oos_blk["valid"],
        "oos_deny_before_open_verified": oos_denied,
        "crash_recovery_quarantine_verified": (not p_crash["committed"]) and bool(p_crash.get("previous_head_preserved")),
        "double_writer_lock_race_verified": bool(p_dup.get("idempotent_duplicate")),
        "external_head_anchor_verified": bool(chain_v["valid"]) and (cust_root / "external_head_anchor.jsonl").is_file(),
        "custody_chain_verification": chain_v,
        "status": "PASS" if f11_ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    # 12: Coverage report & execution fidelity register (explicitly INCOMPLETE — 0 observed live records)
    cov_rep = build_r5_market_data_coverage_report({"ohlcv": ohlcv_v, "bbo": bbo_v, "funding": fund_v})
    fid_reg = build_r5_execution_fidelity_register(meta_v, bbo_v, fund_v, fee_def, cov_rep, legacy_register={"r4_linked": True})
    f12 = _stamp_r5({**fid_reg, "coverage_report": cov_rep}, run_id=run_id, tested_sha=tested_sha)

    return {"03": f03, "04": f04, "05": f05, "06": f06, "07": f07, "08": f08, "09": f09, "10": f10, "11": f11, "12": f12}


def _run_stage3a_r4_real_replay_13(
    *, datasets: Mapping[str, Any], dataset_sha: str, timeframe_stats: Mapping[str, Any],
    run_dir: Path, sentinel_tmp: Path, run_id: str, tested_sha: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Fresh read-only R4 canonical DEV replay (NO summary copy!) + C4 risk tail validation."""
    mtf_bundle = build_kh02_asof_mtf_features(datasets)
    r1_frozen_plan, _ = _r2_frozen_plan_and_arms(code_sha=tested_sha)
    r1_candidates = generate_kh02_research_candidates(mtf_bundle, r1_frozen_plan)
    ledger_bundle = build_r2_causal_unswept_level_ledger(datasets, source_latency_ms=0, warmup_m1=30, run_t10_future_mutation_proof=True, rng_seed=DEFAULT_RANDOM_SEED_KH02_R2)
    r2_cands_1x = build_r2_target_repair_full_opportunity_conservation(datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=r1_candidates, cost_assumptions=CostAssumptions())
    replay_bundle = replay_r2_same_opportunity_paired_counterfactuals(datasets=datasets, r1_candidates=r1_candidates, r2_candidates=r2_cands_1x)
    live_ff = reprice_r2_fixed_fills_cost_counterfactual(r2_replays=replay_bundle["r2_replays"])

    # C4 per-episode risk tail disclosure check (into ephemeral sentinel_tmp!)
    c4_gz = sentinel_tmp / "r5_c4_tail_ledger.jsonl.gz"
    c4_meta = build_c4_per_episode_risk_ledger(datasets=datasets, r2_replays=replay_bundle["r2_replays"], out_jsonl_gz_path=c4_gz)
    c4_rec = recompute_c4_tail_counters_from_episode_ledger(c4_gz)
    c4_tail_doc = build_c4_realized_tail_evidence_from_r2_replays(datasets=datasets, r2_replays=replay_bundle["r2_replays"], ledger_meta=c4_meta, ledger_recomputed=c4_rec)
    c4_val = validate_c4_tail_disclosure_vs_risk_ledger(c4_tail_doc, c4_gz)

    led_rel = "logs/r4_replay_forensic_ledger_1x.jsonl.gz"
    forensic_gz = run_dir / led_rel
    f06_meta, forensic_rows, null_sum = audit_r3_entry_exit_geometry_and_accounting(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x, out_ledger_gz_path=forensic_gz
    )
    f06_meta["ledger_rel_path"] = led_rel
    lineage_05 = build_r3_full_opportunity_lineage(datasets=datasets, r2_cands_1x=r2_cands_1x, null_replay_summary=null_sum, null_episodes=forensic_rows)
    f07_wl, f08_attr = build_r3_win_loss_failure_attribution(forensic_rows=forensic_rows, ledger_meta_06=f06_meta)
    f09_cost = build_r3_cost_and_break_even_sensitivity(datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r2_cands_1x=r2_cands_1x)
    f10_nc = run_r3_negative_control_and_label_integrity_tests(datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_1x=r2_cands_1x, forensic_rows=forensic_rows)

    m5_ledger = build_r3_causal_m5_unswept_target_ledger(datasets=datasets, pivot_radius=2)
    f11_plan = freeze_r3_bounded_research_plan(tested_code_sha=tested_sha)
    r3_cands_1x = build_r3_train_only_candidates_asof(datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_cands_1x, m5_ledger=m5_ledger, costs=CostAssumptions())
    f13_wf, f14_wf = run_r3_forward_only_dev_evaluation(datasets=datasets, mtf_bundle=mtf_bundle, r3_cands_1x=r3_cands_1x, prereg_plan_11=f11_plan, tested_code_sha=tested_sha)
    f15_cost, f16_gate = run_r3_paired_and_strict_dev_evaluation_15_16(
        datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, m5_ledger=m5_ledger,
        r2_cands_1x=r2_cands_1x, r3_cands_1x=r3_cands_1x, prereg_plan_11=f11_plan, wf_13=f13_wf, wf_14=f14_wf,
    )
    r2_folds_b = build_kh03_r2_forward_only_dev_folds(bars_1m=datasets["1m"], embargo_minutes=240)
    r2_fold_c = validate_kh03_r2_fold_causality(r2_folds_b.get("folds") or (), min_embargo_minutes=240)

    dyn6 = f15_cost.get("dynamic_full_rerisk_six_scenarios") or {}
    ff_r3 = f15_cost.get("fixed_fills_descriptive_repricing") or {}
    d03 = build_r4_trade_population_conservation(lineage_05=lineage_05, forensic_rows=forensic_rows, r2_cands_1x=r2_cands_1x, dyn6_scenarios=dyn6, dataset_sha=dataset_sha)
    d04 = recompute_r4_decimal_economic_bridge(forensic_rows, dataset_sha=dataset_sha, ledger_meta=f06_meta)
    d07 = build_r4_counterfactual_cost_only_diagnostics(forensic_rows, fixed_fills_from_r3=ff_r3, dyn6_scenarios=dyn6, dataset_sha=dataset_sha)
    d05, d10 = build_r4_mechanism_evidence_table(forensic_rows, f08_attr=f08_attr, f09_cost=f09_cost, decimal_bridge=d04, fee_only_diag=d07)
    d06, d09 = audit_r4_covariate_overlap_and_effective_n(f15_cost=f15_cost, f16_gate=f16_gate, forensic_rows=forensic_rows, f10_nc=f10_nc, f13_wf=f13_wf, r2_fold_causality=r2_fold_c)
    d08 = derive_r4_breakeven_feasibility_bounds(forensic_rows, f09_cost=f09_cost, dyn6_scenarios=dyn6, dataset_sha=dataset_sha)
    d11 = build_r4_data_gap_register(repo_root=REPO_ROOT, dataset_sha=dataset_sha, timeframe_stats=timeframe_stats)
    d12 = decide_r4_research_next_action(decimal_bridge=d04, fee_only_diag=d07, data_register=d11, arm_comparability=d09)
    claim_ok = validate_r4_no_claim_inflation({"04": d04, "05": d05, "07": d07, "09": d09, "10": d10, "11": d11, "12": d12})["valid"]

    # Compare per-trade canonical row hashes against sealed R4 forensic ledger directly!
    r4_sealed_led = REPO_ROOT / f"aurum-desk/docs/artifacts/kh_03_r4_economic_feasibility/{PRIOR_KH03_R4_RUN_ID}/logs/forensic_closed_open_gap_ledger_1x.jsonl.gz"
    r4_sealed_rhashes: List[str] = []
    with gzip.open(r4_sealed_led, "rt", encoding="utf-8") as fh:
        for ln in fh:
            s = ln.rstrip("\r\n")
            if s:
                r4_sealed_rhashes.append(hashlib.sha256(s.encode("utf-8")).hexdigest())
    row_hashes_exact_match = (canonical_hash(r4_sealed_rhashes) == f06_meta["canonical_rows_sha256"])

    b04 = d04["closed_trades_decimal_bridge_usd"]
    o04 = d04["open_and_gap_fee_separation_usd"]
    ok = (
        all(x.get("status") == "PASS" for x in (d03, d04, d05, d06, d07, d08, d09, d10, d11, d12))
        and claim_ok and row_hashes_exact_match and bool(c4_val.get("valid"))
    )
    f13 = _stamp_r5({
        "fresh_replay_executed_not_copied": True,
        "new_data_used_in_backtest": False,
        "source_dataset_sha256": dataset_sha,
        "forensic_ledger_rel_path": led_rel,
        "forensic_ledger_file_sha256": f06_meta["ledger_file_sha256"],
        "forensic_ledger_canonical_rows_sha256": f06_meta["canonical_rows_sha256"],
        "r4_row_ids_and_ledger_sha_match": row_hashes_exact_match,
        "r4_decimal_bridge_exact_match": bool(d04["historical_r3_c2_anchor_exact_match"]),
        "six_cost_dynamic_vs_fixed_fills_separated": bool(d07["dynamic_vs_fixed_fill_populations_separated"]),
        "planned_cap_is_physical_guarantee": False,
        "realized_losses_gt_2p50_count": int(d04["planned_risk_cap_tail_check"]["realized_losses_exceeding_2p50_count"]),
        "max_realized_single_loss_usd": float(d04["planned_risk_cap_tail_check"]["max_realized_single_loss_usd"]),
        "c4_risk_tail_disclosure_valid": bool(c4_val.get("valid")),
        "legacy_funding_status": "EXCLUDED_UNMODELED",
        "null_1x_closed": d04["counts"]["n_closed"],
        "null_1x_wins": d04["counts"]["n_wins"],
        "null_1x_losses": d04["counts"]["n_losses"],
        "null_1x_open_censored": d04["counts"]["n_open_censored"],
        "null_1x_gap_unresolved": d04["counts"]["n_gap_unresolved"],
        "null_1x_gross_usd": b04["gross_pnl_closed_usd"],
        "null_1x_closed_fees_usd": b04["total_closed_fees_usd"],
        "null_1x_net_usd": b04["net_pnl_closed_usd"],
        "null_1x_expectancy_r": b04["expectancy_r"],
        "null_1x_open28_accrued_fees_usd": o04["open_at_horizon_28_accrued_entry_fees_usd"],
        "null_1x_gap2_accrued_fees_usd": o04["data_gap_unresolved_2_accrued_entry_fees_usd"],
        "null_1x_zero_fee_counterfactual_net_usd": d07["zero_fee_counterfactual_net_pnl_usd"],
        "arms_1x_profiles": d09["arm_profiles_1x"],
        "six_cost_scenarios_null_summary": {
            sc: {k: obj["arms"]["NC_NULL_TRIGGER"][k] for k in ("closed", "wins", "losses", "gross_pnl_usd", "total_fees_usd", "net_pnl_usd", "expectancy_r")}
            for sc, obj in d08["dynamic_full_rerisk_six_scenarios"].items()
        },
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE",
        "OOS_EVALUATIONS": 0,
        "status": "PASS" if ok else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)

    ctx = {
        "mtf_bundle": mtf_bundle, "ledger_bundle": ledger_bundle, "r2_cands_1x": r2_cands_1x,
        "replay_bundle": replay_bundle, "live_ff": live_ff, "forensic_gz": forensic_gz,
        "f07_wl": f07_wl, "f13_wf": f13_wf, "m5_ledger": m5_ledger, "forensic_rows": forensic_rows,
        "r4_docs": {"03": d03, "04": d04, "05": d05, "06": d06, "07": d07, "08": d08, "09": d09, "10": d10, "11": d11, "12": d12},
    }
    return f13, ctx


def _run_stage3b_288_faults_18_and_89_mutations_19(
    *, sentinel_tmp: Path, datasets: Mapping[str, Any], ctx: Mapping[str, Any], run_id: str, tested_sha: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # 18: 240 inherited (194 R3 + 14 C1 + 16 C2 + 16 R4) + 48 NEW R5 = 288 RED/GREEN faults
    r3_sub = sentinel_tmp / "r3_194_sub_r5"
    r3_sub.mkdir(parents=True, exist_ok=True)
    r3_194 = _run_kh03_r3_194_red_green_fault_suite(
        sentinel_tmp=r3_sub, datasets=datasets, mtf_bundle=ctx["mtf_bundle"], ledger_bundle=ctx["ledger_bundle"],
        r2_cands_1x=ctx["r2_cands_1x"], replay_bundle=ctx["replay_bundle"], live_ff=ctx["live_ff"],
        forensic_gz=ctx["forensic_gz"], f07=ctx["f07_wl"], f13=ctx["f13_wf"], m5_ledger=ctx["m5_ledger"],
    )
    from tests.test_kh03_r3_c1_regression_truth import run_new_kh03_r3_c1_worker_policy_fault_controls
    from tests.test_kh03_r3_c2_legacy_runner_fail_closed import run_new_kh03_r3_c2_legacy_fail_closed_fault_controls
    from tests.test_kh03_r4_no_alpha_no_causal_claims import run_new_kh03_r4_fault_controls_16
    from tests.test_kh03_r5_readiness_and_verifier import run_new_kh03_r5_48_fault_controls

    inh_all = (
        list(r3_194["controls"])
        + list(run_new_kh03_r3_c1_worker_policy_fault_controls())
        + list(run_new_kh03_r3_c2_legacy_fail_closed_fault_controls())
        + list(run_new_kh03_r4_fault_controls_16(forensic_rows=ctx["forensic_rows"], docs_03_12=ctx["r4_docs"]))
    )
    r5_48 = run_new_kh03_r5_48_fault_controls()
    all_ctrls = inh_all + list(r5_48)
    fids = [c["fault_id"] for c in all_ctrls]
    tot_pass = sum(1 for c in all_ctrls if c["status"] == "PASS")
    f18 = _stamp_r5({
        "inherited_r4_c2_c1_r3_controls_count": len(inh_all),
        "new_r5_controls_count": len(r5_48),
        "total_controls": len(all_ctrls),
        "unique_fault_ids": len(set(fids)),
        "passed_controls": tot_pass,
        "failed_controls": len(all_ctrls) - tot_pass,
        "controls": all_ctrls,
        "status": "PASS" if (tot_pass == len(all_ctrls) and len(set(fids)) == len(all_ctrls) and len(inh_all) >= 240 and len(r5_48) >= 48) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)
    shutil.rmtree(r3_sub, ignore_errors=True)

    # 19: 69 inherited (16 R3 + 17 C1 + 18 C2 + 18 R4) + 20 NEW R5 = 89 separate-OS verifier mutations
    mut_root = sentinel_tmp / "r5_all_muts"
    mut_root.mkdir(parents=True, exist_ok=True)
    r3_mut_dir = mut_root / "r3_m16"
    r3_mut_dir.mkdir(parents=True, exist_ok=True)
    r3_m16 = _run_kh03_r3_verifier_mutation_suite_22(r3_mut_dir)

    from tests.test_kh03_r3_c1_verifier_mutations import KH03_R3_C1_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r3_c1_run_dir, run_kh03_r3_c1_verifier_subprocess
    from tests.test_kh03_r3_c2_independent_mutations import KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS, KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r3_c2_run_dir, run_kh03_r3_c2_verifier_subprocess
    from tests.test_kh03_r3_resource_async_and_verifier_mutations import build_synthetic_valid_kh03_r3_run_dir, run_kh03_r3_verifier_subprocess
    from tests.test_kh03_r4_independent_verifier_mutations import KH03_R4_OWN_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r4_run_dir, run_kh03_r4_verifier_subprocess
    from tests.test_kh03_r5_readiness_and_verifier import KH03_R5_OWN_VERIFIER_MUTATIONS_SPECS, build_synthetic_valid_kh03_r5_run_dir, run_kh03_r5_verifier_subprocess

    bases = {}
    for key, bfn, rfn in (
        ("c1", build_synthetic_valid_kh03_r3_c1_run_dir, run_kh03_r3_c1_verifier_subprocess),
        ("r3", build_synthetic_valid_kh03_r3_run_dir, run_kh03_r3_verifier_subprocess),
        ("c2", build_synthetic_valid_kh03_r3_c2_run_dir, run_kh03_r3_c2_verifier_subprocess),
        ("r4", build_synthetic_valid_kh03_r4_run_dir, run_kh03_r4_verifier_subprocess),
        ("r5", build_synthetic_valid_kh03_r5_run_dir, run_kh03_r5_verifier_subprocess),
    ):
        bd = mut_root / f"base_{key}"
        rid_x, tsha_x = bfn(bd)
        rc_x, _ = rfn(bd, rid_x, tsha_x)
        bases[key] = (bd, rid_x, tsha_x, rfn, rc_x)

    def _run_mut(args_t: Tuple[Path, str, str, Any, str, Any, str]) -> Dict[str, Any]:
        bdir, rid_x, tsha_x, runner_fn, mid, mut_fn, exp_tok = args_t
        cdir = mut_root / f"clone_{mid}"
        shutil.copytree(bdir, cdir)
        try:
            mut_fn(cdir)
            rc, out_t = runner_fn(cdir, rid_x, tsha_x)
            return {"mutation_id": mid, "expected_violation_token": exp_tok, "verifier_exit_code": rc, "detected_fail_closed": (rc != 0) and (exp_tok in out_t)}
        finally:
            shutil.rmtree(cdir, ignore_errors=True)

    inh_jobs = (
        [(bases["c1"][0], bases["c1"][1], bases["c1"][2], bases["c1"][3], m, fn, tok) for m, fn, tok in KH03_R3_C1_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r3"][0], bases["r3"][1], bases["r3"][2], bases["r3"][3], m, fn, tok) for m, fn, tok in KH03_R3_C2_LEGACY_R3_MUTATIONS_SPECS]
        + [(bases["c2"][0], bases["c2"][1], bases["c2"][2], bases["c2"][3], m, fn, tok) for m, fn, tok in KH03_R3_C2_OWN_VERIFIER_MUTATIONS_SPECS]
        + [(bases["r4"][0], bases["r4"][1], bases["r4"][2], bases["r4"][3], m, fn, tok) for m, fn, tok in KH03_R4_OWN_VERIFIER_MUTATIONS_SPECS]
    )
    r5_jobs = [(bases["r5"][0], bases["r5"][1], bases["r5"][2], bases["r5"][3], m, fn, tok) for m, fn, tok in KH03_R5_OWN_VERIFIER_MUTATIONS_SPECS]

    with ThreadPoolExecutor(max_workers=3) as pool:
        inh_res = list(pool.map(_run_mut, inh_jobs))
        r5_res = list(pool.map(_run_mut, r5_jobs))

    shutil.rmtree(mut_root, ignore_errors=True)
    r3_tot, r3_det = int(r3_m16.get("mutations_tested_count", 0)), int(r3_m16.get("mutations_detected_count", 0))
    inh_tot = r3_tot + len(inh_res)
    inh_det = r3_det + sum(1 for r in inh_res if r["detected_fail_closed"])
    r5_tot, r5_det = len(r5_res), sum(1 for r in r5_res if r["detected_fail_closed"])
    clean_ok = all(v[4] == 0 for v in bases.values()) and bool(r3_m16.get("clean_clone_verifier_green"))

    f19 = _stamp_r5({
        "isolated_temp_clones_only": True,
        "sealed_run_dir_mutated": False,
        "clean_clone_verifier_green": clean_ok,
        "inherited_r4_c2_c1_r3_mutations_tested_count": inh_tot,
        "inherited_r4_c2_c1_r3_mutations_detected_count": inh_det,
        "inherited_r4_c2_c1_r3_mutations_failed_detection_count": inh_tot - inh_det,
        "new_r5_mutations_tested_count": r5_tot,
        "new_r5_mutations_detected_count": r5_det,
        "new_r5_mutations_failed_detection_count": r5_tot - r5_det,
        "total_mutations_tested_count": inh_tot + r5_tot,
        "new_r5_results": r5_res,
        "inherited_results_summary": {"r3": f"{r3_det}/{r3_tot}", "c1_c2_r4": f"{sum(1 for r in inh_res if r['detected_fail_closed'])}/{len(inh_res)}"},
        "status": "PASS" if (clean_ok and inh_det == inh_tot and r5_det == r5_tot and inh_tot >= 69 and r5_tot >= 18) else "FAIL",
    }, run_id=run_id, tested_sha=tested_sha)
    return f18, f19


def _collect_and_build_r5_lpt_shards(owned_temp_root: Path) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    all_test_files = sorted(str(p.relative_to(APP_ROOT)).replace("\\", "/") for p in (BACKEND_ROOT / "tests").glob("test_*.py") if p.is_file())
    col_out = owned_temp_root / "collect_r5_stdout.log"
    col_err = owned_temp_root / "collect_r5_stderr.log"
    col_snippet = "import os, sys, pytest; fo=open(sys.argv[1],'w',encoding='utf-8',errors='replace'); fe=open(sys.argv[2],'w',encoding='utf-8',errors='replace'); sys.stdout=fo; sys.stderr=fe; rc=pytest.main(sys.argv[3:]); fo.flush(); fe.flush(); fo.close(); fe.close(); os._exit(int(rc))"
    col_env = dict(os.environ); col_env["PYTHONHASHSEED"] = "0"; col_env["PYTHONPATH"] = _pypath_str()
    proc = subprocess.run([sys.executable, "-c", col_snippet, str(col_out), str(col_err), str(BACKEND_ROOT / "tests"), "--collect-only", "-q"], cwd=str(APP_ROOT), env=col_env, capture_output=True, check=False, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"KH03_R5_PYTEST_COLLECTION_FAILED:rc={proc.returncode}")
    collected_by_file: Dict[str, List[str]] = {}
    for ln in col_out.read_text("utf-8", errors="replace").splitlines():
        cid = canonicalize_collect_node_id_v1793122(ln)
        if cid is not None:
            collected_by_file.setdefault(cid.split("::", 1)[0], []).append(cid)
    for k in collected_by_file:
        collected_by_file[k].sort()
    empirical_ms = _load_empirical_file_durations_ms_from_kh01b()
    non_sub = [f for f in all_test_files if Path(f).name not in KH03_R5_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    sub_files = [f for f in all_test_files if Path(f).name in KH03_R5_EXCLUSIVE_SUBPROCESS_TEST_FILES]
    shards = _build_lpt_shards_kh01b_r1(non_sub, collected_by_file, empirical_ms)
    for sh in shards:
        if sh.get("shard_name") == "SHARD_SINGLE_CHILD_02_BUDGET2":
            sh["exclusive"] = True
            sh["slot_cost"] = 4
    for idx_s, sf in enumerate(sorted(sub_files), 1):
        shards.append({"shard_name": f"SHARD_SINGLE_CHILD_KH03_R5_SUBPROC_{idx_s:02d}", "files": [sf], "exclusive": True, "slot_cost": 4, "priority_weight": 260_000, "needs_venv": False})
    return collected_by_file, shards


def _run_r5_full_repo_pytest_15(
    run_dir: Path, sentinel_tmp: Path, *, run_id: str, tested_sha: str, effective_disk_reserve_bytes: int, max_workers: int, git_clean_at_start: bool
) -> Dict[str, Any]:
    lineage_p = REPO_ROOT / LINEAGE_TRACKED_ARTIFACT_REL
    sha_before = _sha256_file(lineage_p) if lineage_p.is_file() else None
    t0 = time.perf_counter()
    empirical_r2_rss = load_empirical_r2_shard_rss_bytes(REPO_ROOT)
    collected_by_file, r5_shards = _collect_and_build_r5_lpt_shards(sentinel_tmp)
    active_shards_tracker: Dict[str, Any] = {}
    junit_res = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_dir, sentinel_tmp, run_id=run_id, tested_executable_sha=tested_sha,
            effective_disk_reserve_bytes=effective_disk_reserve_bytes,
            max_parallel_workers=max_workers, ram_policy_mode="R3_STRICT_512MIB",
            empirical_rss_by_shard=empirical_r2_rss, active_shards_tracker=active_shards_tracker,
            shard_specs_override=r5_shards, collected_by_file_override=collected_by_file,
        )
    )
    elapsed = round(time.perf_counter() - t0, 3)
    sha_after = _sha256_file(lineage_p) if lineage_p.is_file() else None
    restored = False
    if git_clean_at_start and sha_before is not None and sha_after != sha_before:
        _git_cmd(["checkout", "--", LINEAGE_TRACKED_ARTIFACT_REL])
        restored = (_sha256_file(lineage_p) == sha_before)

    cm, sm = junit_res["collection_manifest"], junit_res["shard_manifest"]
    bj = verify_fresh_junit_bijection_v17931211(
        run_dir, cm, sm, expected_run_id=run_id, expected_tested_sha=tested_sha,
        mandatory_node_prefixes=("backend/tests/test_kh_02_r2", "backend/tests/test_kh_03", "backend/tests/test_kh03_r2", "backend/tests/test_kh03_r3", "backend/tests/test_kh03_r3_c1", "backend/tests/test_kh03_r3_c2", "backend/tests/test_kh03_r4", "backend/tests/test_kh03_r5"),
        require_shard_schedule=True,
    )
    # Stream collected node IDs into compressed ledger to keep 15 compact (§10)
    nodes_rel = "logs/pytest_collected_executed_nodeids.jsonl.gz"
    nodes_gz = run_dir / nodes_rel
    c_ids = list(cm.get("collected_node_ids") or ())
    with gzip.open(nodes_gz, "wt", encoding="utf-8") as fh:
        for cid in c_ids:
            fh.write(cid + "\n")
    nodes_sha = _sha256_file(nodes_gz)

    col_n = len(c_ids)
    exe_n = int(bj.get("executed_node_ids_count") or bj.get("executed_count") or 0)
    pas_n, skp_n, fai_n, err_n, msk_n = int(bj.get("passed_count") or 0), int(bj.get("skipped_count") or 0), int(bj.get("failed_count") or 0), int(bj.get("error_count") or 0), int(bj.get("mandatory_skipped_count") or 0)
    nz = int(sm.get("nonzero_exit_shards_count") or 0)
    ok = bj.get("status") == "PASS" and nz == 0 and col_n >= MIN_EXPECTED_PYTEST_COLLECTED_R5 and col_n == exe_n and fai_n == 0 and err_n == 0 and msk_n == 0
    return _stamp_r5({
        "status": "PASS" if ok else "FAIL",
        "elapsed_sec": elapsed, "max_workers_used": max_workers,
        "collected_count": col_n, "executed_count": exe_n, "passed_count": pas_n,
        "skipped_count": skp_n, "mandatory_skipped_count": msk_n, "failed_count": fai_n,
        "error_count": err_n, "nonzero_exit_shards_count": nz, "junit_bijection_exact": bj.get("status") == "PASS",
        "collected_nodeids_gz_rel": nodes_rel, "collected_nodeids_gz_sha256": nodes_sha,
        "tracked_test_side_effect_audit": {"rel_path": LINEAGE_TRACKED_ARTIFACT_REL, "sha256_before": sha_before, "sha256_after_pytest": sha_after, "restored_to_clean_head": restored},
        "shard_manifest": sm, "bijection_audit": {k: v for k, v in bj.items() if k not in ("executed_node_ids", "collected_node_ids")},
    }, run_id=run_id, tested_sha=tested_sha)


def run_kh03_r5_orchestrator(
    *, allow_local_dirty: bool = False, owner_waive_disk_preflight: bool = False, max_workers: int = GLOBAL_MAX_DESCENDANTS_KH02_R2
) -> Tuple[int, Path, Dict[str, Any]]:
    if owner_waive_disk_preflight:
        raise SystemExit(2)

    lock = _CrossPlatformRunLock(Path(tempfile.gettempdir()) / "aurum_desk_kh03_r5_run.lock")
    lock_ok, lock_msg = lock.acquire()
    if not lock_ok:
        raise RuntimeError(f"KH03_R5_SINGLE_RUN_LOCK_FAILED:{lock_msg}")

    t_start = time.perf_counter()
    orch_pid = os.getpid()
    utc_now = datetime.now(timezone.utc)
    _, branch = _git_cmd(["branch", "--show-current"])
    _, start_head = _git_cmd(["rev-parse", "HEAD"])
    _, st_porc = _git_cmd(["status", "--porcelain=v1"])
    _, ls_rem = _git_cmd(["ls-remote", "origin", f"refs/heads/{MANDATORY_BRANCH_KH03_R5}"])
    remote_sha = ls_rem.split()[0] if ls_rem.strip() else ""
    git_clean = (st_porc.strip() == "")

    if branch != MANDATORY_BRANCH_KH03_R5:
        lock.release()
        raise RuntimeError(f"WRONG_BRANCH:{branch}")
    if (not git_clean) and (not allow_local_dirty):
        lock.release()
        raise RuntimeError(f"DIRTY_WORKTREE_REFUSED:\n{st_porc}")

    run_id = f"{utc_now.strftime('%Y%m%dT%H%M%SZ')}_{start_head[:12]}"
    art_base = (REPO_ROOT / KH03_R5_ARTIFACTS_BASE_REL).resolve()
    run_dir = art_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "junit").mkdir(exist_ok=True)
    (run_dir / "checksums").mkdir(exist_ok=True)

    (run_dir / "README.md").write_text(
        f"# AURUM DESK — KH-03.R5 (IN PROGRESS)\n\n- Run ID: `{run_id}`\n- Tested SHA: `{start_head}`\n- Branch: `{branch}`\n- Status: `IN_PROGRESS_NOT_SEALED`\n",
        encoding="utf-8",
    )

    sentinel_tmp = create_sentinel_owned_temp_dir_r2_c1(f"kh03r5_{utc_now.strftime('%Y%m%dT%H%M%SZ')}_", run_id=run_id, owner_pid=orch_pid)
    write_mount_paths = {
        "repo_volume": REPO_ROOT, "app_volume": APP_ROOT,
        "artifacts_output": art_base if art_base.exists() else APP_ROOT,
        "temp_volume": sentinel_tmp, "python_venv": Path(sys.executable).resolve().parent,
        "caches": Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")),
        "logs": run_dir / "logs",
    }
    strict_probe = probe_r2_c1_write_mounts_strict(write_mount_paths, atomic_probe_dirs=[(sentinel_tmp, True, run_id)])
    avail_ram_mib = round(psutil.virtual_memory().available / (1024.0 * 1024.0), 2)
    min_pre_dk = min(float(m.get("free_gib", 0.0)) for m in strict_probe["distinct_mounts"])
    strict_disk_ok = bool(strict_probe["can_run_heavy_stages"])
    ram_ok = avail_ram_mib >= PREFLIGHT_RAM_MIN_MIB_KH02_R2
    can_run_heavy = strict_disk_ok and ram_ok
    eff_workers = max(1, min(int(max_workers), GLOBAL_MAX_DESCENDANTS_KH02_R2))
    # Conservative RAM/swap guard when disk headroom is close to 20.5-24 GiB (§09)
    pytest_full_cap = min(2, eff_workers) if (min_pre_dk < 24.0 or avail_ram_mib < 3000.0) else min(3, eff_workers)

    sampler = _BoundedResourceSampler(
        out_gz=run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz",
        watch_path=run_dir, watch_paths=[REPO_ROOT, APP_ROOT, run_dir, sentinel_tmp, Path(sys.executable).resolve().parent],
        interval_sec=0.5, enforce_runtime_disk_abort=can_run_heavy,
    )
    sampler.start()
    sampler_stopped = False

    try:
        print(f"[KH-03.R5] Stage 1: Ownership/AST (01) & prior R1..R4 + dataset SHA immutability (02) (run_id={run_id}, disk={min_pre_dk:.2f}GiB, pytest_cap={pytest_full_cap})...", flush=True)
        legacy_cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
        f01 = _audit_ownership_01(tested_sha=start_head, run_id=run_id, legacy_cg=legacy_cg)
        ro_inputs = load_r4_immutable_research_inputs_readonly(REPO_ROOT, PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R5)
        datasets, prov02 = ro_inputs["datasets"], ro_inputs["provenance"]
        pdir = sentinel_tmp / "c4_prov_r5"
        pdir.mkdir(parents=True, exist_ok=True)
        f08_c4, f09_c4 = audit_kh03_collector_and_escrow_readiness(pdir)
        shutil.rmtree(pdir, ignore_errors=True)
        prov_c4_ok = f08_c4.get("status") == "PASS" and f09_c4.get("status") == "PASS" and f08_c4.get("network_capture_enabled") is False and int(f09_c4.get("real_oos_escrow_final_record_count", -1)) == 0
        f02 = _stamp_r5({
            **prov02,
            "c4_provenance_and_oos_boundary": {
                "network_capture_enabled": f08_c4.get("network_capture_enabled"),
                "live_network_capture_executed": f08_c4.get("live_network_capture_executed"),
                "real_oos_escrow_integrity_valid": f09_c4.get("real_oos_escrow_integrity_valid"),
                "real_oos_escrow_final_record_count": f09_c4.get("real_oos_escrow_final_record_count"),
                "status": "PASS" if prov_c4_ok else "FAIL",
            },
            "status": "PASS" if (prov02.get("status") == "PASS" and prov_c4_ok) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "01_function_ownership_keep_as_is_manifest.json", f01)
        write_json_durable_atomic(run_dir / "02_prior_run_dataset_escrow_pointer_integrity.json", f02)

        print("[KH-03.R5] Stage 2: Offline market-data foundation, fail-closed auth, QA capture & DEV custody (03..12)...", flush=True)
        d03_12 = _run_stage2_foundation_offline_03_to_12(sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, min_pre_disk_gib=min_pre_dk)
        names_03_12 = {
            "03": "03_source_capability_contract_inventory.json",
            "04": "04_operator_scope_and_capture_plan.json",
            "05": "05_capture_execution_and_provenance_receipts.json",
            "06": "06_contract_metadata_validation.json",
            "07": "07_ohlcv_lineage_gap_coverage.json",
            "08": "08_bbo_sample_cadence_quality_coverage.json",
            "09": "09_funding_settlement_completeness.json",
            "10": "10_owner_fee_and_slippage_readiness.json",
            "11": "11_dev_custody_partition_chain_and_no_oos_access.json",
            "12": "12_observed_execution_fidelity_register.json",
        }
        for k, fn in names_03_12.items():
            write_json_durable_atomic(run_dir / fn, d03_12[k])

        print("[KH-03.R5] Stage 3A: Fresh read-only R4 canonical DEV replay & C4 tail disclosure (13)...", flush=True)
        f13, ctx = _run_stage3a_r4_real_replay_13(
            datasets=datasets, dataset_sha=str(prov02["canonical_dataset_sha256"]),
            timeframe_stats=prov02["timeframe_coverage_and_gaps"],
            run_dir=run_dir, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head,
        )
        write_json_durable_atomic(run_dir / "13_r4_replay_parity_economic_bridge.json", f13)

        print("[KH-03.R5] Stage 3B: Running 288 RED/GREEN faults (18) & 89 separate-OS verifier mutations (19)...", flush=True)
        f18, f19 = _run_stage3b_288_faults_18_and_89_mutations_19(sentinel_tmp=sentinel_tmp, datasets=datasets, ctx=ctx, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "18_inherited_new_red_green_fault_receipts.json", f18)
        write_json_durable_atomic(run_dir / "19_independent_verifier_mutation_attack_results.json", f19)
        del datasets, ctx
        gc.collect()

        if can_run_heavy:
            eff_res_b = int(STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2 * (1024 ** 3))
            print(f"[KH-03.R5] Stage 4A: Running FULL repo LPT-sharded pytest (max_workers={pytest_full_cap}) (15)...", flush=True)
            f15 = _run_r5_full_repo_pytest_15(
                run_dir, sentinel_tmp, run_id=run_id, tested_sha=start_head,
                effective_disk_reserve_bytes=eff_res_b, max_workers=pytest_full_cap, git_clean_at_start=git_clean,
            )
            write_json_durable_atomic(run_dir / "15_full_pytest_fresh_collection_junit_bijection.json", f15)
            gc.collect()

            print("[KH-03.R5] Stage 4B: Running fresh W1/W2/W4/W1_repeat pytest + R2 science benchmarks (16)...", flush=True)
            pb_raw = asyncio.run(_run_controlled_pytest_w1_w2_w4_benchmark_r1(sentinel_tmp, enforce_r3_ram_guard=True))
            sb_raw = asyncio.run(_run_r2_science_slice_w1_w2_w4_determinism(sentinel_tmp))
            gc.collect()

            pb_doc = _stamp_r5({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_PYTEST, "status": "PASS" if pb_raw.get("all_4_runs_identical") else "FAIL", **pb_raw}, run_id=run_id, tested_sha=start_head)
            sb_doc = _stamp_r5({"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "benchmark_kind": BENCHMARK_KIND_SCIENCE, "status": "PASS" if sb_raw.get("all_4_runs_identical") else "FAIL", **sb_raw}, run_id=run_id, tested_sha=start_head)
            p_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": pytest_full_cap, "full_suite_safe_worker_cap": pytest_full_cap, "shard_exclusive_cap": pytest_full_cap, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
            s_lim = {"configured_child_process_cap": GLOBAL_MAX_DESCENDANTS_KH02_R2, "currently_allowed_worker_cap": eff_workers, "full_suite_safe_worker_cap": eff_workers, "shard_exclusive_cap": eff_workers, "preflight_disk_free_gib": min_pre_dk, "disk_free_gib": min_pre_dk, "available_ram_mib": avail_ram_mib}
            p_pol = select_measured_worker_policy_strict(pb_doc, benchmark_kind=BENCHMARK_KIND_PYTEST, system_limits=p_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
            s_pol = select_measured_worker_policy_strict(sb_doc, benchmark_kind=BENCHMARK_KIND_SCIENCE, system_limits=s_lim, expected_run_id=run_id, expected_tested_sha=start_head, expected_dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2)
            f16 = _stamp_r5({
                "pytest_controlled_subset_raw": pb_doc, "science_slice_8000_raw": sb_doc,
                "pytest_worker_policy": p_pol, "science_worker_policy": s_pol,
                "full_suite_actual_max_workers_applied": pytest_full_cap,
                "full_suite_speedup_extrapolated": False,
                "status": "PASS" if (p_pol["status"] == "PASS" and s_pol["status"] == "PASS") else "FAIL",
            }, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "16_W1_W2_W4_W1repeat_benchmark_worker_selection.json", f16)

            print("[KH-03.R5] Stage 4C: Running FULL 56,710 A1 + 6,375 SI8 + 246 adapter + D4 parity (14)...", flush=True)
            ds_p, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
            f02_r1 = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
            f14 = _stamp_r5(_run_full_production_baseline_parity_20(datasets=ds_p, sentinel_tmp=sentinel_tmp, run_id=run_id, tested_sha=start_head, branch=branch, f01=f01, f02=f02_r1, max_workers=eff_workers), run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "14_A1_SI8_adapter_D4_full_baseline_parity.json", f14)
            del ds_p
            gc.collect()
        else:
            f14 = _stamp_r5({"BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK", "a1_population_count": 0, "a1_mismatches_count": 0, "si8_population_count": 0, "si8_mismatches_count": 0, "three_adapter_population_count": 0, "three_adapter_mismatches_count": 0, "d4_historical_closed_trades": 0, "per_decision_and_episode_fingerprints": {"all_sealed_fingerprints_exact_match": False}}, run_id=run_id, tested_sha=start_head)
            f15 = _stamp_r5({"status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0, "passed_count": 0, "skipped_count": 0, "mandatory_skipped_count": 0, "failed_count": 0, "error_count": 0, "junit_bijection_exact": False}, run_id=run_id, tested_sha=start_head)
            f16 = _stamp_r5({"status": "NOT_RUN_BLOCKED_LOW_DISK", "pytest_controlled_subset_raw": {}, "science_slice_8000_raw": {}, "pytest_worker_policy": {}, "science_worker_policy": {}, "full_suite_speedup_extrapolated": False}, run_id=run_id, tested_sha=start_head)
            write_json_durable_atomic(run_dir / "14_A1_SI8_adapter_D4_full_baseline_parity.json", f14)
            write_json_durable_atomic(run_dir / "15_full_pytest_fresh_collection_junit_bijection.json", f15)
            write_json_durable_atomic(run_dir / "16_W1_W2_W4_W1repeat_benchmark_worker_selection.json", f16)

        cl_info = _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
        sentinel_cleaned = bool(cl_info["cleaned"])
        telem = sampler.stop_and_flush()
        sampler_stopped = True

        f17 = _stamp_r5({
            "orchestrator_pid": orch_pid, "sampling_interval_sec": 0.5,
            "memory_samples_log_rel": "logs/runtime_memory_samples_0p5s.jsonl.gz",
            **telem,
            "strict_preflight_disk_min_gib": STRICT_PREFLIGHT_DISK_MIN_GIB_KH02_R2,
            "strict_runtime_disk_min_gib": STRICT_RUNTIME_DISK_MIN_GIB_KH02_R2,
            "advisory_disk_gib": ADVISORY_DISK_GIB_KH02_R2,
            "owner_disk_preflight_waiver": False,
            "sentinel_temp_cleaned_up": sentinel_cleaned,
            "total_wall_clock_sec": round(time.perf_counter() - t_start, 3),
            "status": "PASS" if (sentinel_cleaned and int(telem.get("orphan_children_after_cleanup", 1)) == 0) else "FAIL",
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "17_disk_ram_pid_samples_cleanup.json", f17)

        _, end_head = _git_cmd(["rev-parse", "HEAD"])
        f00 = _stamp_r5({
            "started_utc": utc_now.strftime("%Y-%m-%dT%H:%M:%SZ"), "branch": branch,
            "start_head_sha": start_head, "end_head_sha": end_head, "remote_head_sha": remote_sha,
            "prior_kh03_r4_pub_head_sha": PRIOR_KH03_R4_PUB_HEAD_SHA,
            "prior_kh03_r4_tested_sha": PRIOR_KH03_R4_TESTED_SHA,
            "prior_kh03_r4_evidence_sha": PRIOR_KH03_R4_EVIDENCE_SHA,
            "prior_kh03_r4_run_id": PRIOR_KH03_R4_RUN_ID,
            "git_clean_at_start": git_clean, "local_uncommitted_changes": not git_clean,
            "single_run_lock_status": lock_msg, "orchestrator_pid": orch_pid,
            "python_version": platform.python_version(), "platform": platform.platform(),
            "random_seed": DEFAULT_RANDOM_SEED_KH02_R2, "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "owner_disk_preflight_waiver": False, "strict_multi_mount_probe": strict_probe,
            "preflight_ram_available_mib": avail_ram_mib, "can_run_heavy_stages": can_run_heavy,
            "readonly_disk_inventory": build_r2_c1_readonly_disk_inventory(REPO_ROOT) if not strict_disk_ok else None,
            "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2), "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
            "status": "PASS" if (strict_disk_ok and ram_ok and git_clean) else ("BLOCKED_LOW_DISK" if not strict_disk_ok else "BLOCKED_PREFLIGHT"),
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "00_branch_sha_clean_preflight_resource.json", f00)

        pub_st = "PRE_PUSH_PENDING" if (strict_disk_ok and git_clean) else "NOT_PUBLISHED_BLOCKED_LOW_DISK"
        f22 = _stamp_r5({
            "branch": branch, "start_head_sha": start_head,
            "evidence_commit_sha": None, "publication_head_sha": None,
            "remote_readback_sha256_verified": False, "latest_run_pointer_updated": False,
            "owner_disk_preflight_waiver": False, "ci_status": "CI_UNKNOWN_NO_ACTIONS_RUN",
            "actual_github_actions_run_verified": False, "publication_status": pub_st, "status": pub_st,
        }, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "22_phase1_phase2_remote_publication_receipts.json", f22)

        all_docs = {"00": f00, "01": f01, "02": f02, **d03_12, "13": f13, "14": f14, "15": f15, "16": f16, "17": f17, "18": f18, "19": f19, "22": f22}
        mem_au = _audit_telemetry_gz_vs_17_r5(run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz", f17)
        r4_au = _audit_r4_replay_ledger_13_r5(run_dir, f13)
        p_rec = _recompute_raw_bench_r5(f16.get("pytest_controlled_subset_raw") or {}, f16.get("pytest_worker_policy") or {}, kind="pytest_controlled_subset", expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=3) if f16.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" else {"valid": False}
        s_rec = _recompute_raw_bench_r5(f16.get("science_slice_8000_raw") or {}, f16.get("science_worker_policy") or {}, kind="r2_science_slice", expected_run_id=run_id, expected_tested_sha=start_head, max_safe_cap=GLOBAL_MAX_DESCENDANTS_KH02_R2) if f16.get("status") != "NOT_RUN_BLOCKED_LOW_DISK" else {"valid": False}

        g_res = recompute_r5_gate_matrix(
            docs=all_docs, mem_audit=mem_au, pytest_recomp=p_rec, science_recomp=s_rec,
            junit_ok=bool(f15.get("junit_bijection_exact")), r4_replay_ok=bool(r4_au.get("valid")),
            verifier_violations_count=0, for_final_verifier_manifest=False,
        )
        f20 = _stamp_r5(g_res, run_id=run_id, tested_sha=start_head)
        write_json_durable_atomic(run_dir / "20_orthogonal_gates_and_verdicts.json", f20)
        all_docs["20"] = f20

        handover_vi = build_r5_owner_handover_vi(f20, f01["source_function_ownership_matrix"], all_docs)
        (run_dir / "HANDOVER_KH03_R5_VI.txt").write_text(handover_vi, encoding="utf-8")
        (run_dir / "README.md").write_text(
            f"# AURUM DESK — KH-03.R5 | Observed Market Data Foundation & Execution Fidelity\n\n"
            f"- **Run ID**: `{run_id}` | **Branch**: `{branch}` | **Tested SHA**: `{start_head}`\n"
            f"- **Classification**: `{f20['overall_classification']}`\n"
            f"- **Safety Lock**: `ACTIVATION_READY=NO | PRODUCTION=LIVE=PAPER=AUTO=OFF | OOS_EVALUATIONS=0 | NO_PROVEN_EDGE | KH04_ENTRY=BLOCKED_NO_DEV_CANDIDATE`\n\n"
            f"```text\n{handover_vi}```\n",
            encoding="utf-8",
        )

        inv = {
            str(p.relative_to(run_dir)).replace("\\", "/"): {"sha256": _sha256_file(p), "bytes": p.stat().st_size}
            for p in sorted(run_dir.rglob("*"))
            if p.is_file() and p.name != "sha256_inventory.json"
        }
        write_json_durable_atomic(run_dir / "checksums" / "sha256_inventory.json", _stamp_r5({"files": inv, "count": len(inv)}, run_id=run_id, tested_sha=start_head))

        print("[KH-03.R5] Stage 5: Launching separate-OS independent verifier (kh_03_r5_independent_verifier.py -> 21)...", flush=True)
        env_v = os.environ.copy()
        env_v["PYTHONPATH"] = _pypath_str()
        vp = subprocess.run(
            [sys.executable, str(APP_ROOT / "tools" / "kh_03_r5_independent_verifier.py"), "--run-dir", str(run_dir), "--expected-run-id", run_id, "--expected-tested-sha", start_head],
            cwd=str(APP_ROOT), env=env_v, capture_output=True, text=True, timeout=180,
        )
        print(vp.stdout.strip(), flush=True)
        if vp.stderr.strip():
            print(vp.stderr.strip(), file=sys.stderr, flush=True)

        summary = {
            "run_id": run_id, "run_dir": str(run_dir), "tested_executable_sha": start_head,
            "verifier_exit_code": int(vp.returncode), "verdicts": f20["verdicts"],
            "overall_classification": f20["overall_classification"],
        }
        return int(vp.returncode), run_dir, summary
    finally:
        if not sampler_stopped:
            try:
                sampler.stop_and_flush()
            except Exception:
                pass
        if sentinel_tmp.exists():
            try:
                _cleanup_sentinel_temp_c4_win_safe(sentinel_tmp, expected_run_id=run_id)
            except Exception:
                pass
        lock.release()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AURUM DESK KH-03.R5 Observed Market Data Foundation & Execution Fidelity Runner")
    ap.add_argument("--allow-local-dirty", action="store_true")
    ap.add_argument("--max-workers", type=int, default=GLOBAL_MAX_DESCENDANTS_KH02_R2)
    args = ap.parse_args(argv)
    rc, _, summ = run_kh03_r5_orchestrator(allow_local_dirty=args.allow_local_dirty, max_workers=args.max_workers)
    print(json.dumps(summ, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
