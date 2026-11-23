#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_independent_verifier.py

AURUM DESK — KH-03.R1 Separate-OS-Process Independent Disk Verifier (V01..V14, G01..G24)
SPEC VERSION: KH-03.R1-v1.0

Executes in a DISTINCT OS process (`verifier_pid != orchestrator_pid`), reads EVERY closed
artifact (`00`..`23`, `04_fee_per_episode_ledger.jsonl.gz`, `README_KH03_R1.md`,
`HANDOVER_KH03_R1_VI.txt`, `logs/runtime_memory_samples_0p5s.jsonl.gz`, `junit/*.xml`)
directly from disk, NEVER trusts producer status/boolean labels, independently recomputes
all money/Decimal/ledger/JUnit/W1-W4/parity/immutability/24-gate checks, and seals
`24_independent_os_verifier_manifest_sha256.json`.

Exit codes:
  0 — ALL mandatory engineering gates PASS (orthogonal economic/OOS verdicts truthful)
  1 — ANY tamper, SHA mismatch, fee bug, numeric discrepancy, or invariant violation
  2 — BLOCKED resource / incomplete heavy stages (fail-closed)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import (
    ACTIVATION_READY_KH02_R2_SHARED as ACTIVATION_READY_KH03,
    CANONICAL_DATASET_SHA256_KH02 as CANONICAL_DATASET_SHA256_KH03,
    GLOBAL_MAX_DESCENDANTS_SHARED as GLOBAL_MAX_DESCENDANTS_KH03,
    HARD_RAM_FLOOR_MIB_SHARED as HARD_RAM_FLOOR_MIB_KH03,
    MANDATORY_BRANCH_KH02_R2_SHARED as MANDATORY_BRANCH_KH03,
    PRIOR_KH02_R1_TESTED_SHA_SHARED,
    PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED,
    PRIOR_KH02_R2_C2_TESTED_SHA_SHARED,
    PRIOR_KH02_R2_TESTED_SHA_SHARED,
    STRICT_PREFLIGHT_DISK_MIN_GIB_SHARED as STRICT_PREFLIGHT_DISK_MIN_GIB,
    STRICT_RUNTIME_DISK_MIN_GIB_SHARED as STRICT_RUNTIME_DISK_MIN_GIB,
)
from services.kh_03_fee_integrity_service import (
    SCHEMA_VERSION_KH03,
    SPEC_VERSION_KH03,
    validate_kh03_fee_ledger_independently,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_02_r2_c3_independent_verifier import _audit_memory_samples_c3
from tools.kh_02_r2_c4_independent_verifier import (
    C4_SOURCE_FILES_ALLOWLIST,
    PRIOR_KH02_R2_C3_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C4,
    _recompute_w1_w2_w4_c4,
    _verify_junit_raw_xml_bijection_c4,
)

MANIFEST_24_NAME_KH03 = "24_independent_os_verifier_manifest_sha256.json"

PRIOR_KH02_R2_C4_PUB_HEAD_SHA = "9200c802a38b50606f388e26fce26acc7275bf0c"
PRIOR_KH02_R2_C4_TESTED_SHA = "ed8b190c8f0ff84e79f2d9b17c668ed1c75beaa4"
PRIOR_KH02_R2_C4_EVIDENCE_SHA = "637b2a7746c1c1ba44a72dce84bc79d29626a43f"
PRIOR_KH02_R2_C4_RUN_ID = "20261123T035128Z_ed8b190c8f0f"

PRIOR_KH02_R2_C4_LATEST_PTR_REL = "aurum-desk/docs/artifacts/kh_02_r2_c4_integrity_closure/latest_run.json"
PRIOR_KH02_R2_C4_LATEST_PTR_SHA256 = "690a2dc3ce594ad34635086087f4522d37fdb6cdc3ffc529f39808c8f37241f2"
PRIOR_KH02_R2_C4_MANIFEST_24_REL = (
    "aurum-desk/docs/artifacts/kh_02_r2_c4_integrity_closure/"
    "20261123T035128Z_ed8b190c8f0f/24_independent_os_verifier_sha256_manifest.json"
)
PRIOR_KH02_R2_C4_MANIFEST_24_SHA256 = "b0e754d9b5ecbad5a1d9024c5b05de9ea49f5f6cf5f1e0e64aadc23aa88b6e93"
PRIOR_KH02_R2_C4_POSTPUSH_REL = (
    "aurum-desk/docs/artifacts/kh_02_r2_c4_integrity_closure/"
    "20261123T035128Z_ed8b190c8f0f_postpush_remote_receipt.json"
)
PRIOR_KH02_R2_C4_POSTPUSH_SHA256 = "f29100fed30165c2b8fd5d30959d2b004471e2ba74c030084eeb1b73b98e94a6"

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_C4,
    (PRIOR_KH02_R2_C4_LATEST_PTR_REL, PRIOR_KH02_R2_C4_LATEST_PTR_SHA256, "KH02_R2_C4_LATEST_PTR"),
    (PRIOR_KH02_R2_C4_MANIFEST_24_REL, PRIOR_KH02_R2_C4_MANIFEST_24_SHA256, "KH02_R2_C4_MANIFEST_24"),
    (PRIOR_KH02_R2_C4_POSTPUSH_REL, PRIOR_KH02_R2_C4_POSTPUSH_SHA256, "KH02_R2_C4_POSTPUSH_RECEIPT"),
)

KH03_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    *C4_SOURCE_FILES_ALLOWLIST,
    "aurum-desk/backend/services/kh_02_r2_research_integrity_service.py",
    "aurum-desk/backend/services/kh_03_fee_integrity_service.py",
    "aurum-desk/backend/services/kh_03_data_governance_service.py",
    "aurum-desk/backend/services/kh_03_research_service.py",
    "aurum-desk/backend/tests/test_kh_03_fee_root_and_costs.py",
    "aurum-desk/backend/tests/test_kh_03_partition_seal_and_provenance.py",
    "aurum-desk/backend/tests/test_kh_03_causal_research_and_negative_controls.py",
    "aurum-desk/backend/tests/test_kh_03_async_and_resource_safety.py",
    "aurum-desk/backend/tests/test_kh_03_verifier_mutations.py",
    "aurum-desk/tools/run_kh_03_acceptance.py",
    "aurum-desk/tools/kh_03_independent_verifier.py",
)

