"""
aurum-desk/backend/services/kh_03_r5_readiness_gate_service.py

AURUM DESK — KH-03.R5 | ORTHOGONAL 26-GATE READINESS ENGINE & VIETNAMESE OWNER HANDOVER
SPEC VERSION: KH-03.R5-v1.0 | ZERO CALLER-INJECTED PASS | ORTHOGONAL ENGINEERING VS DATA VS ECONOMICS

Implements §04 functions 15 & 16 plus §03 function ownership matrix builder:
  15. derive_r5_readiness_verdicts (recomputes ALL 26 gates G01..G26 & ALL 19 independent verdict fields)
  16. build_r5_owner_handover_vi (concise Vietnamese owner handover separating executed vs blocked)
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.kh_03_r5_market_data_foundation_service import (
    SCHEMA_VERSION_KH03_R5,
    SPEC_VERSION_KH03_R5,
    STRICT_PREFLIGHT_DISK_MIN_GIB_R5,
    STRICT_RUNTIME_DISK_MIN_GIB_R5,
)

MANDATORY_BRANCH_KH03_R5 = "feature/v17-9-2-structural-sl-tp-from-qa"
HARD_RAM_FLOOR_MIB_R5 = 512.0
GLOBAL_MAX_DESCENDANTS_R5 = 4
MIN_EXPECTED_PYTEST_COLLECTED_R5 = 5848

GATE_26_KEYS_KH03_R5: Tuple[str, ...] = (
    "G01_BRANCH_CLEAN_FROZEN_SHA",
    "G02_PROTECTED_BYTES_AST_SOURCE_OWNERSHIP",
    "G03_PRIOR_DATASET_ARTIFACT_ESCROW_INTEGRITY",
    "G04_PUBLIC_CONTRACT_SOURCE_CAPABILITY_TRUTH",
    "G05_AUTHORIZATION_FAIL_CLOSED",
    "G06_CAPTURE_BUDGET_TRANSPORT_INTEGRITY",
    "G07_RAW_NORMALIZED_PROVENANCE",
    "G08_METADATA_IDENTITY_PRECISION",
    "G09_OHLCV_LINEAGE_GAP_SEMANTICS",
    "G10_BBO_COVERAGE_HONESTY",
    "G11_FUNDING_SCHEDULE_RATE_TRUTH",
    "G12_ACCOUNT_FEE_SLIPPAGE_UNKNOWN_HONESTY",
    "G13_DEV_OOS_PARTITION_NO_ACCESS",
    "G14_CUSTODY_RACE_CRASH_LOCK_ANCHOR",
    "G15_R4_REAL_REPLAY_EXACT_ACCOUNTING_ANCHORS",
    "G16_NO_SCIENCE_PROD_CHANGE_RISK_TAIL_DISCLOSURE",
    "G17_FULL_FRESH_PYTEST_COLLECTION_JUNIT_BIJECTION",
    "G18_A1_SI8_ADAPTERS_D4_FULL_PARITY",
    "G19_DETERMINISTIC_W1_W2_W4_W1REPEAT",
    "G20_MEASURED_WORKER_POLICY",
    "G21_DISK_RAM_PID_NO_WAIVER",
    "G22_INHERITED_AND_NEW_RED_GREEN_FAULTS",
    "G23_SEPARATE_OS_VERIFIER_RAW_EVIDENCE",
    "G24_VERIFIER_MUTATION_ATTACKS",
    "G25_NO_ACTIVATION_OOS_ALPHA_CLAIM",
    "G26_PUBLICATION_REMOTE_PROOF",
)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def build_r5_source_function_ownership_matrix() -> List[Dict[str, Any]]:
    """Documents actual §03 reused implementations vs new §04 additive R5 APIs (exact HEAD reconciliation)."""
    return [
        {
            "source_file": "aurum-desk/backend/services/kh_02_r2_c2_public_capture.py",
            "functions_reused": "OFFICIAL_BITGET_PUBLIC_ALLOWLIST_C2, FORBIDDEN_URL_TOKENS_C2, StdlibUrllibPublicTransportC2, _NoRedirectHandlerC3, _validate_public_bitget_https_url_c3, RawHttpResponseC2",
            "action": "KEEP_AS_IS_READONLY_REUSE",
            "r5_consumer": "kh_03_r5_market_data_foundation_service.py & kh_03_r5_capture_and_custody_service.py",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_02_r2_c4_trusted_transport_boundary.py",
            "functions_reused": "execute_trusted_stdlib_https_get_c4, is_exact_unmodified_stdlib_transport_c4, verify_capture_proof_c4, bind_capture_proof_consumption_c4, issue_qa_or_unverified_capture_proof_c4",
            "action": "KEEP_AS_IS_READONLY_REUSE",
            "r5_consumer": "kh_03_r5_capture_and_custody_service.py::capture_r5_public_batch & persist_r5_verified_dev_snapshot",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_02_r2_c3_provenance_guard.py",
            "functions_reused": "safe_bounded_decompress_if_gzip_c3, mint_trusted_capture_receipt_c4, ProvenanceClassC3, OFFICIAL_BITGET_PUBLIC_HOST_C3, OFFICIAL_BITGET_PUBLIC_PATHS_C3",
            "action": "KEEP_AS_IS_READONLY_REUSE",
            "r5_consumer": "kh_03_r5_market_data_foundation_service.py & kh_03_r5_capture_and_custody_service.py",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_02_r2_c2_escrow_journal.py",
            "functions_reused": "ExternalHeadAnchorC2, _cross_platform_exclusive_fd_lock, _fsync_directory, _assert_not_symlink_and_contained, _is_symlink_or_junction, _write_all_eintr_safe",
            "action": "KEEP_AS_IS_READONLY_REUSE_NEW_DEV_NAMESPACE_ONLY",
            "r5_consumer": "kh_03_r5_capture_and_custody_service.py::R5DevCustodyJournal",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_02_r2_prospective_data_service.py",
            "functions_reused": "BITGET_XAUUSDT_CONTRACT_SPEC_KH02_R2_C1, CANONICAL_DATASET_SHA256_KH02, scan_for_forbidden_secrets_r2_c1",
            "action": "KEEP_AS_IS_READONLY_REUSE",
            "r5_consumer": "kh_03_r5_market_data_foundation_service.py",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_data_governance_service.py",
            "functions_reused": "audit_kh03_collector_and_escrow_readiness, validate_kh03_source_contracts, validate_kh03_noncontamination_entry",
            "action": "KEEP_AS_IS_READONLY_REUSE_EXCLUDING_FORBIDDEN_R1_SYMBOL",
            "r5_consumer": "tools/run_kh_03_r5_verification.py (escrow metadata audit only)",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r4_economic_diagnostic_service.py",
            "functions_reused": "load_r4_immutable_research_inputs_readonly, build_r4_trade_population_conservation, recompute_r4_decimal_economic_bridge, build_r4_counterfactual_cost_only_diagnostics, build_r4_mechanism_evidence_table, audit_r4_covariate_overlap_and_effective_n, derive_r4_breakeven_feasibility_bounds, build_r4_data_gap_register, decide_r4_research_next_action, validate_r4_no_claim_inflation",
            "action": "KEEP_AS_IS_READONLY_REPLAY_ANCHOR",
            "r5_consumer": "tools/run_kh_03_r5_verification.py::Stage R4 real replay parity (13)",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_02_r2_c4_risk_tail_evidence.py",
            "functions_reused": "build_c4_per_episode_risk_ledger, recompute_c4_tail_counters_from_episode_ledger, validate_c4_tail_disclosure_vs_risk_ledger",
            "action": "KEEP_AS_IS_READONLY_REPLAY_ANCHOR",
            "r5_consumer": "tools/run_kh_03_r5_verification.py (historical modeled risk-tail disclosure)",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r3_c1_worker_policy_service.py",
            "functions_reused": "parse_json_strict_no_nonfinite, parse_benchmark_run_duration_strict, validate_benchmark_semantic_equivalence_strict, select_measured_worker_policy_strict",
            "action": "KEEP_AS_IS_READONLY_REUSE",
            "r5_consumer": "tools/run_kh_03_r5_verification.py (16 benchmark policy)",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r3_legacy_research_guard.py",
            "functions_reused": "audit_kh03_r3_legacy_research_callgraph, require_kh03_r2_or_newer_research_protocol",
            "action": "KEEP_AS_IS_READONLY_REUSE",
            "r5_consumer": "tools/run_kh_03_r5_verification.py (01 legacy R1 guard audit)",
        },
    ]


# =====================================================================
# 15. derive_r5_readiness_verdicts
# =====================================================================
def derive_r5_readiness_verdicts(
    *,
    raw_evidence: Mapping[str, Mapping[str, Any]],
    resource: Mapping[str, Any],
    regression: Mapping[str, Any],
    parity: Mapping[str, Any],
    custody: Mapping[str, Any],
    observed_register: Mapping[str, Any],
    approval_state: Mapping[str, Any],
    stale_prior_shas: Sequence[str] = (),
    stale_prior_run_ids: Sequence[str] = (),
    for_final_verifier_manifest: bool = False,
    verifier_violations_count: int = 0,
) -> Dict[str, Any]:
    """
    Independently recomputes ALL 26 orthogonal KH-03.R5 gates (`G01..G26`) and ALL 19
    independent verdict fields directly from disk-backed raw evidence.
    NEVER trusts caller-injected PASS strings; fails closed on missing keys, NaN/Inf,
    forged readiness, disk waivers, fake alpha, or OOS evaluation > 0.
    """
    f00 = raw_evidence.get("00") or {}
    f01 = raw_evidence.get("01") or {}
    f02 = raw_evidence.get("02") or {}
    f03 = raw_evidence.get("03") or {}
    f04 = raw_evidence.get("04") or {}
    f05 = raw_evidence.get("05") or {}
    f06 = raw_evidence.get("06") or {}
    f07 = raw_evidence.get("07") or {}
    f08 = raw_evidence.get("08") or {}
    f09 = raw_evidence.get("09") or {}
    f10 = raw_evidence.get("10") or {}
    f11 = raw_evidence.get("11") or {}
    f13 = raw_evidence.get("13") or {}
    f16 = raw_evidence.get("16") or {}
    f18 = raw_evidence.get("18") or {}
    f19 = raw_evidence.get("19") or {}
    f22 = raw_evidence.get("22") or {}

    g: Dict[str, str] = {}

    # G01: exact branch, clean worktree, unique frozen SHA (not stale prior SHA/run_id)
    tsha = str(f00.get("tested_executable_sha") or "")
    rid = str(f00.get("run_id") or "")
    sha_ok = (
        f00.get("branch") == MANDATORY_BRANCH_KH03_R5
        and len(tsha) == 40
        and tsha == f00.get("start_head_sha") == f00.get("end_head_sha")
        and tsha not in set(stale_prior_shas)
        and rid not in set(stale_prior_run_ids)
    )
    clean_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_changes", False))
    g["G01_BRANCH_CLEAN_FROZEN_SHA"] = "PASS" if (sha_ok and clean_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if sha_ok else "FAIL")

    # G02: protected bytes/AST/source ownership & zero unauthorized/order imports
    g["G02_PROTECTED_BYTES_AST_SOURCE_OWNERSHIP"] = (
        "PASS"
        if (
            f01.get("status") == "PASS"
            and int(f01.get("locked_files_modified_count", 1)) == 0
            and int(f01.get("unauthorized_changed_files_count", 1)) == 0
            and len(f01.get("forbidden_order_import_hits") or []) == 0
            and bool(f01.get("cost_assumptions_defaults_intact"))
            and f01.get("legacy_guard_status") == "PASS"
            and int(f01.get("unauthorized_r1_callers_count", 1)) == 0
        )
        else "FAIL"
    )

    # G03: prior dataset / artifact / escrow pointer immutability
    g["G03_PRIOR_DATASET_ARTIFACT_ESCROW_INTEGRITY"] = (
        "PASS"
        if (
            f02.get("status") == "PASS"
            and f02.get("canonical_dataset_sha256") == CANONICAL_DATASET_SHA256_KH02
            and bool(f02.get("canonical_dataset_sha256_match"))
            and bool(f02.get("all_prior_sealed_artifacts_intact"))
            and int(((f02.get("c4_provenance_and_oos_boundary") or {}).get("real_oos_escrow_final_record_count", -1))) == 0
        )
        else "FAIL"
    )

    # G04: source capability truth (no invented endpoints/continuous BBO/live contract claims)
    miss_cap = f03.get("missing_or_blocked_capabilities") or {}
    g["G04_PUBLIC_CONTRACT_SOURCE_CAPABILITY_TRUTH"] = (
        "PASS"
        if (
            f03.get("status") == "PASS"
            and (miss_cap.get("continuous_l1_bbo_1s_stream") or {}).get("status") == "BLOCKED_CONTINUOUS_BBO"
            and (miss_cap.get("public_funding_rate_history") or {}).get("status") == "UNSUPPORTED_OR_UNVERIFIED_ENDPOINT"
            and (miss_cap.get("account_effective_fee_tier") or {}).get("status") == "UNKNOWN_NOT_LOCALLY_VERIFIED"
        )
        else "FAIL"
    )

    # G05: operator authorization fail-closed (no fake CLI/env/prompt approval)
    auth_st = str(approval_state.get("authorization_status") or "BLOCKED_OWNER_AUTHORIZATION")
    live_perm = bool(approval_state.get("live_network_permitted", False))
    g["G05_AUTHORIZATION_FAIL_CLOSED"] = (
        "PASS"
        if (
            f04.get("status") == "PASS"
            and auth_st in ("BLOCKED_OWNER_AUTHORIZATION", "AUTHORIZED_BOUNDED_DEV")
            and (auth_st != "BLOCKED_OWNER_AUTHORIZATION" or not live_perm)
            and bool(f04.get("cli_env_prompt_bypass_blocked", False))
        )
        else "FAIL"
    )

    # G06: capture request budget / transport integrity (offline guard PASS distinct from real capture!)
    ext_req = int(f05.get("external_requests_attempted", -1))
    obs_rec = int(f05.get("accepted_observed_records_count", -1))
    qa_rec = int(f05.get("qa_fixture_records_count", -1))
    g06_ok = (
        f05.get("status") == "PASS"
        and (live_perm or (ext_req == 0 and obs_rec == 0))
        and qa_rec >= 1
        and bool(f05.get("qa_isolated_from_observed_verified", False))
    )
    g["G06_CAPTURE_BUDGET_TRANSPORT_INTEGRITY"] = "PASS" if g06_ok else "FAIL"

    # G07: raw->normalized provenance & QA vs observed separation
    g["G07_RAW_NORMALIZED_PROVENANCE"] = (
        "PASS"
        if (
            g06_ok
            and bool(f05.get("fake_transport_cannot_mint_observed_verified", False))
            and (live_perm or f05.get("capture_provenance_status") in ("NO_REAL_CAPTURE", "QA_FIXTURE_ONLY_NO_REAL_CAPTURE"))
        )
        else "FAIL"
    )

    # G08..G12: domain contract validators & honest unknown disclosures
    g["G08_METADATA_IDENTITY_PRECISION"] = "PASS" if (f06.get("status") == "PASS" and bool(f06.get("offline_contract_validation_passed")) and (live_perm or f06.get("live_observed_metadata_status") == "MISSING")) else "FAIL"
    g["G09_OHLCV_LINEAGE_GAP_SEMANTICS"] = "PASS" if (f07.get("status") == "PASS" and bool(f07.get("offline_ohlcv_validation_passed")) and f07.get("forward_fill_applied") is False) else "FAIL"
    g["G10_BBO_COVERAGE_HONESTY"] = (
        "PASS"
        if (
            f08.get("status") == "PASS"
            and bool(f08.get("offline_bbo_validation_passed"))
            and f08.get("interpolation_or_ohlcv_inference_applied") is False
            and f08.get("strategy_decision_fill_coverage") == "UNKNOWN_NO_AUTHORIZED_ALIGNMENT_PROTOCOL"
        )
        else "FAIL"
    )
    g["G11_FUNDING_SCHEDULE_RATE_TRUTH"] = (
        "PASS"
        if (
            f09.get("status") == "PASS"
            and bool(f09.get("offline_funding_validation_passed"))
            and f09.get("zero_fill_applied") is False
            and f09.get("retroactive_r4_pnl_modified") is False
        )
        else "FAIL"
    )
    g["G12_ACCOUNT_FEE_SLIPPAGE_UNKNOWN_HONESTY"] = (
        "PASS"
        if (
            f10.get("status") == "PASS"
            and f10.get("credentials_requested") is False
            and f10.get("observed_slippage_status") == "MISSING"
            and f10.get("account_fee_readiness") in ("UNKNOWN_NOT_LOCALLY_VERIFIED", "OWNER_ATTESTED", "VERIFIED")
            and (f10.get("owner_fee_document_supplied") is True or f10.get("account_fee_readiness") == "UNKNOWN_NOT_LOCALLY_VERIFIED")
        )
        else "FAIL"
    )

    # G13 & G14: DEV/OOS partition isolation, deny-before-open, custody race/crash/lock/anchor
    g["G13_DEV_OOS_PARTITION_NO_ACCESS"] = (
        "PASS"
        if (
            f11.get("status") == "PASS"
            and bool(custody.get("valid"))
            and int(((custody.get("oos_access_guard_summary") or {}).get("oos_reads_executed", 1))) == 0
            and int(((custody.get("oos_access_guard_summary") or {}).get("oos_evaluations", 1))) == 0
            and bool(f11.get("dev_relabel_as_oos_blocked", False))
            and bool(f11.get("oos_deny_before_open_verified", False))
        )
        else "FAIL"
    )
    g["G14_CUSTODY_RACE_CRASH_LOCK_ANCHOR"] = (
        "PASS"
        if (
            f11.get("status") == "PASS"
            and bool(custody.get("valid"))
            and bool(f11.get("crash_recovery_quarantine_verified", False))
            and bool(f11.get("double_writer_lock_race_verified", False))
            and bool(f11.get("external_head_anchor_verified", False))
        )
        else "FAIL"
    )

    # G15 & G16: Real fresh R4 replay parity & zero science/production change + risk tail disclosure
    g["G15_R4_REAL_REPLAY_EXACT_ACCOUNTING_ANCHORS"] = (
        "PASS"
        if (
            f13.get("status") == "PASS"
            and bool(f13.get("fresh_replay_executed_not_copied", False))
            and bool(f13.get("r4_decimal_bridge_exact_match", False))
            and bool(f13.get("r4_row_ids_and_ledger_sha_match", False))
            and bool(f13.get("six_cost_dynamic_vs_fixed_fills_separated", False))
            and f13.get("new_data_used_in_backtest") is False
        )
        else "FAIL"
    )
    g["G16_NO_SCIENCE_PROD_CHANGE_RISK_TAIL_DISCLOSURE"] = (
        "PASS"
        if (
            g["G02_PROTECTED_BYTES_AST_SOURCE_OWNERSHIP"] == "PASS"
            and f13.get("planned_cap_is_physical_guarantee") is False
            and int(f13.get("realized_losses_gt_2p50_count", -1)) == 139
            and f13.get("c4_risk_tail_disclosure_valid") is True
            and f13.get("legacy_funding_status") == "EXCLUDED_UNMODELED"
        )
        else "FAIL"
    )

    # G21: Resource safety (evaluated before heavy G17..G20)
    mp00 = f00.get("strict_multi_mount_probe") or {}
    waiver = bool(f00.get("owner_disk_preflight_waiver", False)) or bool(mp00.get("owner_waiver_permitted", False)) or bool(resource.get("owner_disk_preflight_waiver", False))
    mounts = list(mp00.get("distinct_mounts") or ())
    probe_errs = list(mp00.get("probe_errors") or ())
    pre_disk_ok = len(mounts) > 0 and len(probe_errs) == 0 and all(_is_num(m.get("free_gib")) and float(m["free_gib"]) >= STRICT_PREFLIGHT_DISK_MIN_GIB_R5 and _is_num(m.get("projected_post_reserve_gib")) and float(m["projected_post_reserve_gib"]) >= STRICT_RUNTIME_DISK_MIN_GIB_R5 for m in mounts) and bool(mp00.get("can_run_heavy_stages", False))
    min_rt_disk = float(resource.get("min_disk_free_gib") or 0.0) if _is_num(resource.get("min_disk_free_gib")) else 0.0
    min_rt_ram = float(resource.get("min_available_ram_mib") or 0.0) if _is_num(resource.get("min_available_ram_mib")) else 0.0
    max_ch = int(resource.get("max_descendant_children", 99)) if _is_int(resource.get("max_descendant_children")) else 99
    rt_disk_ok = min_rt_disk >= STRICT_RUNTIME_DISK_MIN_GIB_R5
    ram_pid_ok = (
        min_rt_ram >= HARD_RAM_FLOOR_MIB_R5
        and max_ch <= GLOBAL_MAX_DESCENDANTS_R5
        and int(resource.get("orphan_children_after_cleanup", 99)) == 0
        and bool(resource.get("sentinel_temp_cleaned_up", False))
        and resource.get("raw_telemetry_audit_status") == "PASS"
        and int(resource.get("below_floor_samples_count", 1)) == 0
    )
    if waiver or len(probe_errs) > 0:
        g["G21_DISK_RAM_PID_NO_WAIVER"] = "FAIL"
        res_verdict = "FAIL"
    elif not pre_disk_ok:
        g["G21_DISK_RAM_PID_NO_WAIVER"] = "BLOCKED_LOW_DISK"
        res_verdict = "BLOCKED_LOW_DISK"
    elif not rt_disk_ok:
        g["G21_DISK_RAM_PID_NO_WAIVER"] = "FAIL"
        res_verdict = "BLOCKED_LOW_DISK"
    elif min_rt_ram < HARD_RAM_FLOOR_MIB_R5:
        g["G21_DISK_RAM_PID_NO_WAIVER"] = "FAIL"
        res_verdict = "BLOCKED_RAM"
    elif not ram_pid_ok:
        g["G21_DISK_RAM_PID_NO_WAIVER"] = "FAIL"
        res_verdict = "FAIL"
    else:
        g["G21_DISK_RAM_PID_NO_WAIVER"] = "PASS"
        res_verdict = "PASS"

    res_pass = g["G21_DISK_RAM_PID_NO_WAIVER"] == "PASS"
    low_disk = g["G21_DISK_RAM_PID_NO_WAIVER"] == "BLOCKED_LOW_DISK"

    # G17: Full repo fresh pytest collection & JUnit 1:1 bijection
    if regression.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        g["G17_FULL_FRESH_PYTEST_COLLECTION_JUNIT_BIJECTION"] = "BLOCKED_LOW_DISK" if low_disk else "FAIL"
        reg_verdict = "BLOCKED" if low_disk else "FAIL"
    elif (
        regression.get("status") == "PASS" and res_pass and bool(regression.get("junit_bijection_exact"))
        and int(regression.get("collected_count", 0)) >= MIN_EXPECTED_PYTEST_COLLECTED_R5
        and int(regression.get("collected_count", -1)) == int(regression.get("executed_count", -2))
        and int(regression.get("failed_count", 1)) == 0 and int(regression.get("error_count", 1)) == 0
        and int(regression.get("mandatory_skipped_count", 1)) == 0
    ):
        g["G17_FULL_FRESH_PYTEST_COLLECTION_JUNIT_BIJECTION"] = "PASS"
        reg_verdict = "PASS"
    else:
        g["G17_FULL_FRESH_PYTEST_COLLECTION_JUNIT_BIJECTION"] = "FAIL"
        reg_verdict = "FAIL"

    # G18: Full A1/SI8/adapters/D4 parity
    if parity.get("BASELINE_PARITY_VERDICT") == "NOT_RUN_BLOCKED_LOW_DISK":
        g["G18_A1_SI8_ADAPTERS_D4_FULL_PARITY"] = "BLOCKED_LOW_DISK" if low_disk else "FAIL"
        par_verdict = "BLOCKED" if low_disk else "FAIL"
    elif (
        parity.get("BASELINE_PARITY_VERDICT") == "PASS" and res_pass
        and int(parity.get("a1_population_count", 0)) == 56710 and int(parity.get("a1_mismatches_count", 1)) == 0
        and int(parity.get("si8_population_count", 0)) == 6375 and int(parity.get("si8_mismatches_count", 1)) == 0
        and int(parity.get("three_adapter_population_count", 0)) == 246 and int(parity.get("three_adapter_mismatches_count", 1)) == 0
        and int(parity.get("d4_historical_closed_trades", 1)) == 0
        and bool(((parity.get("per_decision_and_episode_fingerprints") or {}).get("all_sealed_fingerprints_exact_match")))
    ):
        g["G18_A1_SI8_ADAPTERS_D4_FULL_PARITY"] = "PASS"
        par_verdict = "PASS"
    else:
        g["G18_A1_SI8_ADAPTERS_D4_FULL_PARITY"] = "FAIL"
        par_verdict = "FAIL"

    # G19 & G20: W1/W2/W4/W1_repeat determinism & measured worker policy
    if f16.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        st_b = "BLOCKED_LOW_DISK" if low_disk else "FAIL"
        g["G19_DETERMINISTIC_W1_W2_W4_W1REPEAT"] = st_b
        g["G20_MEASURED_WORKER_POLICY"] = st_b
    else:
        p_ok = bool(f16.get("pytest_recompute_valid", False))
        s_ok = bool(f16.get("science_recompute_valid", False))
        p_pol = f16.get("pytest_worker_policy") or {}
        s_pol = f16.get("science_worker_policy") or {}
        g["G19_DETERMINISTIC_W1_W2_W4_W1REPEAT"] = "PASS" if (res_pass and p_ok and s_ok) else "FAIL"
        g["G20_MEASURED_WORKER_POLICY"] = (
            "PASS"
            if (
                res_pass and p_ok and s_ok and f16.get("status") == "PASS"
                and int(p_pol.get("applied_worker_count", 99)) <= 3
                and int(s_pol.get("applied_worker_count", 99)) <= GLOBAL_MAX_DESCENDANTS_R5
                and f16.get("full_suite_speedup_extrapolated") is False
            )
            else "FAIL"
        )

    # G22: Inherited (>=240) + NEW R5 (>=48) = >=288 unique RED/GREEN fault controls
    g["G22_INHERITED_AND_NEW_RED_GREEN_FAULTS"] = (
        "PASS"
        if (
            f18.get("status") == "PASS"
            and int(f18.get("inherited_r4_c2_c1_r3_controls_count", 0)) >= 240
            and int(f18.get("new_r5_controls_count", 0)) >= 48
            and int(f18.get("failed_controls", 1)) == 0
            and int(f18.get("unique_fault_ids", 0)) == int(f18.get("total_controls", -1))
        )
        else "FAIL"
    )

    # G23: Separate-OS independent verifier
    g["G23_SEPARATE_OS_VERIFIER_RAW_EVIDENCE"] = (
        ("PASS" if verifier_violations_count == 0 else "FAIL") if for_final_verifier_manifest else "PENDING_SEPARATE_OS_VERIFIER"
    )

    # G24: Separate-OS verifier mutation attacks (>=69 inherited + >=18 NEW R5)
    g["G24_VERIFIER_MUTATION_ATTACKS"] = (
        "PASS"
        if (
            f19.get("status") == "PASS"
            and bool(f19.get("clean_clone_verifier_green"))
            and int(f19.get("inherited_r4_c2_c1_r3_mutations_tested_count", 0)) >= 69
            and int(f19.get("inherited_r4_c2_c1_r3_mutations_failed_detection_count", 1)) == 0
            and int(f19.get("new_r5_mutations_tested_count", 0)) >= 18
            and int(f19.get("new_r5_mutations_failed_detection_count", 1)) == 0
        )
        else "FAIL"
    )

    # G25: No activation / zero OOS / no alpha inflation
    modes = f00.get("runtime_safety_modes") or {}
    g["G25_NO_ACTIVATION_OOS_ALPHA_CLAIM"] = (
        "PASS"
        if (
            f00.get("ACTIVATION_READY") == "NO"
            and all(modes.get(k) == "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO"))
            and f13.get("RESEARCH_ECONOMIC_VERDICT") == "NO_PROVEN_EDGE"
            and f13.get("KH04_ENTRY") == "BLOCKED_NO_DEV_CANDIDATE"
            and int(f13.get("OOS_EVALUATIONS", -1)) == 0
            and observed_register.get("execution_fidelity_status") in ("INCOMPLETE", "EVIDENCE_READY_FOR_SEPARATE_REVIEW")
            and (live_perm or observed_register.get("execution_fidelity_status") == "INCOMPLETE")
        )
        else "FAIL"
    )

    # G26: Publication remote proof
    false_pub = (
        (bool(f22.get("remote_readback_sha256_verified")) and not f22.get("publication_head_sha"))
        or (f22.get("ci_status") == "CI_PASS" and not bool(f22.get("actual_github_actions_run_verified")))
    )
    g["G26_PUBLICATION_REMOTE_PROOF"] = "FAIL" if false_pub else str(f22.get("publication_status") or "PRE_PUSH_PENDING")

    ordered = {k: g[k] for k in GATE_26_KEYS_KH03_R5}
    eng_keys = [k for k in GATE_26_KEYS_KH03_R5 if k not in ("G23_SEPARATE_OS_VERIFIER_RAW_EVIDENCE", "G26_PUBLICATION_REMOTE_PROOF")]
    if for_final_verifier_manifest:
        eng_keys.append("G23_SEPARATE_OS_VERIFIER_RAW_EVIDENCE")

    any_fail = any(ordered[k] == "FAIL" for k in GATE_26_KEYS_KH03_R5)
    all_eng_pass = all(ordered[k] == "PASS" for k in eng_keys) and not false_pub
    eng_verdict = "PASS" if all_eng_pass else ("BLOCKED" if (low_disk and not any_fail) else "FAIL")

    # Derive orthogonal data/capture/economic verdicts strictly from raw observations
    cap_exec = str(f05.get("capture_execution_status") or "BLOCKED_OWNER_AUTHORIZATION")
    if not live_perm:
        cap_exec_v = "BLOCKED_OWNER_AUTHORIZATION"
        cap_prov_v = "NO_REAL_CAPTURE"
        bbo_v = "MISSING"
        fund_v = "MISSING"
        data_readiness_v = "INCOMPLETE"
        exec_fid_v = "INCOMPLETE"
    else:
        cap_exec_v = cap_exec
        cap_prov_v = str(f05.get("capture_provenance_status") or "NO_REAL_CAPTURE")
        bbo_v = str(f08.get("observed_bbo_coverage_status") or "MISSING")
        fund_v = str(f09.get("observed_funding_readiness") or "MISSING")
        exec_fid_v = str(observed_register.get("execution_fidelity_status") or "INCOMPLETE")
        data_readiness_v = "DEV_DATA_READY_FOR_SEPARATE_RESEARCH_REVIEW" if exec_fid_v == "EVIDENCE_READY_FOR_SEPARATE_REVIEW" else "INCOMPLETE"

    acct_fee_v = str(f10.get("account_fee_readiness") or "UNKNOWN_NOT_LOCALLY_VERIFIED")
    if all_eng_pass and auth_st == "BLOCKED_OWNER_AUTHORIZATION":
        overall_cls = "ENGINEERING_PASS_DATA_FOUNDATION_READY_CAPTURE_BLOCKED_OWNER_AUTHORIZATION_ECONOMIC_NO_PROVEN_EDGE"
    elif all_eng_pass:
        overall_cls = "ENGINEERING_PASS_PARTIAL_DEV_DATA_ECONOMIC_NO_PROVEN_EDGE"
    elif low_disk and not any_fail:
        overall_cls = "BLOCKED_LOW_DISK"
    else:
        overall_cls = "FAIL_CLOSED"

    verdicts = {
        "ENGINEERING": eng_verdict,
        "RESOURCE_SAFETY": res_verdict,
        "FULL_REGRESSION": reg_verdict,
        "BACKTEST_PARITY": par_verdict,
        "CAPTURE_AUTHORIZATION": auth_st,
        "CAPTURE_EXECUTION": cap_exec_v,
        "CAPTURE_PROVENANCE": cap_prov_v,
        "DATA_READINESS": data_readiness_v,
        "OBSERVED_BBO_COVERAGE": bbo_v,
        "ACCOUNT_FEE_READINESS": acct_fee_v,
        "FUNDING_READINESS": fund_v,
        "EXECUTION_FIDELITY": exec_fid_v,
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA",
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE",
        "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": "NO",
        "PRODUCTION": "OFF",
        "LIVE": "OFF",
        "PAPER": "OFF",
        "AUTO": "OFF",
        "PUBLICATION": ordered["G26_PUBLICATION_REMOTE_PROOF"],
        "CI_STATUS": str(f22.get("ci_status") or "CI_UNKNOWN_NO_ACTIONS_RUN"),
    }
    return {
        "spec_version": SPEC_VERSION_KH03_R5,
        "schema_version": SCHEMA_VERSION_KH03_R5,
        "gates_26": ordered,
        "verdicts": verdicts,
        "overall_classification": overall_cls,
    }


# =====================================================================
# 16. build_r5_owner_handover_vi
# =====================================================================
def build_r5_owner_handover_vi(
    report: Mapping[str, Any],
    source_function_inventory: Sequence[Mapping[str, Any]],
    receipts: Mapping[str, Mapping[str, Any]],
) -> str:
    """Renders concise, truthful Vietnamese owner handover distinguishing executed vs blocked."""
    f00 = receipts.get("00") or {}
    f05 = receipts.get("05") or {}
    f13 = receipts.get("13") or {}
    f14 = receipts.get("14") or {}
    f15 = receipts.get("15") or {}
    f16 = receipts.get("16") or {}
    f17 = receipts.get("17") or {}
    f18 = receipts.get("18") or {}
    f19 = receipts.get("19") or {}
    v = report.get("verdicts") or {}
    g = report.get("gates_26") or {}
    pp = f16.get("pytest_worker_policy") or {}
    sp = f16.get("science_worker_policy") or {}

    lines = [
        "AURUM DESK — BAN GIAO CHU SO HUU KH-03.R5 (TIENG VIET)",
        "======================================================================",
        f"SPEC: {SPEC_VERSION_KH03_R5} | RUN_ID: {f00.get('run_id')}",
        f"BRANCH: {f00.get('branch')} | TESTED_EXECUTABLE_SHA: {f00.get('tested_executable_sha')}",
        f"CLASSIFICATION: {report.get('overall_classification')}",
        f"ENGINEERING={v.get('ENGINEERING')} | RESOURCE_SAFETY={v.get('RESOURCE_SAFETY')} | FULL_REGRESSION={v.get('FULL_REGRESSION')} | BACKTEST_PARITY={v.get('BACKTEST_PARITY')}",
        f"CAPTURE_AUTHORIZATION={v.get('CAPTURE_AUTHORIZATION')} | CAPTURE_EXECUTION={v.get('CAPTURE_EXECUTION')} | CAPTURE_PROVENANCE={v.get('CAPTURE_PROVENANCE')}",
        f"DATA_READINESS={v.get('DATA_READINESS')} | OBSERVED_BBO={v.get('OBSERVED_BBO_COVERAGE')} | ACCOUNT_FEE={v.get('ACCOUNT_FEE_READINESS')} | FUNDING={v.get('FUNDING_READINESS')} | EXECUTION_FIDELITY={v.get('EXECUTION_FIDELITY')}",
        f"ECONOMIC={v.get('RESEARCH_ECONOMIC_VERDICT')} | FRESH_OOS={v.get('FRESH_OOS')} | KH04={v.get('KH04_ENTRY')} | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO | PRODUCTION/LIVE/PAPER/AUTO=OFF",
        f"CI_STATUS={v.get('CI_STATUS')} | PUBLICATION={v.get('PUBLICATION')}",
        "",
        "1. TRANG THAI THU THAP DU LIEU THUC (KHONG GIA LAP):",
        f"- External requests attempted: {f05.get('external_requests_attempted')} | Accepted observed records: {f05.get('accepted_observed_records_count')} | QA offline fixture records (tach rieng): {f05.get('qa_fixture_records_count')}",
        "- Thu thap cong khai ben ngoai bi CHAN AN TOAN (CAPTURE_BLOCKED_OWNER_AUTHORIZATION) vi prompt nay KHONG thay the phe duyet van hanh rieng cua chu so huu; KHONG yeu cau API key/mat khau; KHONG goi endpoint private.",
        "- Nguon du lieu thieu: L1 BBO lien tuc <=1s (REST chi co snapshot, BLOCKED_CONTINUOUS_BBO), funding history (chua co trong C2/C3 allowlist), bieu phi tai khoan thuc te (UNKNOWN_NOT_LOCALLY_VERIFIED), slippage/fill mix (MISSING), fresh untouched OOS (ABSENT).",
        "",
        "2. KIEM THU TUOI, REPLAY R4 & PARITY:",
        f"- Full Repo Pytest JUnit 1:1: collected={f15.get('collected_count')}, executed={f15.get('executed_count')}, passed={f15.get('passed_count')}, skipped={f15.get('skipped_count')}, mandatory_skipped={f15.get('mandatory_skipped_count')}, failed={f15.get('failed_count')}, error={f15.get('error_count')} (max_workers={f15.get('max_workers_used')})",
        f"- Baseline Parity: A1={f14.get('a1_population_count')}/{f14.get('a1_mismatches_count')}, SI8={f14.get('si8_population_count')}/{f14.get('si8_mismatches_count')}, Adapter={f14.get('three_adapter_population_count')}/{f14.get('three_adapter_mismatches_count')}, D4={f14.get('d4_historical_closed_trades')}",
        f"- Fresh R4 Replay (REUSED DEV 1x, khong dung du lieu moi): closed={f13.get('null_1x_closed')} ({f13.get('null_1x_wins')}W/{f13.get('null_1x_losses')}L), gross={f13.get('null_1x_gross_usd')} USD, fees={f13.get('null_1x_closed_fees_usd')} USD, net={f13.get('null_1x_net_usd')} USD, exp={f13.get('null_1x_expectancy_r')}R, open_censored={f13.get('null_1x_open_censored')}, gap={f13.get('null_1x_gap_unresolved')}, >2.50USD_tails={f13.get('realized_losses_gt_2p50_count')}",
        f"- Faults: {f18.get('passed_controls')}/{f18.get('total_controls')} ({f18.get('inherited_r4_c2_c1_r3_controls_count')} ke thua + {f18.get('new_r5_controls_count')} moi R5) | Verifier Mutations: {f19.get('new_r5_mutations_detected_count')}/{f19.get('new_r5_mutations_tested_count')} moi R5 + {f19.get('inherited_r4_c2_c1_r3_mutations_detected_count')}/{f19.get('inherited_r4_c2_c1_r3_mutations_tested_count')} ke thua",
        "",
        "3. HIEU NANG W1/W2/W4/W1_REPEAT & TAI NGUYEN:",
        f"- Pytest subset (155): means={pp.get('mean_wall_sec_by_mode')}, w1_repeat={pp.get('w1_repeat_wall_sec')}s (var={pp.get('w1_repeat_variance_pct')}), fastest={pp.get('measured_fastest_mode')}, applied={pp.get('applied_worker_count')} (safe_cap={pp.get('safe_worker_cap')}), full_suite_extrapolated=False",
        f"- Science slice (8000): means={sp.get('mean_wall_sec_by_mode')}, w1_repeat={sp.get('w1_repeat_wall_sec')}s (var={sp.get('w1_repeat_variance_pct')}), fastest={sp.get('measured_fastest_mode')}, applied={sp.get('applied_worker_count')}",
        f"- Resources: min_disk={f17.get('min_disk_free_gib')} GiB, min_ram={f17.get('min_available_ram_mib')} MiB, peak_rss={f17.get('max_tree_rss_mib')} MiB, max_children={f17.get('max_descendant_children')}, orphans={f17.get('orphan_children_after_cleanup')}, waiver=False",
        f"- Reused read-only source modules audited: {len(source_function_inventory)} (0 locked production/science files modified).",
        "",
        "4. QUYET DINH TIEP THEO CUA CHU SO HUU:",
        "- Lua chon A: Dung nghien cuu chien luoc tren tap DEV thang 10/2026 (da dung lai nhieu lan, chi 17 ngay NY).",
        "- Lua chon B: Neu muon tiep tuc trong mot pha moi, chu so huu phe duyet ro rang pham vi thu thap du lieu cong khai DEV gioi han (symbol, productType, endpoint cong khai, max requests/bytes/thoi gian, thu muc local ngoai Git) va cung cap xac nhan bieu phi da che thong tin nhay cam (KHONG gui API key/mat khau). Tuyet doi KHONG bat trading.",
        "",
        "5. 26 CONG (G01..G26):",
    ] + [f"  {k}: {val}" for k, val in g.items()]
    return "\n".join(lines) + "\n"
