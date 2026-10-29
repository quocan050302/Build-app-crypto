"""
tools/acceptance_verdict.py
AURUM DESK V17.8.0.4 — Authoritative Release Verdict Resolution
Extracts verdict evaluation into a pure, fail-closed, testable module (V17.7.4 Section 10 & V17.8.0.1 DEF-178-15 & V17.8.0.4 Sections 14 & 22).
"""

from typing import Dict, List, Any, Optional

ALLOWED_GATE_STATUSES = {
    "PASS",
    "FAIL",
    "BLOCKED",
    "NOT_EXECUTED",
    "SKIPPED_WITH_REASON",
}

TRADING_AUTHORIZATION_V17_8_0_4 = [
    "MARKET_SHADOW_RESEARCH_ONLY",
    "PAPER_AUTO_TRADING_NOT_AUTHORIZED_BY_V17_8_0_4",
    "LIVE_TRADING_NOT_AUTHORIZED",
]

TRADING_AUTHORIZATION_V17_9_0 = [
    "AUTO_PAPER_NOT_AUTHORIZED_BY_V17_9_0",
    "LIVE_TRADING_NOT_AUTHORIZED",
]

# The mandatory 28 gates for V17.7.3 release acceptance (backward-compatible)
MANDATORY_GATES_V17_7_3 = [
    "G01_BRANCH_INTEGRITY",
    "G02_TEST_DB_BOOTSTRAP",
    "G03_UNSAFE_PATH_REJECTION",
    "G04_DESTRUCTIVE_FIXTURE_ISOLATION",
    "G05_RAW_ATR_PROVENANCE",
    "G06_REQUIRED_M1_ATR_FAIL_CLOSED",
    "G07_ATR_BUFFER_MATH_CORRECTNESS",
    "G08_STRUCTURAL_ANCHOR_CAUSAL",
    "G09_DISTANT_SL_FINANCIAL_INTEGRITY",
    "G10_CG01_EVALUATOR_INTEGRATION",
    "G11_M1_EVALUATOR_INTEGRATION",
    "G12_ORCHESTRATOR_REAL_ARM",
    "G13_REAL_ARM_CONCURRENCY",
    "G14_FILL_TIME_FINANCIAL_INVARIANTS",
    "G15_PAPER_EXIT_AND_PERSISTENCE",
    "G16_LIVE_ORDER_SAFETY_BOUNDARY",
    "G17_EXISTING_SL_DSL_CE_REGRESSION",
    "G18_V17_5_REGRESSION",
    "G19_V17_6_REGRESSION",
    "G20_FULL_BACKEND_PYTEST",
    "G21_FRONTEND_VITEST",
    "G22_FRONTEND_BUILD",
    "G23_FRONTEND_LINT",
    "G24_PLAYWRIGHT_BROWSER_E2E",
    "G25_DETERMINISTIC_REPLAY",
    "G26_ACCEPTANCE_RUNNER_NEGATIVE_TESTS",
    "G27_ARTIFACT_SOURCE_SHA_INTEGRITY",
    "G28_DOCS_DEFECT_REGISTER_CONSISTENCY",
]

# The mandatory 28 gates for V17.7.4 release acceptance (Section 14)
MANDATORY_GATES_V17_7_4 = list(MANDATORY_GATES_V17_7_3)

# The 42 gates from V17.8.0 (retained as subset)
MANDATORY_GATES_V17_8_0 = list(MANDATORY_GATES_V17_7_4) + [
    "G29_UNICORN_BREAKER_DETECTION",
    "G30_UNICORN_FVG_OVERLAP",
    "G31_CROSS_TIMEFRAME_CAUSALITY",
    "G32_STRUCTURAL_INVALIDATION_AUTHORITY",
    "G33_MODEL_SPECIFIC_SL_SELECTION",
    "G34_ATR_SPREAD_NOISE_PROTECTION",
    "G35_LIQUIDITY_TP_INDEPENDENCE",
    "G36_ENTRY_QUOTE_PROXIMITY",
    "G37_RISK_BUDGET_AND_NET_RR",
    "G38_COMPLETE_ARM_FILL_EXIT",
    "G39_HISTORICAL_REPLAY_DETERMINISM",
    "G40_FEATURE_FLAG_AND_LEGACY_PARITY",
    "G41_NEGATIVE_CONTROLS_AND_ANTI_FABRICATION",
    "G42_EXACT_SOURCE_AND_ARTIFACT_INTEGRITY",
]

# The mandatory 52 gates for V17.8.0.1 release acceptance (DEF-178-15)
MANDATORY_GATES_V17_8_0_1 = list(MANDATORY_GATES_V17_8_0) + [
    "G43_UNICORN_RUNTIME_INTERFACE_CONTRACT",
    "G44_FEATURE_FLAG_PERSISTENCE_AND_RELOAD",
    "G45_FILL_TIME_PROTECTIVE_DISTANCE_GUARD",
    "G46_FILL_TIME_STOP_BREACH_QUOTE_GUARD",
    "G47_CAUSAL_UNICORN_EPISODE_BINDING",
    "G48_ANTI_LOOKAHEAD_AND_UNCLOSED_CANDLE_REJECTION",
    "G49_END_TO_END_RUNTIME_INTEGRATION",
    "G50_FRONTEND_MOUNTED_SLTP_INTELLIGENCE_VIEW_E2E",
    "G51_FORENSIC_AUDIT_TRUTH_RECONCILIATION",
    "G52_HONEST_REPLAY_COMPARISON_DISCLOSURE",
]

# The mandatory 58 gates for V17.8.0.2 release acceptance (52 core gates + 6 observation safety gates)
MANDATORY_GATES_V17_8_0_2 = list(MANDATORY_GATES_V17_8_0_1) + [
    "G53_OBSERVE_ONLY_STARTUP_ISOLATION",
    "G54_ZERO_BACKGROUND_ORDER_MUTATIONS",
    "G55_ZERO_LIVE_EXCHANGE_DISPATCH",
    "G56_OBSERVATION_DATABASE_IMMUTABILITY",
    "G57_OBSERVATION_STARTUP_SHUTDOWN",
    "G58_RELEASE_ARTIFACT_TRUTH_INTEGRITY",
]

# The mandatory 75 gates for V17.8.0.3 release acceptance (58 gates + 17 market shadow & backtest gates)
MANDATORY_GATES_V17_8_0_3 = list(MANDATORY_GATES_V17_8_0_2) + [
    "G59_MARKET_SHADOW_PROFILE_CONTRACT",
    "G60_MARKET_SHADOW_EXECUTION_FORBIDDEN",
    "G61_PUBLIC_MARKET_FEED_CONNECTED_OR_EXPLICIT_BLOCKER",
    "G62_CANONICAL_QUOTE_VALIDATION_AND_ORDERING",
    "G63_SHADOW_CANDLE_FINALITY_AND_CACHE",
    "G64_SHADOW_DATABASE_ISOLATION",
    "G65_CONTINUOUS_UNICORN_SHADOW_EVALUATION",
    "G66_STRUCTURAL_SL_TP_SHADOW_DECISIONS",
    "G67_HISTORICAL_DATA_QUALITY_GATE",
    "G68_REAL_HISTORICAL_BACKTEST_EXECUTION",
    "G69_MATCHED_ABCD_COMPARISON_COMPLETENESS",
    "G70_OUT_OF_SAMPLE_AND_STRESS_VALIDATION",
    "G71_SHADOW_API_READ_ONLY_CONTRACT",
    "G72_FRONTEND_SHADOW_OBSERVATION_E2E",
    "G73_OPERATIONAL_DB_ZERO_MUTATION",
    "G74_MARKET_SHADOW_STARTUP_SHUTDOWN",
    "G75_EXACT_COMMIT_AND_ARTIFACT_TRUTH_VERIFICATION",
]