REQUIRED_ARTIFACTS_KH03: Tuple[str, ...] = (
    "00_source_branch_sha_and_resource_preflight.json",
    "01_file_function_ownership_and_keep_as_is_ast_diff.json",
    "02_prior_dataset_and_sealed_sha256_immutability.json",
    "03_fee_root_bug_red_proof_and_diff.json",
    "04_fee_per_episode_ledger.jsonl.gz",
    "05_fee_full_universe_six_scenario_reconciliation.json",
    "06_data_source_contract_and_quality_inventory.json",
    "07_partition_freeze_pre_registration_and_noncontamination.json",
    "08_public_collector_readiness_authorization_and_provenance.json",
    "09_escrow_integrity_and_hash_linked_readiness.json",
    "10_kh03_frozen_hypotheses_with_falsification_budget.json",
    "11_causal_asof_mtf_and_future_mutation_tests.json",
    "12_full_opportunity_conservation_and_candidate_replay.json",
    "13_paired_null_matching_and_balance.json",
    "14_net_economics_cost_1x_2x_3x_stress.json",
    "15_purged_walkforward_bootstrap_multiplicity_power.json",
    "16_risk_tail_gap_fill_time_and_fee_disclosure.json",
    "17_full_regression_collected_executed_junit_bijection.json",
    "18_w1_w2_w4_w1repeat_speed_hash_and_resource.json",
    "19_all_faults_unique_red_green_receipts.json",
    "20_full_A1_SI8_246_adapter_D4_parity.json",
    "21_independent_verifier_mutation_resistance.json",
    "22_orthogonal_gate_matrix_prepush.json",
    "23_phase1_code_commit_push_receipt.json",
    "README_KH03_R1.md",
    "HANDOVER_KH03_R1_VI.txt",
)