# The mandatory 88 gates for V17.8.0.4 release acceptance (75 gates + 13 artifact, parity & backtest methodology gates)
MANDATORY_GATES_V17_8_0_4 = list(MANDATORY_GATES_V17_8_0_3) + [
    "G76_REMOTE_ACCEPTANCE_LOG_COMPLETENESS",
    "G77_GIT_REPRODUCIBLE_SOURCE_MANIFEST",
    "G78_SOURCE_MANIFEST_NEGATIVE_CONTROLS",
    "G79_PRODUCTION_BACKTEST_SHADOW_DECISION_PARITY",
    "G80_UNICORN_CAUSAL_CHAIN_STRICT_VALIDATION",
    "G81_ATR_M1_M5_PROVENANCE_INTEGRITY",
    "G82_MATCHED_COHORT_STOP_OUT_ANALYSIS",
    "G83_OOS_ZERO_TRADE_INSUFFICIENT_SAMPLE_HANDLING",
    "G84_TRADE_LEVEL_PNL_RECONCILIATION",
    "G85_MARKET_SHADOW_ROUTING_REGRESSION",
    "G86_BACKTEST_ANTI_BIAS_NEGATIVE_CONTROLS",
    "G87_JUNIT_DOCUMENTATION_COUNT_RECONCILIATION",
    "G88_EXACT_COMMIT_REMOTE_EVIDENCE_VERIFICATION",
]

MANDATORY_GATES_V17_8 = list(MANDATORY_GATES_V17_8_0_4)

# The mandatory 107 gates for V17.9.0 release acceptance (88 gates + 19 shared parity, async infra, disk guard & backtest gates)
MANDATORY_GATES_V17_9_0 = list(MANDATORY_GATES_V17_8_0_4) + [
    "G89_SHARED_DECISION_CONTEXT_CONTRACT",
    "G90_SHARED_DECISION_SNAPSHOT_CONTRACT",
    "G91_PRODUCTION_M1_REVERSAL_PARITY",
    "G92_PRODUCTION_M1_CONTINUATION_PARITY",
    "G93_PRODUCTION_M1_FVG_CONTINUATION_PARITY",
    "G94_PRODUCTION_M1_SWEEP_RECLAIM_PARITY",
    "G95_PRODUCTION_CG01_PARITY",
    "G96_PRODUCTION_SHADOW_DECISION_PARITY",
    "G97_PRODUCTION_BACKTEST_DECISION_PARITY",
    "G98_SYNTHETIC_INDEX_SIGNAL_REMOVAL",
    "G99_UNICORN_CAUSAL_CHAIN_PARITY",
    "G100_STRUCTURAL_SL_TP_RR_PARITY",
    "G101_BOUNDED_ASYNC_TEST_RUNNER_INTEGRITY",
    "G102_DISK_BUDGET_GUARD_AND_TEMP_CLEANUP",
    "G103_CHECKPOINT_RESUME_AND_INVALIDATION",
    "G104_REAL_HISTORICAL_BACKTEST_SHARED_EVALUATOR",
    "G105_MATCHED_VS_FULL_STRATEGY_COHORT_INTEGRITY",
    "G106_EXACT_COMMIT_SOURCE_MANIFEST_VERIFICATION",
    "G107_REMOTE_ACCEPTANCE_BUNDLE_COMPLETENESS",
]

MANDATORY_GATES_V17_9 = list(MANDATORY_GATES_V17_9_0)

# Authoritative gate taxonomy and requirements (V17.7.4 Section 10 / V17.8.0.1 DEF-178-15 / V17.8.0.2 / V17.8.0.3 / V17.8.0.4)
TEST_GATES = {
    "G02_TEST_DB_BOOTSTRAP",
    "G03_UNSAFE_PATH_REJECTION",
    "G04_DESTRUCTIVE_FIXTURE_ISOLATION",
    "G05_RAW_ATR_PROVENANCE",
    "G06_REQUIRED_M1_ATR_FAIL_CLOSED",
    "G07_ATR_BUFFER_MATH_CORRECTNESS",
    "G08_STRUCTURAL_ANCHOR_CAUSAL",
    "G09_DISTANT_SL_FINANCIAL_INTEGRITY",
    "G10_CG01_EVALUATOR_INTEGRATION",
    "G11_M1_EVALUATOR_INTEGRATION",
    "G12_ORCHESTRATOR_REAL_ARM",
    "G13_REAL_ARM_CONCURRENCY",
    "G14_FILL_TIME_FINANCIAL_INVARIANTS",
    "G15_PAPER_EXIT_AND_PERSISTENCE",
    "G16_LIVE_ORDER_SAFETY_BOUNDARY",
    "G17_EXISTING_SL_DSL_CE_REGRESSION",
    "G18_V17_5_REGRESSION",
    "G19_V17_6_REGRESSION",
    "G20_FULL_BACKEND_PYTEST",
    "G21_FRONTEND_VITEST",
    "G24_PLAYWRIGHT_BROWSER_E2E",
    "G25_DETERMINISTIC_REPLAY",
    "G26_ACCEPTANCE_RUNNER_NEGATIVE_TESTS",
    # V17.8 Additions
    "G29_UNICORN_BREAKER_DETECTION",
    "G30_UNICORN_FVG_OVERLAP",
    "G31_CROSS_TIMEFRAME_CAUSALITY",
    "G32_STRUCTURAL_INVALIDATION_AUTHORITY",
    "G33_MODEL_SPECIFIC_SL_SELECTION",
    "G34_ATR_SPREAD_NOISE_PROTECTION",
    "G35_LIQUIDITY_TP_INDEPENDENCE",
    "G36_ENTRY_QUOTE_PROXIMITY",
    "G37_RISK_BUDGET_AND_NET_RR",
    "G38_COMPLETE_ARM_FILL_EXIT",
    "G39_HISTORICAL_REPLAY_DETERMINISM",
    "G40_FEATURE_FLAG_AND_LEGACY_PARITY",
    "G41_NEGATIVE_CONTROLS_AND_ANTI_FABRICATION",
    # V17.8.0.1 Additions (DEF-178-15)
    "G43_UNICORN_RUNTIME_INTERFACE_CONTRACT",
    "G44_FEATURE_FLAG_PERSISTENCE_AND_RELOAD",
    "G45_FILL_TIME_PROTECTIVE_DISTANCE_GUARD",
    "G46_FILL_TIME_STOP_BREACH_QUOTE_GUARD",
    "G47_CAUSAL_UNICORN_EPISODE_BINDING",
    "G48_ANTI_LOOKAHEAD_AND_UNCLOSED_CANDLE_REJECTION",
    "G49_END_TO_END_RUNTIME_INTEGRATION",
    "G50_FRONTEND_MOUNTED_SLTP_INTELLIGENCE_VIEW_E2E",
    "G51_FORENSIC_AUDIT_TRUTH_RECONCILIATION",
    "G52_HONEST_REPLAY_COMPARISON_DISCLOSURE",
    # V17.8.0.2 Observation Safety Additions
    "G53_OBSERVE_ONLY_STARTUP_ISOLATION",
    "G54_ZERO_BACKGROUND_ORDER_MUTATIONS",
    "G55_ZERO_LIVE_EXCHANGE_DISPATCH",
    "G56_OBSERVATION_DATABASE_IMMUTABILITY",
    "G57_OBSERVATION_STARTUP_SHUTDOWN",
    # V17.8.0.3 Market Shadow & Real Historical Backtesting Additions
    "G59_MARKET_SHADOW_PROFILE_CONTRACT",
    "G60_MARKET_SHADOW_EXECUTION_FORBIDDEN",
    "G61_PUBLIC_MARKET_FEED_CONNECTED_OR_EXPLICIT_BLOCKER",
    "G62_CANONICAL_QUOTE_VALIDATION_AND_ORDERING",
    "G63_SHADOW_CANDLE_FINALITY_AND_CACHE",
    "G64_SHADOW_DATABASE_ISOLATION",
    "G65_CONTINUOUS_UNICORN_SHADOW_EVALUATION",
    "G66_STRUCTURAL_SL_TP_SHADOW_DECISIONS",
    "G67_HISTORICAL_DATA_QUALITY_GATE",
    "G68_REAL_HISTORICAL_BACKTEST_EXECUTION",
    "G69_MATCHED_ABCD_COMPARISON_COMPLETENESS",
    "G70_OUT_OF_SAMPLE_AND_STRESS_VALIDATION",
    "G71_SHADOW_API_READ_ONLY_CONTRACT",
    "G72_FRONTEND_SHADOW_OBSERVATION_E2E",
    "G73_OPERATIONAL_DB_ZERO_MUTATION",
    "G74_MARKET_SHADOW_STARTUP_SHUTDOWN",
    # V17.8.0.4 Acceptance Closure, Parity & Backtest Methodology Additions
    "G76_REMOTE_ACCEPTANCE_LOG_COMPLETENESS",
    "G77_GIT_REPRODUCIBLE_SOURCE_MANIFEST",
    "G78_SOURCE_MANIFEST_NEGATIVE_CONTROLS",
    "G79_PRODUCTION_BACKTEST_SHADOW_DECISION_PARITY",
    "G80_UNICORN_CAUSAL_CHAIN_STRICT_VALIDATION",
    "G81_ATR_M1_M5_PROVENANCE_INTEGRITY",
    "G82_MATCHED_COHORT_STOP_OUT_ANALYSIS",
    "G83_OOS_ZERO_TRADE_INSUFFICIENT_SAMPLE_HANDLING",
    "G84_TRADE_LEVEL_PNL_RECONCILIATION",
    "G85_MARKET_SHADOW_ROUTING_REGRESSION",
    "G86_BACKTEST_ANTI_BIAS_NEGATIVE_CONTROLS",
    # V17.9.0 Shared Strategy Evaluator, Multi-Adapter Parity, Async Runner & Disk Guard Additions
    "G89_SHARED_DECISION_CONTEXT_CONTRACT",
    "G90_SHARED_DECISION_SNAPSHOT_CONTRACT",
    "G91_PRODUCTION_M1_REVERSAL_PARITY",
    "G92_PRODUCTION_M1_CONTINUATION_PARITY",
    "G93_PRODUCTION_M1_FVG_CONTINUATION_PARITY",
    "G94_PRODUCTION_M1_SWEEP_RECLAIM_PARITY",
    "G95_PRODUCTION_CG01_PARITY",
    "G96_PRODUCTION_SHADOW_DECISION_PARITY",
    "G97_PRODUCTION_BACKTEST_DECISION_PARITY",
    "G98_SYNTHETIC_INDEX_SIGNAL_REMOVAL",
    "G99_UNICORN_CAUSAL_CHAIN_PARITY",
    "G100_STRUCTURAL_SL_TP_RR_PARITY",
    "G101_BOUNDED_ASYNC_TEST_RUNNER_INTEGRITY",
    "G102_DISK_BUDGET_GUARD_AND_TEMP_CLEANUP",
    "G103_CHECKPOINT_RESUME_AND_INVALIDATION",
    "G104_REAL_HISTORICAL_BACKTEST_SHARED_EVALUATOR",
    "G105_MATCHED_VS_FULL_STRATEGY_COHORT_INTEGRITY",
}

COMMAND_GATES = {
    "G01_BRANCH_INTEGRITY",
    "G22_FRONTEND_BUILD",
    "G23_FRONTEND_LINT",
    "G28_DOCS_DEFECT_REGISTER_CONSISTENCY",
}

INTEGRITY_GATES = {
    "G27_ARTIFACT_SOURCE_SHA_INTEGRITY",
    "G42_EXACT_SOURCE_AND_ARTIFACT_INTEGRITY",
    "G58_RELEASE_ARTIFACT_TRUTH_INTEGRITY",
    "G75_EXACT_COMMIT_AND_ARTIFACT_TRUTH_VERIFICATION",
    "G87_JUNIT_DOCUMENTATION_COUNT_RECONCILIATION",
    "G88_EXACT_COMMIT_REMOTE_EVIDENCE_VERIFICATION",
    "G106_EXACT_COMMIT_SOURCE_MANIFEST_VERIFICATION",
    "G107_REMOTE_ACCEPTANCE_BUNDLE_COMPLETENESS",
}


def determine_release_verdict(
    actual_gate_results: Dict[str, Any],
    mandatory_gate_manifest: Optional[List[str]] = None,
) -> str:
    """
    Evaluates gate results against mandatory manifest with fail-closed semantics (V17.7.4 Section 10).
    Enforces authoritative typed gate requirements:
    - Test gates MUST have exit_code==0, verified counts dict, positive executed coverage (passed >= 1),
      and zero failures/errors.
    - Build and doc gates MUST have exit_code==0 and status=='PASS'.
    - Integrity gates MUST have verified artifact integrity (integrity_verified is True or verified test count).
    - Missing required evidence blocks the release regardless of payload status.
    """
    manifest = mandatory_gate_manifest if mandatory_gate_manifest is not None else MANDATORY_GATES_V17_7_4

    # 1. Manifest must be a non-empty list of unique strings
    if not manifest or not isinstance(manifest, list):
        return "BLOCKED"
    if len(manifest) == 0:
        return "BLOCKED"
    if len(set(manifest)) != len(manifest):
        return "BLOCKED"
    if any(not isinstance(g, str) or not g.strip() for g in manifest):
        return "BLOCKED"

    # 2. Results must be a non-empty dictionary
    if not actual_gate_results or not isinstance(actual_gate_results, dict):
        return "BLOCKED"

    # 3. Check every mandatory gate
    for gate_id in manifest:
        if gate_id not in actual_gate_results:
            return "BLOCKED"

        gate_data = actual_gate_results[gate_id]
        if not isinstance(gate_data, dict):
            return "BLOCKED"

        status = gate_data.get("status")
        if status not in ALLOWED_GATE_STATUSES or status != "PASS":
            return "BLOCKED"

        # Check exit code consistency (must be int, not bool, and == 0)
        if "exit_code" in gate_data:
            exit_code = gate_data["exit_code"]
            if exit_code is None or isinstance(exit_code, bool) or not isinstance(exit_code, int):
                return "BLOCKED"
            if exit_code != 0:
                return "BLOCKED"

        # Check report parsing/status
        if gate_data.get("report_status") in ("ERROR", "MISSING"):
            return "BLOCKED"

        # Authoritative Typed Gate Contract Checks
        if gate_id in TEST_GATES:
            # Test gates strictly require exit_code == 0
            if "exit_code" not in gate_data:
                return "BLOCKED"

            # Test gates strictly require counts evidence
            counts = gate_data.get("counts")
            if not counts or not isinstance(counts, dict):
                return "BLOCKED"

            for k in ("passed", "failed", "skipped", "errors", "collection_errors"):
                if k in counts:
                    v = counts[k]
                    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                        return "BLOCKED"

            passed = counts.get("passed", 0)
            if not isinstance(passed, int) or isinstance(passed, bool) or passed <= 0:
                return "BLOCKED"

            if counts.get("failed", 0) > 0:
                return "BLOCKED"
            if counts.get("errors", 0) > 0:
                return "BLOCKED"
            if counts.get("collection_errors", 0) > 0:
                return "BLOCKED"

            collected = counts.get("collected", 0)
            skipped = counts.get("skipped", 0)
            if collected > 0 and passed == 0 and skipped == collected:
                return "BLOCKED"

        elif gate_id in COMMAND_GATES:
            # Command gates strictly require exit_code == 0
            if "exit_code" not in gate_data or gate_data["exit_code"] != 0:
                return "BLOCKED"

        elif gate_id in INTEGRITY_GATES:
            # Integrity gate strictly requires integrity verification evidence
            has_integrity = gate_data.get("integrity_verified") is True
            has_passed_count = (
                isinstance(gate_data.get("counts"), dict)
                and gate_data["counts"].get("passed", 0) > 0
                and gate_data.get("integrity_verified") is not False
            )
            if not (has_integrity or has_passed_count):
                return "BLOCKED"

        else:
            # Generic/Custom gates (e.g. unit test manifests)
            gate_type = gate_data.get("gate_type", "command")
            if gate_type == "command" and "exit_code" not in gate_data:
                return "BLOCKED"

            if "counts" in gate_data:
                counts = gate_data["counts"]
                if not isinstance(counts, dict):
                    return "BLOCKED"

                for k in ("passed", "failed", "skipped", "errors", "collection_errors"):
                    if k in counts:
                        v = counts[k]
                        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                            return "BLOCKED"

                if counts.get("failed", 0) > 0:
                    return "BLOCKED"
                if counts.get("errors", 0) > 0:
                    return "BLOCKED"
                if counts.get("collection_errors", 0) > 0:
                    return "BLOCKED"

                passed = counts.get("passed", 0)
                collected = counts.get("collected", 0)
                skipped = counts.get("skipped", 0)
                if collected > 0 and passed == 0 and skipped == collected:
                    return "BLOCKED"

            if gate_data.get("integrity_verified") is False:
                return "BLOCKED"

    return "ACCEPTED_FOR_PAPER_OPT_IN"