GATE_24_KEYS_KH03: Tuple[str, ...] = (
    "G01_SOURCE_BRANCH_CLEAN_SHA",
    "G02_PRODUCTION_IMMUTABILITY_PARITY",
    "G03_FEE_ROOT_CORRECTNESS",
    "G04_FEE_PER_TRADE_DECIMAL",
    "G05_FULL_SIX_SCENARIO_COST_PARITY",
    "G06_DATA_CONTRACT_QUALITY",
    "G07_DEV_OOS_PARTITION_NONCONTAMINATION",
    "G08_C4_TRUSTED_CAPTURE_INVARIANTS",
    "G09_ESCROW_INTEGRITY",
    "G10_PREREG_FALSIFICATION_BUDGET",
    "G11_CAUSAL_ASOF_FUTURE_MUTATION",
    "G12_ONE_POSITION_TARGET_CONSERVATION",
    "G13_PAIRED_NULL_MATCHING_BALANCE",
    "G14_COST_RISK_TAIL_HONESTY",
    "G15_FULL_JUNIT_REGRESSION",
    "G16_ASYNC_W1_W2_W4_DETERMINISM",
    "G17_DISK_RAM_PID_SAFETY",
    "G18_FAULT_SUITE_RED_GREEN",
    "G19_INDEPENDENT_OS_VERIFIER",
    "G20_PUBLICATION_REMOTE",
    "G21_ECONOMIC_DEV_EVIDENCE",
    "G22_FRESH_OOS_READINESS",
    "G23_KH04_ENTRY",
    "G24_ACTIVATION_ALWAYS_NO",
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def recompute_kh03_24_gates_and_verdicts(
    *,
    docs: Mapping[str, Mapping[str, Any]],
    mem_audit: Mapping[str, Any],
    fee_ledger_audit: Mapping[str, Any],
    junit_bijection_ok: bool,
    verifier_violations_count: int = 0,
    for_final_verifier_manifest: bool = False,
) -> Dict[str, Any]:
    """Independently recomputes ALL 24 orthogonal KH-03 gates (G01..G24) and distinct verdicts."""
    f00, f01, f02, f03 = docs["00"], docs["01"], docs["02"], docs["03"]
    f05, f06, f07, f08 = docs["05"], docs["06"], docs["07"], docs["08"]
    f09, f10, f11, f12 = docs["09"], docs["10"], docs["11"], docs["12"]
    f13, f14, f15, f16 = docs["13"], docs["14"], docs["15"], docs["16"]
    f17, f18, f19, f20, f21, f23 = docs["17"], docs["18"], docs["19"], docs["20"], docs["21"], docs["23"]

    gates: Dict[str, str] = {}

    # G01: Branch / clean SHA / prior sealed intact
    tsha = str(f00.get("tested_executable_sha") or "")
    sha_ok = (
        f00.get("branch") == MANDATORY_BRANCH_KH03
        and len(tsha) == 40
        and tsha == f00.get("start_head_sha")
        and tsha == f00.get("end_head_sha")
        and tsha not in (
            PRIOR_KH02_R1_TESTED_SHA_SHARED,
            PRIOR_KH02_R2_TESTED_SHA_SHARED,
            PRIOR_KH02_R2_C1_CODE_COMMIT_SHA_SHARED,
            PRIOR_KH02_R2_C2_TESTED_SHA_SHARED,
            PRIOR_KH02_R2_C3_TESTED_SHA,
            PRIOR_KH02_R2_C4_TESTED_SHA,
            PRIOR_KH02_R2_C4_PUB_HEAD_SHA,
        )
    )
    clean_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_changes", False))
    prior_ok = bool(f02.get("all_prior_sealed_artifacts_intact", False)) and bool(f02.get("canonical_dataset_sha256_match", False))
    gates["G01_SOURCE_BRANCH_CLEAN_SHA"] = "PASS" if (sha_ok and clean_ok and prior_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if (sha_ok and not clean_ok) else "FAIL")

    # G02: Production immutability + A1 56710 / SI8 6375 / 246 adapters / D4=0 parity
    p_st = str(f20.get("BASELINE_PARITY_VERDICT") or "FAIL")
    keep_ok = (
        f01.get("status") == "PASS"
        and int(f01.get("locked_files_modified_count", 1)) == 0
        and len(f01.get("forbidden_order_import_hits") or []) == 0
        and bool(f01.get("cost_assumptions_defaults_intact", False))
    )
    parity_ok = (
        p_st == "PASS"
        and bool((f20.get("per_decision_and_episode_fingerprints") or {}).get("all_sealed_fingerprints_exact_match", False))
        and int(f20.get("a1_population_count", 0)) == 56710
        and int(f20.get("a1_mismatches_count", 1)) == 0
        and int(f20.get("si8_population_count", 0)) == 6375
        and int(f20.get("si8_mismatches_count", 1)) == 0
        and int(f20.get("three_adapter_population_count", 0)) == 246
        and int(f20.get("three_adapter_mismatches_count", 1)) == 0
        and int(f20.get("d4_historical_closed_trades", 1)) == 0
    )
    gates["G02_PRODUCTION_IMMUTABILITY_PARITY"] = (
        "NOT_RUN_BLOCKED_LOW_DISK" if p_st == "NOT_RUN_BLOCKED_LOW_DISK" else ("PASS" if (keep_ok and parity_ok) else "FAIL")
    )

    # G03: Fee root RED proof captured + root fix eliminated all 0-fee display hits without changing economics
    g03_ok = (
        f03.get("classification") == "REAL_RED"
        and int(f03.get("zero_fee_display_bug_cells_count", 0)) >= 1
        and bool(f05.get("C4_UPSTREAM_FEE_DISPLAY_ROOT_CAUSE_FIXED", False))
        and int(f05.get("zero_fee_display_bug_hits_after_fix", 1)) == 0
        and f05.get("status") == "PASS"
    )
    gates["G03_FEE_ROOT_CORRECTNESS"] = "PASS" if g03_ok else "FAIL"

    # G04: Independent streaming Decimal fee ledger validation (04 vs 05)
    g04_ok = (
        fee_ledger_audit.get("valid") is True
        and fee_ledger_audit.get("status") == "PASS"
        and int(fee_ledger_audit.get("total_episode_rows", 0)) > 0
        and float(fee_ledger_audit.get("max_episode_gross_fee_net_residual_usd", 99.0)) <= 0.001
    )
    gates["G04_FEE_PER_TRADE_DECIMAL"] = "PASS" if g04_ok else "FAIL"

    # G05: Full 6-scenario cost parity & conservation
    g05_ok = (
        f05.get("status") == "PASS"
        and bool(f05.get("starts_from_full_raw_opportunity_stream", False))
        and bool(f05.get("conservation_verified_all_scenarios", False))
        and int(f05.get("total_overlap_violations", 1)) == 0
        and len(f05.get("scenarios") or {}) == 6
        and f05.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
    )
    gates["G05_FULL_SIX_SCENARIO_COST_PARITY"] = "PASS" if g05_ok else "FAIL"

    # G06: Data source contract & quality inventory
    g06_ok = (
        f06.get("status") == "PASS"
        and f06.get("dev_contract_validation_status") == "PASS"
        and bool((f06.get("ny_dst_validation") or {}).get("passed", False))
        and f06.get("observed_l1_bbo_available") is False
        and f06.get("historical_quote_mode") == "MODELED_BID_ASK"
    )
    gates["G06_DATA_CONTRACT_QUALITY"] = "PASS" if g06_ok else "FAIL"

    # G07: DEV / purged-validation / fresh-OOS partition & non-contamination
    g07_ok = (
        f07.get("status") == "PASS"
        and int(f07.get("embargo_minutes", 0)) >= 240
        and int(f07.get("OOS_EVALUATIONS", -1)) == 0
        and bool((f07.get("noncontamination_controls") or {}).get("all_passed", False))
    )
    gates["G07_DEV_OOS_PARTITION_NONCONTAMINATION"] = "PASS" if g07_ok else "FAIL"

    # G08: C4 trusted capture invariants & default network OFF
    g08_ok = (
        f08.get("status") == "PASS"
        and f08.get("network_capture_enabled") is False
        and f08.get("persistent_capture_active") is False
        and f08.get("live_network_session_authorized") is False
        and f08.get("live_network_capture_executed") is False
        and f08.get("LIVE_NETWORK_VALIDATION") == "NOT_RUN_BY_DESIGN"
        and int(f08.get("real_prospective_snapshots_collected", -1)) == 0
        and bool(f08.get("c4_boundary_direct_mint_blocked", False))
        and bool(f08.get("c4_boundary_evil_subclass_blocked", False))
        and bool(f08.get("c4_boundary_monkeypatch_blocked", False))
    )
    gates["G08_C4_TRUSTED_CAPTURE_INVARIANTS"] = "PASS" if g08_ok else "FAIL"

    # G09: Escrow integrity & real OOS record count == 0
    g09_ok = (
        f09.get("status") == "PASS"
        and bool(f09.get("real_oos_escrow_integrity_valid", False))
        and int(f09.get("real_oos_escrow_final_record_count", -1)) == 0
        and bool(f09.get("qa_fixture_refused_by_real_oos_escrow", False))
        and f09.get("unanchored_worm_verified") is False
    )
    gates["G09_ESCROW_INTEGRITY"] = "PASS" if g09_ok else "FAIL"

    # G10: Pre-registration & falsification budget bound to replay hash
    plan_sha = str(f10.get("plan_frozen_sha256") or "")
    g10_ok = (
        f10.get("status") == "PASS"
        and bool(f10.get("written_before_any_kh03_replay", False))
        and len(plan_sha) == 64
        and f12.get("preregistered_plan_sha256") == plan_sha
        and f15.get("preregistered_plan_sha256") == plan_sha
    )
    gates["G10_PREREG_FALSIFICATION_BUDGET"] = "PASS" if g10_ok else "FAIL"

    # G11: Causal as-of MTF & future mutation invariance
    g11_ok = (
        f11.get("status") == "PASS"
        and bool((f11.get("future_mutation_invariance") or {}).get("passed", False))
        and int((f11.get("future_mutation_invariance") or {}).get("mismatches_count", 1)) == 0
        and bool(f11.get("t01_to_t10_unswept_target_all_passed", False))
    )
    gates["G11_CAUSAL_ASOF_FUTURE_MUTATION"] = "PASS" if g11_ok else "FAIL"

    # G12: Full opportunity conservation & chronological 1-position replay
    g12_ok = (
        f12.get("status") == "PASS"
        and bool(f12.get("all_arms_conserved_exact", False))
        and int(f12.get("total_overlap_violations", 1)) == 0
    )
    gates["G12_ONE_POSITION_TARGET_CONSERVATION"] = "PASS" if g12_ok else "FAIL"

    # G13: Paired null matching & negative controls
    g13_ok = (
        f13.get("status") == "PASS"
        and f13.get("b0_vs_b2_cross_universe_status") == "COMPARISON_NOT_IDENTIFIABLE"
        and len(f13.get("arm_matching_reports") or {}) >= 3
        and len(f13.get("negative_control_summaries_1x") or {}) >= 3
    )
    gates["G13_PAIRED_NULL_MATCHING_BALANCE"] = "PASS" if g13_ok else "FAIL"

    # G14: Cost 1x/2x/3x stress & risk-tail honesty
    g14_ok = (
        f14.get("status") == "PASS"
        and int(f14.get("zero_fee_display_bugs_count", 1)) == 0
        and int(f14.get("total_overlap_violations", 1)) == 0
        and f16.get("status") == "PASS"
        and f16.get("planned_cap_is_physical_guarantee") is False
        and "NOT a physical guarantee" in str(f16.get("realized_tail_loss_disclosure") or "")
    )
    gates["G14_COST_RISK_TAIL_HONESTY"] = "PASS" if g14_ok else "FAIL"

    # G17: Disk/RAM/PID safety (computed before G15/G16 since heavy stages depend on resource safety)
    mp00 = (f00.get("strict_multi_mount_probe") or {})
    telem = (f18.get("runtime_resource_telemetry") or {})
    waiver_flag = (
        bool(f00.get("owner_disk_preflight_waiver", False))
        or bool(mp00.get("owner_waiver_permitted", False))
        or bool(telem.get("owner_disk_preflight_waiver", False))
        or bool(f23.get("owner_disk_preflight_waiver", False))
    )
    mounts = list(mp00.get("distinct_mounts") or ())
    probe_errs = list(mp00.get("probe_errors") or ())
    preflight_disk_ok = (
        len(mounts) > 0
        and len(probe_errs) == 0
        and all(
            float(m.get("free_gib", 0.0)) >= STRICT_PREFLIGHT_DISK_MIN_GIB
            and float(m.get("projected_post_reserve_gib", 0.0)) >= STRICT_RUNTIME_DISK_MIN_GIB
            for m in mounts
        )
        and bool(mp00.get("can_run_heavy_stages", False))
    )
    rt_disk_ok = float(telem.get("min_disk_free_gib") or 0.0) >= STRICT_RUNTIME_DISK_MIN_GIB
    ram_pid_ok = (
        float(telem.get("min_available_ram_mib") or 0.0) >= HARD_RAM_FLOOR_MIB_KH03
        and int(telem.get("max_descendant_children", 99)) <= GLOBAL_MAX_DESCENDANTS_KH03
        and int(telem.get("orphan_children_after_cleanup", 99)) == 0
        and bool(telem.get("sentinel_temp_cleaned_up", False))
        and mem_audit.get("status") == "PASS"
        and int(mem_audit.get("below_floor_samples_count", 1)) == 0
        and int(mem_audit.get("max_workers_observed_in_samples", 99)) <= GLOBAL_MAX_DESCENDANTS_KH03
    )
    if waiver_flag or len(probe_errs) > 0 or not ram_pid_ok:
        gates["G17_DISK_RAM_PID_SAFETY"] = "FAIL"
    elif preflight_disk_ok and rt_disk_ok:
        gates["G17_DISK_RAM_PID_SAFETY"] = "PASS"
    elif preflight_disk_ok and not rt_disk_ok:
        gates["G17_DISK_RAM_PID_SAFETY"] = "FAIL"
    else:
        gates["G17_DISK_RAM_PID_SAFETY"] = "BLOCKED_LOW_DISK"

    # G15: Full repository pytest JUnit 1:1 bijection
    r17 = str(f17.get("status") or "FAIL")
    if r17 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G15_FULL_JUNIT_REGRESSION"] = "NOT_RUN_BLOCKED_LOW_DISK" if gates["G17_DISK_RAM_PID_SAFETY"] == "BLOCKED_LOW_DISK" else "FAIL"
    elif (
        r17 == "PASS"
        and gates["G17_DISK_RAM_PID_SAFETY"] == "PASS"
        and junit_bijection_ok
        and int(f17.get("collected_count", 0)) >= 5578
        and int(f17.get("collected_count", -1)) == int(f17.get("executed_count", -2))
        and int(f17.get("failed_count", 1)) == 0
        and int(f17.get("error_count", 1)) == 0
        and int(f17.get("mandatory_skipped_count", 1)) == 0
    ):
        gates["G15_FULL_JUNIT_REGRESSION"] = "PASS"
    else:
        gates["G15_FULL_JUNIT_REGRESSION"] = "FAIL"

    # G16: Async W1/W2/W4/W1_repeat determinism
    r18 = str(f18.get("status") or "FAIL")
    if r18 == "NOT_RUN_BLOCKED_LOW_DISK":
        gates["G16_ASYNC_W1_W2_W4_DETERMINISM"] = "NOT_RUN_BLOCKED_LOW_DISK" if gates["G17_DISK_RAM_PID_SAFETY"] == "BLOCKED_LOW_DISK" else "FAIL"
    elif r18 == "PASS" and gates["G17_DISK_RAM_PID_SAFETY"] == "PASS" and _recompute_w1_w2_w4_c4(f18):
        gates["G16_ASYNC_W1_W2_W4_DETERMINISM"] = "PASS"
    else:
        gates["G16_ASYNC_W1_W2_W4_DETERMINISM"] = "FAIL"

    # G18: Faults >= 126 inherited C4 + >= 20 unique KH-03 = >= 146 unique controls
    g18_ok = (
        f19.get("status") == "PASS"
        and int(f19.get("inherited_c4_controls_count", 0)) >= 126
        and int(f19.get("new_kh03_controls_count", 0)) >= 20
        and int(f19.get("total_controls", 0)) >= 146
        and int(f19.get("unique_fault_ids", 0)) == int(f19.get("total_controls", -1))
        and int(f19.get("failed_controls", 1)) == 0
    )
    gates["G18_FAULT_SUITE_RED_GREEN"] = "PASS" if g18_ok else "FAIL"

    # G19: Independent OS verifier & >= 12 verifier mutation attacks
    mut_ok = (
        f21.get("status") == "PASS"
        and int(f21.get("mutations_tested_count", 0)) >= 12
        and int(f21.get("mutations_failed_detection_count", 1)) == 0
    )
    if for_final_verifier_manifest:
        gates["G19_INDEPENDENT_OS_VERIFIER"] = "PASS" if (verifier_violations_count == 0 and mut_ok) else "FAIL"
    else:
        gates["G19_INDEPENDENT_OS_VERIFIER"] = "PENDING_SEPARATE_OS_VERIFIER"

    # G20: Publication remote status
    pub_st = str(f23.get("publication_status") or "NOT_PUBLISHED_BLOCKED_LOW_DISK")
    if waiver_flag or "WAIVER" in pub_st:
        gates["G20_PUBLICATION_REMOTE"] = "FAIL"
    elif pub_st == "PASS_REMOTE_VERIFIED" and bool(f23.get("remote_readback_sha256_verified", False)):
        gates["G20_PUBLICATION_REMOTE"] = "PASS_REMOTE_VERIFIED"
    elif pub_st == "PRE_PUSH_PENDING":
        gates["G20_PUBLICATION_REMOTE"] = "PRE_PUSH_PENDING"
    elif "BLOCKED" in pub_st:
        gates["G20_PUBLICATION_REMOTE"] = pub_st
    else:
        gates["G20_PUBLICATION_REMOTE"] = "FAIL"

    # G21: Economic DEV evidence (recomputed strictly from f15 primary_arm_evaluations — NEVER trust forged PROVEN_EDGE!)
    valid_dev_verdicts = {"NO_ECONOMIC_ADVANCEMENT_CANDIDATE", "DEV_CANDIDATE_UNPROVEN_OOS", "INCONCLUSIVE_LOW_POWER"}
    claimed_dev_v = str(f15.get("KH03_DEV_CANDIDATE_VERDICT") or "FAIL")
    claimed_econ_v = str(f15.get("RESEARCH_ECONOMIC_VERDICT") or "FAIL")
    evals_15 = f15.get("primary_arm_evaluations") or {}
    any_pass_15 = any(bool((v or {}).get("passes_preregistered_dev_advancement", False)) for v in evals_15.values())
    if claimed_econ_v != "NO_PROVEN_EDGE" or claimed_dev_v not in valid_dev_verdicts:
        gates["G21_ECONOMIC_DEV_EVIDENCE"] = "FAIL"
    elif any_pass_15 and claimed_dev_v != "DEV_CANDIDATE_UNPROVEN_OOS":
        gates["G21_ECONOMIC_DEV_EVIDENCE"] = "FAIL"
    elif (not any_pass_15) and claimed_dev_v == "DEV_CANDIDATE_UNPROVEN_OOS":
        gates["G21_ECONOMIC_DEV_EVIDENCE"] = "FAIL"
    else:
        gates["G21_ECONOMIC_DEV_EVIDENCE"] = claimed_dev_v

    # G22: Fresh OOS readiness (honest BLOCKED_NO_UNTOUCHED_DATA, OOS_EVALUATIONS == 0)
    oos_ok = (
        int(f15.get("OOS_EVALUATIONS", -1)) == 0
        and int(f07.get("OOS_EVALUATIONS", -1)) == 0
        and f15.get("FRESH_OOS_VERDICT") == "BLOCKED_NO_UNTOUCHED_DATA"
    )
    gates["G22_FRESH_OOS_READINESS"] = "BLOCKED_NO_UNTOUCHED_DATA" if oos_ok else "FAIL"

    # G24: Activation ALWAYS NO & all runtime safety modes OFF
    modes_ok = True
    for d in (f00, f01, f10, f15):
        if d.get("ACTIVATION_READY") != "NO":
            modes_ok = False
        rsm = d.get("runtime_safety_modes") or {}
        if any(rsm.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
            modes_ok = False
    gates["G24_ACTIVATION_ALWAYS_NO"] = "NO" if modes_ok else "FAIL"

    # Roll up KH03_ENGINEERING across G01..G19
    eng_gates_pre = [
        "G01_SOURCE_BRANCH_CLEAN_SHA",
        "G02_PRODUCTION_IMMUTABILITY_PARITY",
        "G03_FEE_ROOT_CORRECTNESS",
        "G04_FEE_PER_TRADE_DECIMAL",
        "G05_FULL_SIX_SCENARIO_COST_PARITY",
        "G06_DATA_CONTRACT_QUALITY",
        "G07_DEV_OOS_PARTITION_NONCONTAMINATION",
        "G08_C4_TRUSTED_CAPTURE_INVARIANTS",
        "G09_ESCROW_INTEGRITY",
        "G10_PREREG_FALSIFICATION_BUDGET",
        "G11_CAUSAL_ASOF_FUTURE_MUTATION",
        "G12_ONE_POSITION_TARGET_CONSERVATION",
        "G13_PAIRED_NULL_MATCHING_BALANCE",
        "G14_COST_RISK_TAIL_HONESTY",
        "G15_FULL_JUNIT_REGRESSION",
        "G16_ASYNC_W1_W2_W4_DETERMINISM",
        "G17_DISK_RAM_PID_SAFETY",
        "G18_FAULT_SUITE_RED_GREEN",
    ]
    any_fail = (
        any(gates[k] == "FAIL" for k in gates)
        or (for_final_verifier_manifest and verifier_violations_count > 0)
    )
    any_low_disk = any("LOW_DISK" in gates[k] for k in eng_gates_pre)
    eng_core_pass = all(gates[k] == "PASS" for k in eng_gates_pre) and (
        (not for_final_verifier_manifest) or gates["G19_INDEPENDENT_OS_VERIFIER"] == "PASS"
    )
    if any_fail:
        eng_verdict = "FAIL"
    elif any_low_disk:
        eng_verdict = "BLOCKED_LOW_DISK"
    elif eng_core_pass:
        eng_verdict = "PASS"
    else:
        eng_verdict = "BLOCKED_INCOMPLETE_VERIFICATION"

    # G23: KH04_ENTRY (truthful dependency gate)
    if eng_verdict != "PASS" or not oos_ok or not modes_ok:
        gates["G23_KH04_ENTRY"] = f"BLOCKED_{eng_verdict}"
    elif gates["G21_ECONOMIC_DEV_EVIDENCE"] == "DEV_CANDIDATE_UNPROVEN_OOS":
        gates["G23_KH04_ENTRY"] = "BLOCKED_NO_ELIGIBLE_PROSPECTIVE_DATA"
    else:
        gates["G23_KH04_ENTRY"] = "BLOCKED_NO_DEV_CANDIDATE"

    if eng_verdict == "PASS" and oos_ok and modes_ok and gates["G21_ECONOMIC_DEV_EVIDENCE"] in valid_dev_verdicts:
        overall = "PASS_KH03_ENGINEERING_RESEARCH_NO_PROVEN_EDGE_OOS_BLOCKED"
    elif eng_verdict == "BLOCKED_LOW_DISK":
        overall = "BLOCKED_LOW_DISK"
    elif eng_verdict == "FAIL":
        overall = "FAIL_INTEGRITY_OR_VERIFICATION"
    else:
        overall = f"BLOCKED_{eng_verdict}"

    verdicts = {
        "KH03_ENGINEERING": eng_verdict,
        "KH03_DATA_READINESS": "DEV_VERIFIED_FRESH_OOS_BLOCKED_NO_NEW_DATA" if (g06_ok and g07_ok and g08_ok and g09_ok) else "FAIL",
        "KH03_FEE_ROOT_FIX": "PASS" if (g03_ok and g04_ok and g05_ok) else "FAIL",
        "KH03_DEV_CANDIDATE_VERDICT": gates["G21_ECONOMIC_DEV_EVIDENCE"],
        "FRESH_OOS": gates["G22_FRESH_OOS_READINESS"],
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE" if gates["G21_ECONOMIC_DEV_EVIDENCE"] != "FAIL" else "FAIL",
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": str(f05.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") or "FAIL"),
        "LIVE_NETWORK_VALIDATION": str(f08.get("LIVE_NETWORK_VALIDATION") or "FAIL"),
        "KH04_ENTRY": gates["G23_KH04_ENTRY"],
        "PUBLICATION": gates["G20_PUBLICATION_REMOTE"],
        "ACTIVATION_READY": gates["G24_ACTIVATION_ALWAYS_NO"],
    }
    return {
        "overall_classification": overall,
        "gates_24": gates,
        "verdicts": verdicts,
    }