def determine_v17_8_0_2_release_verdict(
    actual_gate_results: Dict[str, Any],
    mandatory_gate_manifest: Optional[List[str]] = None,
    runtime_preflight: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Resolves the V17.8.0.2 release verdict (Section 13):
    Allowed values:
    - V17_8_0_2_ACCEPTED_FOR_SAFE_OBSERVATION
    - V17_8_0_2_ACCEPTED_WITH_RUNTIME_RESTRICTIONS
    - V17_8_0_2_BLOCKED
    - V17_8_0_2_NOT_ACCEPTED
    """
    manifest = mandatory_gate_manifest if mandatory_gate_manifest is not None else MANDATORY_GATES_V17_8_0_2
    base_verdict = determine_release_verdict(actual_gate_results, manifest)
    if base_verdict != "ACCEPTED_FOR_PAPER_OPT_IN":
        return "V17_8_0_2_BLOCKED"

    if runtime_preflight is not None:
        if not isinstance(runtime_preflight, dict):
            return "V17_8_0_2_BLOCKED"
        if runtime_preflight.get("status") != "PASS":
            return "V17_8_0_2_BLOCKED"
        counters = runtime_preflight.get("counters", {})
        for zero_key in (
            "PAPER_ORDERS_CREATED",
            "PAPER_ORDERS_FILLED",
            "PAPER_POSITIONS_CLOSED",
            "LIVE_EXCHANGE_ORDERS_SENT",
            "UNAUTHORIZED_DB_WRITES",
            "BACKGROUND_TRADING_WORKERS_STARTED",
            "UNEXPECTED_RUNTIME_ERRORS",
        ):
            val = counters.get(zero_key)
            if val is None or isinstance(val, bool) or not isinstance(val, int) or val != 0:
                return "V17_8_0_2_BLOCKED"
        if runtime_preflight.get("db_sha256_before") != runtime_preflight.get("db_sha256_after"):
            return "V17_8_0_2_BLOCKED"

    return "V17_8_0_2_ACCEPTED_FOR_SAFE_OBSERVATION"


def determine_v17_8_0_3_release_verdict(
    actual_gate_results: Dict[str, Any],
    mandatory_gate_manifest: Optional[List[str]] = None,
    runtime_preflight: Optional[Dict[str, Any]] = None,
    market_shadow_preflight: Optional[Dict[str, Any]] = None,
    backtest_summary: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Resolves the V17.8.0.3 release verdict (Section 19):
    Allowed values:
    - V17_8_0_3_ACCEPTED_FOR_SAFE_MARKET_SHADOW_AND_BACKTEST
    - V17_8_0_3_ACCEPTED_WITH_EXTERNAL_DATA_RESTRICTIONS
    - V17_8_0_3_BLOCKED
    - V17_8_0_3_NOT_ACCEPTED
    """
    manifest = mandatory_gate_manifest if mandatory_gate_manifest is not None else MANDATORY_GATES_V17_8_0_3
    base_verdict = determine_release_verdict(actual_gate_results, manifest)
    if base_verdict != "ACCEPTED_FOR_PAPER_OPT_IN":
        return "V17_8_0_3_BLOCKED"

    # Validate OBSERVE_ONLY preflight if provided
    if runtime_preflight is not None:
        if (
            determine_v17_8_0_2_release_verdict(
                actual_gate_results,
                MANDATORY_GATES_V17_8_0_2,
                runtime_preflight,
            )
            != "V17_8_0_2_ACCEPTED_FOR_SAFE_OBSERVATION"
        ):
            return "V17_8_0_3_BLOCKED"

    # Validate MARKET_SHADOW preflight if provided
    if market_shadow_preflight is not None:
        if not isinstance(market_shadow_preflight, dict):
            return "V17_8_0_3_BLOCKED"
        if market_shadow_preflight.get("status") != "PASS":
            return "V17_8_0_3_BLOCKED"
        if market_shadow_preflight.get("runtime_profile") != "MARKET_SHADOW":
            return "V17_8_0_3_BLOCKED"
        if market_shadow_preflight.get("operational_db_sha256_before") != market_shadow_preflight.get("operational_db_sha256_after"):
            return "V17_8_0_3_BLOCKED"
        if market_shadow_preflight.get("operational_db_immutable") is not True:
            return "V17_8_0_3_BLOCKED"
        counters = market_shadow_preflight.get("counters", {})
        for zero_key in (
            "PAPER_ORDERS_CREATED",
            "PAPER_ORDERS_FILLED",
            "PAPER_POSITIONS_CLOSED",
            "LIVE_EXCHANGE_ORDERS_SENT",
            "UNAUTHORIZED_DB_WRITES",
            "BACKGROUND_TRADING_WORKERS_STARTED",
            "UNEXPECTED_RUNTIME_ERRORS",
        ):
            val = counters.get(zero_key)
            if val is None or isinstance(val, bool) or not isinstance(val, int) or val != 0:
                return "V17_8_0_3_BLOCKED"

    # Validate backtest summary if provided
    if backtest_summary is not None:
        if not isinstance(backtest_summary, dict):
            return "V17_8_0_3_BLOCKED"
        if backtest_summary.get("status") != "PASS":
            return "V17_8_0_3_BLOCKED"
        if int(backtest_summary.get("fabricated_candles_count", -1)) != 0:
            return "V17_8_0_3_BLOCKED"

    return "V17_8_0_3_ACCEPTED_FOR_SAFE_MARKET_SHADOW_AND_BACKTEST"


def determine_v17_8_0_4_release_verdict(
    actual_gate_results: Dict[str, Any],
    mandatory_gate_manifest: Optional[List[str]] = None,
    runtime_preflight: Optional[Dict[str, Any]] = None,
    market_shadow_preflight: Optional[Dict[str, Any]] = None,
    backtest_summary: Optional[Dict[str, Any]] = None,
    source_manifest_verified: bool = True,
) -> str:
    """
    Resolves the V17.8.0.4 Engineering Release Verdict (Section 22.1):
    Allowed values:
    - V17_8_0_4_ENGINEERING_ACCEPTED
    - V17_8_0_4_IMPLEMENTED_BUT_NOT_FULLY_ACCEPTED
    - V17_8_0_4_BLOCKED
    """
    manifest = mandatory_gate_manifest if mandatory_gate_manifest is not None else MANDATORY_GATES_V17_8_0_4
    base_verdict = determine_release_verdict(actual_gate_results, manifest)
    if base_verdict != "ACCEPTED_FOR_PAPER_OPT_IN":
        return "V17_8_0_4_BLOCKED"

    if not source_manifest_verified:
        return "V17_8_0_4_BLOCKED"

    v3_check = determine_v17_8_0_3_release_verdict(
        actual_gate_results,
        MANDATORY_GATES_V17_8_0_3,
        runtime_preflight,
        market_shadow_preflight,
        backtest_summary,
    )
    if v3_check != "V17_8_0_3_ACCEPTED_FOR_SAFE_MARKET_SHADOW_AND_BACKTEST":
        return "V17_8_0_4_BLOCKED"

    return "V17_8_0_4_ENGINEERING_ACCEPTED"


def determine_v17_8_0_4_backtest_verdict(
    backtest_summary: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Resolves the V17.8.0.4 Backtest Verdict (Section 22.2):
    Allowed values:
    - PRODUCTION_PARITY_BACKTEST_VERIFIED
    - HISTORICAL_RESEARCH_COMPLETED_PARITY_INCOMPLETE
    - REAL_HISTORICAL_BACKTEST_INSUFFICIENT_DATA
    - BACKTEST_BLOCKED
    """
    if not backtest_summary or not isinstance(backtest_summary, dict):
        return "BACKTEST_BLOCKED"
    if backtest_summary.get("status") != "PASS":
        return "BACKTEST_BLOCKED"
    if int(backtest_summary.get("fabricated_candles_count", -1)) != 0:
        return "BACKTEST_BLOCKED"
    if int(backtest_summary.get("m1_bars_count", 0)) <= 0:
        return "REAL_HISTORICAL_BACKTEST_INSUFFICIENT_DATA"
    if not backtest_summary.get("production_parity_verified", False):
        return "HISTORICAL_RESEARCH_COMPLETED_PARITY_INCOMPLETE"
    if not backtest_summary.get("strict_causal_unicorn_enforced", False):
        return "HISTORICAL_RESEARCH_COMPLETED_PARITY_INCOMPLETE"
    if not backtest_summary.get("atr_m5_provenance_verified", False):
        return "HISTORICAL_RESEARCH_COMPLETED_PARITY_INCOMPLETE"
    return "PRODUCTION_PARITY_BACKTEST_VERIFIED"


def determine_v17_8_0_4_performance_verdict(
    backtest_summary: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Resolves the V17.8.0.4 Trading Performance Verdict (Section 22.3):
    Allowed values:
    - PERFORMANCE_EVIDENCE_SUFFICIENT_FOR_REVIEW
    - PERFORMANCE_INCONCLUSIVE
    - NEGATIVE_OBSERVED_EXPECTANCY
    - PERFORMANCE_NOT_EVALUABLE
    """
    if not backtest_summary or not isinstance(backtest_summary, dict):
        return "PERFORMANCE_NOT_EVALUABLE"
    if backtest_summary.get("status") != "PASS":
        return "PERFORMANCE_NOT_EVALUABLE"
    total_trades = int(backtest_summary.get("variant_c_simulated_trades", 0))
    if total_trades <= 0:
        return "PERFORMANCE_INCONCLUSIVE"
    expectancy_r = backtest_summary.get("variant_c_net_expectancy_r")
    if expectancy_r is None:
        return "PERFORMANCE_INCONCLUSIVE"
    if float(expectancy_r) < 0.0:
        return "NEGATIVE_OBSERVED_EXPECTANCY"
    oos_status = backtest_summary.get("out_of_sample_verdict")
    if oos_status == "OUT_OF_SAMPLE_INCONCLUSIVE":
        return "PERFORMANCE_INCONCLUSIVE"
    return "PERFORMANCE_EVIDENCE_SUFFICIENT_FOR_REVIEW"


def determine_v17_9_0_verdicts_bundle(
    actual_gate_results: Dict[str, Any],
    mandatory_gate_manifest: Optional[List[str]] = None,
    runtime_preflight: Optional[Dict[str, Any]] = None,
    market_shadow_preflight: Optional[Dict[str, Any]] = None,
    backtest_summary: Optional[Dict[str, Any]] = None,
    parity_diff_summary: Optional[Dict[str, Any]] = None,
    disk_budget_summary: Optional[Dict[str, Any]] = None,
    concurrency_summary: Optional[Dict[str, Any]] = None,
    source_manifest_verified: bool = True,
) -> Dict[str, Any]:
    """
    Resolves the complete V17.9.0 Verdict Taxonomy (Section 22):
    1. decision_parity_verdict
    2. shared_evaluator_verdict
    3. backtest_methodology_verdict
    4. strategy_performance_verdict
    5. async_test_infrastructure_verdict
    6. disk_safety_verdict
    7. market_shadow_verdict
    8. release_acceptance_verdict
    9. trading_authorization
    """
    manifest = mandatory_gate_manifest if mandatory_gate_manifest is not None else MANDATORY_GATES_V17_9_0
    base_verdict = determine_release_verdict(actual_gate_results, manifest)

    # 1. Decision Parity Verdict
    parity_gates = (
        "G89_SHARED_DECISION_CONTEXT_CONTRACT",
        "G90_SHARED_DECISION_SNAPSHOT_CONTRACT",
        "G91_PRODUCTION_M1_REVERSAL_PARITY",
        "G92_PRODUCTION_M1_CONTINUATION_PARITY",
        "G93_PRODUCTION_M1_FVG_CONTINUATION_PARITY",
        "G94_PRODUCTION_M1_SWEEP_RECLAIM_PARITY",
        "G95_PRODUCTION_CG01_PARITY",
        "G96_PRODUCTION_SHADOW_DECISION_PARITY",
        "G97_PRODUCTION_BACKTEST_DECISION_PARITY",
        "G99_UNICORN_CAUSAL_CHAIN_PARITY",
        "G100_STRUCTURAL_SL_TP_RR_PARITY",
    )
    parity_gates_pass = all(
        isinstance(actual_gate_results.get(g), dict) and actual_gate_results[g].get("status") == "PASS"
        for g in parity_gates
    )
    parity_mismatches = 0
    if isinstance(parity_diff_summary, dict):
        parity_mismatches = int(parity_diff_summary.get("mismatched_decisions", 0))
    if parity_gates_pass and parity_mismatches == 0:
        decision_parity_verdict = "TRUE_PRODUCTION_DECISION_PARITY_VERIFIED"
    elif any(
        isinstance(actual_gate_results.get(g), dict) and actual_gate_results[g].get("status") == "PASS"
        for g in parity_gates
    ):
        decision_parity_verdict = "PARTIAL_DECISION_PARITY_ONLY"
    else:
        decision_parity_verdict = "DECISION_PARITY_NOT_ESTABLISHED"

    # 2. Shared Evaluator Verdict
    shared_gates = (
        "G89_SHARED_DECISION_CONTEXT_CONTRACT",
        "G90_SHARED_DECISION_SNAPSHOT_CONTRACT",
        "G96_PRODUCTION_SHADOW_DECISION_PARITY",
        "G97_PRODUCTION_BACKTEST_DECISION_PARITY",
        "G104_REAL_HISTORICAL_BACKTEST_SHARED_EVALUATOR",
    )
    if all(
        isinstance(actual_gate_results.get(g), dict) and actual_gate_results[g].get("status") == "PASS"
        for g in shared_gates
    ):
        shared_evaluator_verdict = "SHARED_STRATEGY_EVALUATOR_ACTIVE_ACROSS_ALL_THREE_PATHS"
    elif any(
        isinstance(actual_gate_results.get(g), dict) and actual_gate_results[g].get("status") == "PASS"
        for g in shared_gates
    ):
        shared_evaluator_verdict = "SHARED_EVALUATOR_PARTIAL_INTEGRATION"
    else:
        shared_evaluator_verdict = "SHARED_EVALUATOR_NOT_INTEGRATED"

    # 3. Backtest Methodology Verdict
    bt_gates_pass = all(
        isinstance(actual_gate_results.get(g), dict) and actual_gate_results[g].get("status") == "PASS"
        for g in (
            "G98_SYNTHETIC_INDEX_SIGNAL_REMOVAL",
            "G104_REAL_HISTORICAL_BACKTEST_SHARED_EVALUATOR",
            "G105_MATCHED_VS_FULL_STRATEGY_COHORT_INTEGRITY",
        )
    )
    bt_valid = (
        isinstance(backtest_summary, dict)
        and backtest_summary.get("status") == "PASS"
        and int(backtest_summary.get("fabricated_candles_count", -1)) == 0
        and int(backtest_summary.get("m1_bars_count", 0)) > 0
    )
    if bt_gates_pass and bt_valid:
        backtest_methodology_verdict = "REAL_HISTORICAL_PRODUCTION_PARITY_BACKTEST_VERIFIED"
    elif bt_valid:
        backtest_methodology_verdict = "HISTORICAL_BACKTEST_COMPLETED_WITH_LIMITATIONS"
    else:
        backtest_methodology_verdict = "BACKTEST_INVALID_OR_SYNTHETIC"

    # 4. Strategy Performance Verdict
    if not bt_valid:
        strategy_performance_verdict = "INSUFFICIENT_SAMPLE_FOR_CONCLUSION"
    else:
        c_trades = int(backtest_summary.get("variant_c_simulated_trades", 0))
        c_exp = backtest_summary.get("variant_c_net_expectancy_r")
        a_exp = backtest_summary.get("baseline_net_expectancy_r")
        if c_trades <= 0 or c_exp is None:
            strategy_performance_verdict = "INSUFFICIENT_SAMPLE_FOR_CONCLUSION"
        elif float(c_exp) > 0.0:
            strategy_performance_verdict = "POSITIVE_NET_EXPECTANCY_OBSERVED_NOT_CLEARED_FOR_TRADING"
        elif a_exp is not None and float(c_exp) > float(a_exp):
            strategy_performance_verdict = "PERFORMANCE_IMPROVED_OVER_LEGACY_STILL_NEGATIVE_OR_INCONCLUSIVE"
        else:
            strategy_performance_verdict = "NEGATIVE_EXPECTANCY_CONFIRMED"

    # 5. Async Test Infrastructure Verdict
    async_gates_pass = all(
        isinstance(actual_gate_results.get(g), dict) and actual_gate_results[g].get("status") == "PASS"
        for g in (
            "G101_BOUNDED_ASYNC_TEST_RUNNER_INTEGRITY",
            "G103_CHECKPOINT_RESUME_AND_INVALIDATION",
        )
    )
    conc_ok = True
    if isinstance(concurrency_summary, dict):
        conc_ok = (
            int(concurrency_summary.get("unaccounted_nodes_count", 0)) == 0
            and int(concurrency_summary.get("orphan_processes_remaining", 0)) == 0
        )
    if async_gates_pass and conc_ok:
        async_test_infrastructure_verdict = "BOUNDED_ASYNC_EXECUTION_VERIFIED"
    elif async_gates_pass:
        async_test_infrastructure_verdict = "ASYNC_EXECUTION_PARTIAL"
    else:
        async_test_infrastructure_verdict = "ASYNC_INFRASTRUCTURE_UNSAFE"

    # 6. Disk Safety Verdict
    disk_gate_pass = (
        isinstance(actual_gate_results.get("G102_DISK_BUDGET_GUARD_AND_TEMP_CLEANUP"), dict)
        and actual_gate_results["G102_DISK_BUDGET_GUARD_AND_TEMP_CLEANUP"].get("status") == "PASS"
    )
    disk_ok = True
    if isinstance(disk_budget_summary, dict):
        disk_ok = (
            disk_budget_summary.get("status") == "PASS"
            and disk_budget_summary.get("temp_directories_cleaned_up", True) is True
        )
    if disk_gate_pass and disk_ok:
        disk_safety_verdict = "DISK_BUDGET_AND_CLEANUP_VERIFIED"
    elif disk_gate_pass:
        disk_safety_verdict = "DISK_SAFETY_PARTIAL"
    else:
        disk_safety_verdict = "DISK_EXHAUSTION_RISK_PRESENT"

    # 7. Market Shadow Verdict
    v3_verdict = determine_v17_8_0_3_release_verdict(
        actual_gate_results,
        MANDATORY_GATES_V17_8_0_3,
        runtime_preflight,
        market_shadow_preflight,
        backtest_summary,
    )
    if v3_verdict == "V17_8_0_3_ACCEPTED_FOR_SAFE_MARKET_SHADOW_AND_BACKTEST":
        market_shadow_verdict = "SAFE_FOR_MARKET_SHADOW_RESEARCH_ONLY"
    elif v3_verdict == "V17_8_0_3_ACCEPTED_WITH_EXTERNAL_DATA_RESTRICTIONS":
        market_shadow_verdict = "MARKET_SHADOW_RESTRICTED"
    else:
        market_shadow_verdict = "MARKET_SHADOW_NOT_SAFE"

    # 8. Release Acceptance Verdict
    if (
        base_verdict == "ACCEPTED_FOR_PAPER_OPT_IN"
        and source_manifest_verified
        and decision_parity_verdict == "TRUE_PRODUCTION_DECISION_PARITY_VERIFIED"
        and shared_evaluator_verdict == "SHARED_STRATEGY_EVALUATOR_ACTIVE_ACROSS_ALL_THREE_PATHS"
        and backtest_methodology_verdict == "REAL_HISTORICAL_PRODUCTION_PARITY_BACKTEST_VERIFIED"
        and async_test_infrastructure_verdict == "BOUNDED_ASYNC_EXECUTION_VERIFIED"
        and disk_safety_verdict == "DISK_BUDGET_AND_CLEANUP_VERIFIED"
        and market_shadow_verdict == "SAFE_FOR_MARKET_SHADOW_RESEARCH_ONLY"
    ):
        release_acceptance_verdict = "V17_9_0_ENGINEERING_ACCEPTED_FOR_RESEARCH_ONLY"
    elif base_verdict == "ACCEPTED_FOR_PAPER_OPT_IN":
        release_acceptance_verdict = "V17_9_0_PARTIALLY_VERIFIED_NOT_ACCEPTED"
    else:
        release_acceptance_verdict = "V17_9_0_BLOCKED"

    return {
        "decision_parity_verdict": decision_parity_verdict,
        "shared_evaluator_verdict": shared_evaluator_verdict,
        "backtest_methodology_verdict": backtest_methodology_verdict,
        "strategy_performance_verdict": strategy_performance_verdict,
        "async_test_infrastructure_verdict": async_test_infrastructure_verdict,
        "disk_safety_verdict": disk_safety_verdict,
        "market_shadow_verdict": market_shadow_verdict,
        "release_acceptance_verdict": release_acceptance_verdict,
        "trading_authorization": list(TRADING_AUTHORIZATION_V17_9_0),
    }


def determine_v17_9_0_release_verdict(
    actual_gate_results: Dict[str, Any],
    mandatory_gate_manifest: Optional[List[str]] = None,
    runtime_preflight: Optional[Dict[str, Any]] = None,
    market_shadow_preflight: Optional[Dict[str, Any]] = None,
    backtest_summary: Optional[Dict[str, Any]] = None,
    parity_diff_summary: Optional[Dict[str, Any]] = None,
    disk_budget_summary: Optional[Dict[str, Any]] = None,
    concurrency_summary: Optional[Dict[str, Any]] = None,
    source_manifest_verified: bool = True,
) -> str:
    """Returns the top-level V17.9.0 release_acceptance_verdict string."""
    bundle = determine_v17_9_0_verdicts_bundle(
        actual_gate_results=actual_gate_results,
        mandatory_gate_manifest=mandatory_gate_manifest,
        runtime_preflight=runtime_preflight,
        market_shadow_preflight=market_shadow_preflight,
        backtest_summary=backtest_summary,
        parity_diff_summary=parity_diff_summary,
        disk_budget_summary=disk_budget_summary,
        concurrency_summary=concurrency_summary,
        source_manifest_verified=source_manifest_verified,
    )
    return bundle["release_acceptance_verdict"]


AUTHORITATIVE_GATE_MAPPING_V17_9_0_1: Dict[str, Dict[str, Any]] = {
    "G89_SHARED_DECISION_CONTEXT_CONTRACT": {
        "previous_selector_v17_9_0": "test_01 or test_02 or test_03",
        "repaired_selector_v17_9_0_1": "test_01 or test_02 or test_03 or test_04 or test_05 or test_v17_9_0_1_htf",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 5,
        "requirement": "MarketDecisionContext immutability, closed-bar causality, H4/H1 UTC boundary validation & quote checks",
    },
    "G90_SHARED_DECISION_SNAPSHOT_CONTRACT": {
        "previous_selector_v17_9_0": "test_04 or test_05",
        "repaired_selector_v17_9_0_1": "test_06 or test_07 or test_08 or test_v17_9_0_1_canonical_decision_result",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 3,
        "requirement": "StrategyDecisionSnapshot / DecisionResult 20-field schema, evaluate_decision entrypoint & deterministic hashes",
    },
    "G91_PRODUCTION_M1_REVERSAL_PARITY": {
        "previous_selector_v17_9_0": "test_06 or test_07",
        "repaired_selector_v17_9_0_1": "test_09 or test_14 or test_v17_9_0_1_reversal_parity",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 2,
        "requirement": "SMC_M1_REVERSAL independent pre-refactor baseline vs SharedStrategyEvaluator parity",
    },
    "G92_PRODUCTION_M1_CONTINUATION_PARITY": {
        "previous_selector_v17_9_0": "test_08 or test_09",
        "repaired_selector_v17_9_0_1": "test_10 or test_14 or test_v17_9_0_1_continuation_parity",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 2,
        "requirement": "SMC_M1_CONTINUATION independent pre-refactor baseline vs SharedStrategyEvaluator parity",
    },
    "G93_PRODUCTION_M1_FVG_CONTINUATION_PARITY": {
        "previous_selector_v17_9_0": "test_10 or test_11",
        "repaired_selector_v17_9_0_1": "test_11 or test_14 or test_v17_9_0_1_fvg_continuation_parity",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 2,
        "requirement": "SMC_M1_FVG_CONTINUATION independent pre-refactor baseline vs SharedStrategyEvaluator parity",
    },
    "G94_PRODUCTION_M1_SWEEP_RECLAIM_PARITY": {
        "previous_selector_v17_9_0": "test_12 or test_13",
        "repaired_selector_v17_9_0_1": "test_12 or test_14 or test_v17_9_0_1_sweep_reclaim_parity",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 2,
        "requirement": "SMC_M1_SWEEP_RECLAIM independent pre-refactor baseline vs SharedStrategyEvaluator parity",
    },
    "G95_PRODUCTION_CG01_PARITY": {
        "previous_selector_v17_9_0": "test_14",
        "repaired_selector_v17_9_0_1": "test_13 or test_14 or test_v17_9_0_1_cg01_parity",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 2,
        "requirement": "CG01_SWEEP_MSS_FVG independent pre-refactor baseline vs SharedStrategyEvaluator parity",
    },
    "G96_PRODUCTION_SHADOW_DECISION_PARITY": {
        "previous_selector_v17_9_0": "test_15",
        "repaired_selector_v17_9_0_1": "test_15 or test_17 or test_v17_9_0_1_runtime_spy_production_and_shadow",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 2,
        "requirement": "Live runtime spy & decision parity between ScalpWatcherOrchestrator.evaluate_cycle and MarketShadowService.evaluate_shadow_cycle",
    },
    "G97_PRODUCTION_BACKTEST_DECISION_PARITY": {
        "previous_selector_v17_9_0": "test_16 or test_17",
        "repaired_selector_v17_9_0_1": "test_16 or test_17 or test_18 or test_v17_9_0_1_runtime_spy_backtest",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 3,
        "requirement": "Live runtime spy & decision parity between Production, MARKET_SHADOW, and Chronological Backtest",
    },
    "G98_SYNTHETIC_INDEX_SIGNAL_REMOVAL": {
        "previous_selector_v17_9_0": "test_18 or test_19",
        "repaired_selector_v17_9_0_1": "test_21 or test_22 or test_29",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
        ],
        "min_passed": 3,
        "requirement": "AST & runtime verification of zero modulo/index-driven synthetic signal triggers in backtest",
    },
    "G99_UNICORN_CAUSAL_CHAIN_PARITY": {
        "previous_selector_v17_9_0": "test_20 or test_21 or test_22 or test_23",
        "repaired_selector_v17_9_0_1": "test_19 or test_20 or test_23 or test_24 or test_25 or test_v17_9_0_1_non_self_referential_oracle",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 5,
        "requirement": "Anti-fake parity trap detection, non-self-referential baseline oracle & Unicorn causal chain validation",
    },
    "G100_STRUCTURAL_SL_TP_RR_PARITY": {
        "previous_selector_v17_9_0": "test_24 or test_25 or test_26 or test_27 or test_28 or test_29 or test_30",
        "repaired_selector_v17_9_0_1": "test_26 or test_27 or test_28 or test_v17_9_0_1_negative_acceptance",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 3,
        "requirement": "Structural SL anchor, R:R, quantity, rejection reason parity & 15 negative acceptance scenarios",
    },
    "G101_BOUNDED_ASYNC_TEST_RUNNER_INTEGRITY": {
        "previous_selector_v17_9_0": "test_31 or test_32 or test_33",
        "repaired_selector_v17_9_0_1": "test_39 or test_v17_9_0_1_async_orchestrator",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 1,
        "requirement": "Bounded async multi-worker test orchestrator concurrency limit, failure accounting & zero silent skip",
    },
    "G102_DISK_BUDGET_GUARD_AND_TEMP_CLEANUP": {
        "previous_selector_v17_9_0": "test_34 or test_35 or test_36",
        "repaired_selector_v17_9_0_1": "test_33 or test_34 or test_35 or test_v17_9_0_1_disk_guard",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 3,
        "requirement": "Disk budget guard preflight, fail-closed threshold, worker temp cleanup & log compression",
    },
    "G103_CHECKPOINT_RESUME_AND_INVALIDATION": {
        "previous_selector_v17_9_0": "test_37 or test_38",
        "repaired_selector_v17_9_0_1": "test_36 or test_37 or test_38",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
        ],
        "min_passed": 3,
        "requirement": "Atomic execution state checkpoint resumption, SHA mismatch & corrupt JUnit XML invalidation",
    },
    "G104_REAL_HISTORICAL_BACKTEST_SHARED_EVALUATOR": {
        "previous_selector_v17_9_0": "test_39",
        "repaired_selector_v17_9_0_1": "test_30 or test_31 or test_v17_9_0_1_chronological_backtest_invokes_shared_evaluator",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 2,
        "requirement": "Real chronological historical backtest invoking SharedStrategyEvaluator across all 5 production models",
    },
    "G105_MATCHED_VS_FULL_STRATEGY_COHORT_INTEGRITY": {
        "previous_selector_v17_9_0": "test_40",
        "repaired_selector_v17_9_0_1": "test_32 or test_40 or test_v17_9_0_1_phase_b_55_scenario_parity",
        "test_files": [
            "backend/tests/test_v17_9_shared_parity_and_async_infra.py",
            "backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py",
        ],
        "min_passed": 2,
        "requirement": "Matched SL-only cohort vs Complete Strategy cohort denominators, 55-scenario baseline parity & runtime safety",
    },
}


def normalize_v17_9_0_1_gate_status(
    gate_data: Any,
    *,
    is_test_gate: bool = True,
    expected_source_sha: Optional[str] = None,
) -> str:
    """
    Phase H & Section 8 Gate Status Normalization:
    Never converts `SKIPPED`, `NOT_RUN`, `NOT_EXECUTED`, `BLOCKED`, `STALE_EVIDENCE`, or `UNKNOWN` into `PASS`.
    If `expected_source_sha` is provided and `gate_data` has a mismatched `tested_source_sha`, returns `BLOCKED`.
    Returns one of: `PASS`, `FAIL`, `BLOCKED`, `INCOMPLETE`.
    """
    if not isinstance(gate_data, dict):
        return "INCOMPLETE"
    raw_status = str(gate_data.get("status") or "UNKNOWN").upper().strip()
    if raw_status in ("NOT_EXECUTED", "NOT_RUN", "SKIPPED", "SKIPPED_WITH_REASON", "UNKNOWN", "INCOMPLETE"):
        return "INCOMPLETE"
    if raw_status in ("BLOCKED", "STALE_EVIDENCE", "INVALID_EVIDENCE"):
        return "BLOCKED"
    if raw_status != "PASS":
        return "FAIL"

    if expected_source_sha:
        gate_sha = gate_data.get("tested_source_sha")
        if gate_sha is not None and str(gate_sha).strip() != str(expected_source_sha).strip():
            return "BLOCKED"

    exit_code = gate_data.get("exit_code")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != 0):
        return "FAIL"

    if is_test_gate:
        counts = gate_data.get("counts")
        if not isinstance(counts, dict):
            return "INCOMPLETE"
        passed = int(counts.get("passed", 0) or 0)
        failed = int(counts.get("failed", 0) or 0)
        errors = int(counts.get("errors", 0) or 0)
        coll_errors = int(counts.get("collection_errors", 0) or 0)
        if failed > 0 or errors > 0 or coll_errors > 0:
            return "FAIL"
        if passed <= 0:
            return "INCOMPLETE"

    return "PASS"