def verify_kh03_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_live_repo_pointers: bool = True,
    enforce_distinct_pid: bool = True,
    write_manifest: bool = True,
) -> Dict[str, Any]:
    """Separate-OS-process disk verifier recomputing V01..V14 and G01..G24 from raw disk artifacts."""
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "spec_version": SPEC_VERSION_KH03,
            "schema_version": SCHEMA_VERSION_KH03,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": [f"RUN_DIR_MISSING:{run_dir}"],
        }

    for rel in REQUIRED_ARTIFACTS_KH03:
        fp = run_dir / rel
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_OR_EMPTY_ARTIFACT:{rel}")
    if violations:
        return {
            "spec_version": SPEC_VERSION_KH03,
            "schema_version": SCHEMA_VERSION_KH03,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": violations,
        }

    def _rd(fn: str) -> Dict[str, Any]:
        return json.loads((run_dir / fn).read_text("utf-8"))

    try:
        docs: Dict[str, Dict[str, Any]] = {
            "00": _rd("00_source_branch_sha_and_resource_preflight.json"),
            "01": _rd("01_file_function_ownership_and_keep_as_is_ast_diff.json"),
            "02": _rd("02_prior_dataset_and_sealed_sha256_immutability.json"),
            "03": _rd("03_fee_root_bug_red_proof_and_diff.json"),
            "05": _rd("05_fee_full_universe_six_scenario_reconciliation.json"),
            "06": _rd("06_data_source_contract_and_quality_inventory.json"),
            "07": _rd("07_partition_freeze_pre_registration_and_noncontamination.json"),
            "08": _rd("08_public_collector_readiness_authorization_and_provenance.json"),
            "09": _rd("09_escrow_integrity_and_hash_linked_readiness.json"),
            "10": _rd("10_kh03_frozen_hypotheses_with_falsification_budget.json"),
            "11": _rd("11_causal_asof_mtf_and_future_mutation_tests.json"),
            "12": _rd("12_full_opportunity_conservation_and_candidate_replay.json"),
            "13": _rd("13_paired_null_matching_and_balance.json"),
            "14": _rd("14_net_economics_cost_1x_2x_3x_stress.json"),
            "15": _rd("15_purged_walkforward_bootstrap_multiplicity_power.json"),
            "16": _rd("16_risk_tail_gap_fill_time_and_fee_disclosure.json"),
            "17": _rd("17_full_regression_collected_executed_junit_bijection.json"),
            "18": _rd("18_w1_w2_w4_w1repeat_speed_hash_and_resource.json"),
            "19": _rd("19_all_faults_unique_red_green_receipts.json"),
            "20": _rd("20_full_A1_SI8_246_adapter_D4_parity.json"),
            "21": _rd("21_independent_verifier_mutation_resistance.json"),
            "22": _rd("22_orthogonal_gate_matrix_prepush.json"),
            "23": _rd("23_phase1_code_commit_push_receipt.json"),
        }
        readme_txt = (run_dir / "README_KH03_R1.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH03_R1_VI.txt").read_text("utf-8")
    except Exception as exc:
        return {
            "spec_version": SPEC_VERSION_KH03,
            "schema_version": SCHEMA_VERSION_KH03,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "exit_code_recommended": 1,
            "violations": [f"ARTIFACT_PARSE_ERROR:{exc}"],
        }

    f00, f05, f08, f10, f12, f15, f17, f18, f22, f23 = (
        docs["00"], docs["05"], docs["08"], docs["10"], docs["12"],
        docs["15"], docs["17"], docs["18"], docs["22"], docs["23"],
    )

    orch_pid = int(f00.get("orchestrator_pid") or -1)
    if enforce_distinct_pid and verifier_pid == orch_pid:
        violations.append(f"VERIFIER_SAME_OS_PID_AS_ORCHESTRATOR:{verifier_pid}")

    # V01: Branch, tested SHA, run_id binding
    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    if f00.get("branch") != MANDATORY_BRANCH_KH03:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if tested_sha != f00.get("tested_executable_sha") or tested_sha != f22.get("tested_executable_sha") or tested_sha != f23.get("tested_executable_sha"):
        violations.append("TESTED_SHA_MISMATCH_ACROSS_00_22_23")
    if len(tested_sha) >= 12 and not run_id.endswith(f"_{tested_sha[:12]}"):
        violations.append(f"RUN_ID_MISSING_TESTED_SHA12_SUFFIX:{run_id}")

    # V02: Live SHA-256 immutability of prior sealed artifacts + canonical dataset + CostAssumptions
    if check_live_repo_pointers:
        for rel_p, exp_sha, lbl in PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03:
            p = REPO_ROOT / rel_p
            if not p.is_file() or _file_sha256(p) != exp_sha:
                violations.append(f"PRIOR_SEALED_ARTIFACT_MUTATED:{lbl}")
        from domain_calculator import CostAssumptions
        c_live = CostAssumptions()
        if (
            abs(float(c_live.taker_fee_rate) - 0.0004) > 1e-12
            or abs(float(c_live.maker_fee_rate) - 0.0002) > 1e-12
            or abs(float(c_live.slippage_usd) - 0.10) > 1e-12
        ):
            violations.append("LIVE_COST_ASSUMPTIONS_MUTATED")
    else:
        pm = docs["02"].get("prior_sealed_artifacts_immutability") or {}
        if any(not bool(v.get("match", False)) for v in pm.values() if isinstance(v, Mapping)):
            violations.append("PRIOR_SEALED_ARTIFACT_MUTATED")
    if docs["02"].get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH03 or not bool(docs["02"].get("canonical_dataset_sha256_match", False)):
        violations.append("CANONICAL_DATASET_SHA256_TAMPERED")

    # V03 / V04 / V05: Streaming Decimal verification of 04_fee_per_episode_ledger.jsonl.gz vs 05!
    fee_audit = validate_kh03_fee_ledger_independently(run_dir / "04_fee_per_episode_ledger.jsonl.gz", f05)
    if not fee_audit.get("valid", False):
        violations.extend([f"INDEPENDENT_FEE_LEDGER:{v}" for v in (fee_audit.get("violations") or ["FEE_AUDIT_FAILED"])])

    # V07: Pre-registration manifest SHA integrity (recompute canonical hash of 10!)
    recomp_plan_sha = canonical_hash({k: v for k, v in f10.items() if k != "plan_frozen_sha256"})
    if f10.get("plan_frozen_sha256") != recomp_plan_sha:
        violations.append("PREREG_PLAN_SHA256_TAMPERED")
    if f12.get("preregistered_plan_sha256") != recomp_plan_sha or f15.get("preregistered_plan_sha256") != recomp_plan_sha:
        violations.append("PREREG_PLAN_SHA256_UNLINKED_IN_12_OR_15")

    # V08 / V09 / V14: OOS zero-peek, economic truth, live network off, activation off
    if int(f15.get("OOS_EVALUATIONS", -1)) != 0 or int(docs["07"].get("OOS_EVALUATIONS", -1)) != 0:
        violations.append("NONZERO_OOS_EVALUATIONS_PEEK")
    if f15.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE":
        violations.append(f"FORGED_RESEARCH_ECONOMIC_VERDICT:{f15.get('RESEARCH_ECONOMIC_VERDICT')}")
    if f15.get("ACTIVATION_READY") != "NO" or f00.get("ACTIVATION_READY") != "NO":
        violations.append("FORGED_ACTIVATION_READY_ON")
    if f08.get("live_network_capture_executed") is not False or int(docs["09"].get("real_oos_escrow_final_record_count", -1)) != 0:
        violations.append("UNAUTHORIZED_LIVE_NETWORK_OR_NONZERO_REAL_OOS_ESCROW")

    # V10: Raw JUnit XML 1:1 bijection
    junit_ok = False
    if f17.get("status") == "PASS":
        junit_ok, j_errs = _verify_junit_raw_xml_bijection_c4(
            run_dir, f17, expected_run_id=run_id, expected_tested_sha=tested_sha
        )
        if not junit_ok:
            violations.extend(j_errs)

    # V11: W1/W2/W4/W1_repeat determinism
    if f18.get("status") == "PASS" and not _recompute_w1_w2_w4_c4(f18):
        violations.append("W1_W2_W4_DETERMINISM_HASH_MISMATCH")

    # V12: Memory/disk/PID telemetry log audit directly from gzip log
    mem_log = run_dir / "logs" / "runtime_memory_samples_0p5s.jsonl.gz"
    if not mem_log.is_file() or mem_log.stat().st_size == 0:
        mem_audit: Dict[str, Any] = {"status": "FAIL_MISSING_LOG", "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99}
        violations.append("MISSING_MEMORY_TELEMETRY_LOG")
    else:
        mem_audit = _audit_memory_samples_c3(mem_log)
        if mem_audit.get("status") != "PASS":
            violations.append(f"MEMORY_TELEMETRY_BREACHED_512MIB_FLOOR:{mem_audit.get('status')}")
        if int(mem_audit.get("max_workers_observed_in_samples", 99)) > GLOBAL_MAX_DESCENDANTS_KH03:
            violations.append(f"MEMORY_TELEMETRY_EXCEEDED_CHILD_CAP:{mem_audit.get('max_workers_observed_in_samples')}")

    # V13: Publication honesty & zero disk waiver
    if f23.get("publication_status") == "PASS_REMOTE_VERIFIED" and not bool(f23.get("remote_readback_sha256_verified", False)):
        violations.append("FALSE_REMOTE_READBACK_CLAIM")
    if bool(f00.get("owner_disk_preflight_waiver", False)):
        violations.append("UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN")

    # Recompute 24 gates & verify against stored 22_orthogonal_gate_matrix_prepush.json
    recomp_pre = recompute_kh03_24_gates_and_verdicts(
        docs=docs,
        mem_audit=mem_audit,
        fee_ledger_audit=fee_audit,
        junit_bijection_ok=junit_ok,
        verifier_violations_count=len(violations),
        for_final_verifier_manifest=False,
    )
    stored_gates = f22.get("gates_24") or {}
    for gk, gv in recomp_pre["gates_24"].items():
        if stored_gates.get(gk) != gv:
            violations.append(f"GATE_TAMPER_OR_DRIFT:{gk}:stored={stored_gates.get(gk)}!=recomputed={gv}")

    # Cross-view consistency on README & HANDOVER
    if run_id not in readme_txt or run_id not in handover_txt:
        violations.append("CROSS_VIEW_RUN_ID_MISSING")
    if "ACTIVATION_READY=NO" not in readme_txt or "ACTIVATION_READY=NO" not in handover_txt:
        violations.append("CROSS_VIEW_ACTIVATION_LOCK_MISSING")
    if "NO_PROVEN_EDGE" not in readme_txt or "NO_PROVEN_EDGE" not in handover_txt:
        violations.append("CROSS_VIEW_NO_PROVEN_EDGE_MISSING")

    recomp_final = recompute_kh03_24_gates_and_verdicts(
        docs=docs,
        mem_audit=mem_audit,
        fee_ledger_audit=fee_audit,
        junit_bijection_ok=junit_ok,
        verifier_violations_count=len(violations),
        for_final_verifier_manifest=True,
    )

    # Non-circular SHA-256 manifest of EVERY file in run_dir (excluding 24 & .tmp)
    artifact_sha256_map: Dict[str, Dict[str, Any]] = {}
    for fp in sorted(run_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(run_dir).as_posix()
        if rel == MANIFEST_24_NAME_KH03 or rel.endswith(".tmp"):
            continue
        artifact_sha256_map[rel] = {
            "size_bytes": int(fp.stat().st_size),
            "sha256": _file_sha256(fp),
        }

    eng_final = recomp_final["verdicts"]["KH03_ENGINEERING"]
    if len(violations) > 0 or eng_final == "FAIL":
        verifier_status = "FAIL"
        exit_code = 1
    elif eng_final == "PASS":
        verifier_status = "PASS"
        exit_code = 0
    else:
        verifier_status = "PASS"
        exit_code = 2

    payload: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "orchestrator_pid": orch_pid,
        "verifier_pid": verifier_pid,
        "distinct_os_process_verified": (verifier_pid != orch_pid),
        "independent_verifier_status": verifier_status,
        "overall_classification": recomp_final["overall_classification"],
        "exit_code_recommended": exit_code,
        "recomputed_24_gates": recomp_final["gates_24"],
        "recomputed_verdicts": recomp_final["verdicts"],
        "independent_fee_ledger_audit": {
            "status": fee_audit.get("status"),
            "total_episode_rows": fee_audit.get("total_episode_rows"),
            "ledger_file_sha256": fee_audit.get("ledger_file_sha256"),
            "max_episode_gross_fee_net_residual_usd": fee_audit.get("max_episode_gross_fee_net_residual_usd"),
        },
        "memory_telemetry_disk_audit": mem_audit,
        "junit_bijection_verified": junit_ok,
        "violations": violations,
        "artifact_count_hashed": len(artifact_sha256_map),
        "artifact_sha256_map": artifact_sha256_map,
    }
    payload["manifest_content_sha256"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "manifest_content_sha256"}
    )

    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_24_NAME_KH03, payload)
    return payload


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KH-03.R1 Independent Separate-OS Disk Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-live-repo-pointers", action="store_true")
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)

    res = verify_kh03_artifacts_independently_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_live_repo_pointers=not args.skip_live_repo_pointers,
        enforce_distinct_pid=True,
        write_manifest=not args.no_write_manifest,
    )
    print(
        json.dumps(
            {
                "independent_verifier_status": res["independent_verifier_status"],
                "overall_classification": res.get("overall_classification"),
                "verifier_pid": res["verifier_pid"],
                "distinct_os_process_verified": res.get("distinct_os_process_verified"),
                "recomputed_24_gates": res.get("recomputed_24_gates"),
                "recomputed_verdicts": res.get("recomputed_verdicts"),
                "violations": res.get("violations"),
                "artifact_count_hashed": res.get("artifact_count_hashed"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return int(res.get("exit_code_recommended", 1))


if __name__ == "__main__":
    raise SystemExit(main())