def verify_gate_provenance_integrity(
    actual_gate_results: Dict[str, Any],
    final_code_sha: str,
    mandatory_gate_manifest: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Section 8 (V17.9.0.1.1): Verifies that no gate has a stale or mismatched `tested_source_sha`
    relative to `final_code_sha`, and that every mandatory gate was executed on `final_code_sha`.
    Never mutates or overwrites `tested_source_sha`.
    """
    manifest = mandatory_gate_manifest if mandatory_gate_manifest is not None else MANDATORY_GATES_V17_9_0
    if not final_code_sha or not isinstance(actual_gate_results, dict) or not manifest:
        return {
            "status": "INVALID_EVIDENCE",
            "is_valid": False,
            "final_code_sha": final_code_sha,
            "stale_gates": list(manifest or []),
            "missing_gates": list(manifest or []),
        }

    stale_gates: List[str] = []
    missing_gates: List[str] = []
    verified_gates: List[str] = []

    for gid in manifest:
        g_data = actual_gate_results.get(gid)
        if not isinstance(g_data, dict):
            missing_gates.append(gid)
            continue
        g_sha = g_data.get("tested_source_sha")
        if g_sha is not None and str(g_sha).strip() != str(final_code_sha).strip():
            stale_gates.append(gid)
            continue
        if str(g_data.get("status", "")).upper() in ("STALE_EVIDENCE", "INVALID_EVIDENCE"):
            stale_gates.append(gid)
            continue
        verified_gates.append(gid)

    is_valid = len(stale_gates) == 0 and len(missing_gates) == 0
    return {
        "status": "PASS" if is_valid else "INVALID_EVIDENCE",
        "is_valid": is_valid,
        "final_code_sha": final_code_sha,
        "verified_gates_count": len(verified_gates),
        "stale_gates": stale_gates,
        "missing_gates": missing_gates,
    }


def determine_v17_9_0_1_top_level_verdict(
    actual_gate_results: Dict[str, Any],
    mandatory_gate_manifest: Optional[List[str]] = None,
    expected_source_sha: Optional[str] = None,
) -> str:
    """
    Computes normalized top-level status (`PASS`, `FAIL`, `BLOCKED`, `INCOMPLETE`).
    """
    manifest = mandatory_gate_manifest if mandatory_gate_manifest is not None else MANDATORY_GATES_V17_9_0
    if not manifest or not isinstance(actual_gate_results, dict):
        return "INCOMPLETE"

    if expected_source_sha:
        prov = verify_gate_provenance_integrity(actual_gate_results, expected_source_sha, manifest)
        if not prov["is_valid"]:
            return "BLOCKED"

    statuses: List[str] = []
    for gate_id in manifest:
        if gate_id not in actual_gate_results:
            statuses.append("INCOMPLETE")
            continue
        is_test = gate_id in TEST_GATES
        statuses.append(
            normalize_v17_9_0_1_gate_status(
                actual_gate_results[gate_id],
                is_test_gate=is_test,
                expected_source_sha=expected_source_sha,
            )
        )

    if any(s == "FAIL" for s in statuses):
        return "FAIL"
    if any(s == "BLOCKED" for s in statuses):
        return "BLOCKED"
    if any(s == "INCOMPLETE" for s in statuses):
        return "INCOMPLETE"
    if determine_release_verdict(actual_gate_results, manifest) != "ACCEPTED_FOR_PAPER_OPT_IN":
        return "BLOCKED"
    return "PASS"

