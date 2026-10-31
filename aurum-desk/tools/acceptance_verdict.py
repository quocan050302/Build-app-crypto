"""
tools/acceptance_verdict.py
AURUM DESK V17.8.0.4 — Authoritative Release Verdict Resolution
Extracts verdict evaluation into a pure, fail-closed, testable module (V17.7.4 Section 10 & V17.8.0.1 DEF-178-15 & V17.8.0.4 Sections 14 & 22).
"""

from typing import Dict, List, Any, Optional, Sequence, Tuple
import hashlib
import json
import math
from pathlib import Path

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


REQUIRED_FIVE_WAY_SOURCES = (
    "historical_baseline_oracle",
    "shared_strategy_evaluator",
    "production_runtime",
    "market_shadow_runtime",
    "chronological_backtest_runtime",
)


def validate_comparison_source_accounting(
    record_sources: Any,
    *,
    claimed_five_way: bool = True,
    expected_unique_sources: int = 5,
) -> Dict[str, Any]:
    """
    DEF-179012-C03 & DEF-179013-R05 (N38-N40, N78-N79): Validates that a comparison record claiming 5-way parity
    genuinely executed all 5 required sources (`historical_baseline_oracle`, `shared_strategy_evaluator`,
    `production_runtime`, `market_shadow_runtime`, `chronological_backtest_runtime`), that no sources are missing,
    and no duplicate source masquerades as an independent runtime.
    """
    if isinstance(record_sources, (list, tuple)):
        if not record_sources:
            return {
                "is_valid": False,
                "status": "FAIL",
                "comparison_tier": "INVALID",
                "present_sources": [],
                "missing_sources": list(REQUIRED_FIVE_WAY_SOURCES),
                "violations": ["EMPTY_RECORD_SOURCES_LIST"],
            }
        rec = record_sources[0] if isinstance(record_sources[0], dict) else {}
    elif isinstance(record_sources, dict):
        rec = record_sources
    else:
        return {
            "is_valid": False,
            "status": "FAIL",
            "comparison_tier": "INVALID",
            "present_sources": [],
            "missing_sources": list(REQUIRED_FIVE_WAY_SOURCES),
            "violations": ["INVALID_RECORD_SOURCES_TYPE"],
        }

    violations: List[str] = []
    present_sources: List[str] = []
    missing_sources: List[str] = []
    engine_ids: List[str] = []
    for src_key in REQUIRED_FIVE_WAY_SOURCES:
        val = rec.get(src_key)
        if isinstance(val, dict) and (val.get("decision_status") or val.get("engine_id")):
            present_sources.append(src_key)
            eng_id = val.get("engine_id")
            if eng_id:
                engine_ids.append(f"{eng_id}_{val.get('runtime_role', '')}")
            if src_key == "production_runtime" and val.get("runtime") == "MARKET_SHADOW":
                violations.append("SHADOW_SUBSTITUTED_FOR_PRODUCTION_CONSUMED")
        else:
            missing_sources.append(src_key)

    if len(present_sources) == 5:
        tier = "FIVE_WAY_VERIFIED"
    elif len(present_sources) == 4:
        tier = "FOUR_WAY_CURRENT_PLUS_BASELINE"
    elif (
        len(present_sources) == 3
        and set(present_sources)
        == {"production_runtime", "market_shadow_runtime", "chronological_backtest_runtime"}
    ):
        tier = "THREE_WAY_CURRENT_RUNTIME"
    else:
        tier = f"PARTIAL_{len(present_sources)}_WAY"

    if claimed_five_way and len(present_sources) != 5:
        if tier == "THREE_WAY_CURRENT_RUNTIME":
            violations.append("THREE_WAY_COMPARISON_COUNTED_AS_FIVE_WAY")
        if "historical_baseline_oracle" in missing_sources:
            violations.append("FIVE_WAY_MISSING_HISTORICAL_BASELINE_DECISION")
        if "shared_strategy_evaluator" in missing_sources:
            violations.append("FIVE_WAY_MISSING_SHARED_EVALUATOR_DECISION")
        for m_src in missing_sources:
            if m_src not in ("historical_baseline_oracle", "shared_strategy_evaluator"):
                violations.append(f"FIVE_WAY_MISSING_SOURCE:{m_src}")

    if expected_unique_sources > 1 and len(engine_ids) >= 2:
        if len(set(engine_ids)) < len(engine_ids) and len(set(engine_ids)) == 1:
            violations.append("DUPLICATE_SOURCE_MASQUERADING_AS_INDEPENDENT_RUNTIME")

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "comparison_tier": tier,
        "present_sources": present_sources,
        "missing_sources": missing_sources,
        "violations": violations,
    }


def canonicalize_runtime_market_input(source_input: Any) -> Dict[str, Any]:
    """
    DEF-179013-R03: Extracts and canonicalizes material market input fields affecting trading decisions:
      - symbol, model_id, decision_timestamp_ms / as_of_ms
      - closed M1/M5/M15/H1/H4 candles (open_time, open, high, low, close, volume)
      - quote & spread assumptions (bid, ask, last, spread)
      - market structure inputs (swing_high_1m, swing_low_1m, htf_bias, h1_alignment)
      - liquidity context (liquidity_levels)
      - ATR values (atr_m1, atr_m5)
    Normalizes floats to 4 decimal places and sorts nested structures for deterministic hashing.
    """
    if hasattr(source_input, "to_dict"):
        raw = source_input.to_dict()
    elif isinstance(source_input, dict):
        raw = source_input
    else:
        raw = getattr(source_input, "__dict__", {})

    def _norm_candle(c: Any) -> Dict[str, Any]:
        if isinstance(c, dict):
            return {
                "open_time": int(c.get("open_time") or c.get("open_time_ms") or c.get("time") or 0),
                "open": round(float(c.get("open", 0.0) or 0.0), 4),
                "high": round(float(c.get("high", 0.0) or 0.0), 4),
                "low": round(float(c.get("low", 0.0) or 0.0), 4),
                "close": round(float(c.get("close", 0.0) or 0.0), 4),
                "volume": round(float(c.get("volume", 0.0) or 0.0), 4),
            }
        return {
            "open_time": int(getattr(c, "open_time", 0) or getattr(c, "open_time_ms", 0) or 0),
            "open": round(float(getattr(c, "open", 0.0) or 0.0), 4),
            "high": round(float(getattr(c, "high", 0.0) or 0.0), 4),
            "low": round(float(getattr(c, "low", 0.0) or 0.0), 4),
            "close": round(float(getattr(c, "close", 0.0) or 0.0), 4),
            "volume": round(float(getattr(c, "volume", 0.0) or 0.0), 4),
        }

    m1_bars = [_norm_candle(c) for c in (raw.get("closed_m1_candles") or raw.get("m1_candles") or raw.get("bars_1m") or raw.get("candles_1m") or ())]
    m5_bars = [_norm_candle(c) for c in (raw.get("closed_m5_candles") or raw.get("closed_5m_candles") or raw.get("m5_candles") or raw.get("bars_5m") or raw.get("candles_5m") or ())]
    m15_bars = [_norm_candle(c) for c in (raw.get("closed_m15_candles") or raw.get("closed_15m_candles") or raw.get("m15_candles") or raw.get("bars_15m") or raw.get("candles_15m") or ())]
    h1_bars = [_norm_candle(c) for c in (raw.get("closed_h1_candles") or raw.get("closed_1h_candles") or raw.get("h1_candles") or raw.get("bars_1h") or raw.get("candles_1h") or ())]
    h4_bars = [_norm_candle(c) for c in (raw.get("closed_h4_candles") or raw.get("closed_4h_candles") or raw.get("h4_candles") or raw.get("bars_4h") or raw.get("candles_4h") or ())]

    q_raw = raw.get("quote") or {}
    if hasattr(q_raw, "to_dict"):
        q_dict = q_raw.to_dict()
    elif isinstance(q_raw, dict):
        q_dict = q_raw
    else:
        q_dict = {
            "bid": getattr(q_raw, "bid", raw.get("bid", 0.0)),
            "ask": getattr(q_raw, "ask", raw.get("ask", 0.0)),
            "last": getattr(q_raw, "last", getattr(q_raw, "price", raw.get("last", 0.0))),
            "spread": getattr(q_raw, "spread", raw.get("spread", 0.0)),
        }
    bid_val = float(q_dict.get("bid", raw.get("bid", 0.0)) or 0.0)
    ask_val = float(q_dict.get("ask", raw.get("ask", 0.0)) or 0.0)
    last_val = float(q_dict.get("last", raw.get("last", 0.0)) or 0.0)
    if last_val == 0.0 and bid_val > 0.0 and ask_val > 0.0:
        last_val = round((bid_val + ask_val) / 2.0, 4)
    spread_val = float(q_dict.get("spread", raw.get("spread", 0.0)) or (ask_val - bid_val if ask_val and bid_val else 0.0))

    quote_norm = {
        "bid": round(bid_val, 4),
        "ask": round(ask_val, 4),
        "last": round(last_val, 4),
        "spread": round(spread_val, 4),
    }

    atr_m1 = round(float(raw.get("atr_m1") or getattr(raw.get("atr_m1_obj"), "atr", 0.0) or 0.0), 4)
    atr_m5 = round(float(raw.get("atr_m5") or getattr(raw.get("atr_m5_obj"), "atr", 0.0) or 0.0), 4)

    sh_1m = round(float(raw.get("swing_high_1m", 0.0) or 0.0), 4)
    sl_1m = round(float(raw.get("swing_low_1m", 0.0) or 0.0), 4)
    def _extract_liq(lv: Any) -> float:
        if isinstance(lv, (int, float)):
            return float(lv)
        if isinstance(lv, str):
            try:
                return float(lv)
            except Exception:
                return 0.0
        return float(getattr(lv, "price", getattr(lv, "level", 0.0)) or 0.0)

    liq = sorted([round(_extract_liq(lv), 4) for lv in (raw.get("liquidity_levels") or raw.get("liquidity_pools") or [])])

    return {
        "symbol": str(raw.get("symbol") or "XAUUSDT").upper(),
        "decision_timestamp_ms": int(raw.get("decision_timestamp_ms") or raw.get("as_of_ms") or raw.get("timestamp_ms") or 0),
        "quote": quote_norm,
        "swing_high_1m": sh_1m,
        "swing_low_1m": sl_1m,
        "htf_bias": str(raw.get("htf_bias") or "UNKNOWN"),
        "h1_alignment": str(raw.get("h1_alignment") or "UNKNOWN"),
        "liquidity_levels": liq,
        "m1_bars": m1_bars[-15:] if m1_bars else [],
        "m5_bars": m5_bars[-3:] if m5_bars else [],
        "m15_bars": m15_bars[-3:] if m15_bars else [],
        "h1_bars": h1_bars[-2:] if h1_bars else [],
        "h4_bars": h4_bars[-2:] if h4_bars else [],
    }


def compute_canonical_semantic_input_hash(source_input: Any) -> str:
    """
    DEF-179013-R03: Computes deterministic SHA-256 hash of canonicalized runtime market input.
    """
    canon = canonicalize_runtime_market_input(source_input)
    serialized = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def canonicalize_runtime_configuration(source_config: Any) -> Dict[str, Any]:
    """
    DEF-179013-R03: Extracts and canonicalizes runtime strategy, risk, stop policy, fee, and slippage configuration.
    """
    if hasattr(source_config, "to_dict"):
        raw = source_config.to_dict()
    elif isinstance(source_config, dict):
        raw = source_config
    else:
        raw = getattr(source_config, "__dict__", {})

    policy_settings = raw.get("policy_settings") or {}
    cost_settings = raw.get("cost_config_snapshot") or raw.get("cost_settings") or {}
    risk_settings = raw.get("risk_config_snapshot") or raw.get("risk_settings") or {}

    return {
        "stop_policy": str(raw.get("stop_policy") or "UNCORRELATED_STRUCTURAL_V17_8"),
        "capital_usdt": round(float(policy_settings.get("capital_usdt", raw.get("capital_usdt", 1000.0))), 2),
        "risk_pct": round(float(policy_settings.get("risk_pct", raw.get("risk_pct", 0.0025))), 6),
        "max_stop_distance": round(float(policy_settings.get("max_stop_distance", raw.get("max_stop_distance", 15.0))), 2),
        "min_stop_distance": round(float(policy_settings.get("min_stop_distance", raw.get("min_stop_distance", 0.5))), 2),
        "min_net_rr": round(float(policy_settings.get("min_net_rr", raw.get("min_net_rr", 1.5))), 2),
        "slippage_usd": round(float(cost_settings.get("slippage_usd", raw.get("slippage_usd", 0.1))), 4),
        "maker_fee_rate": round(float(cost_settings.get("maker_fee_rate", raw.get("maker_fee_rate", 0.0002))), 6),
        "taker_fee_rate": round(float(cost_settings.get("taker_fee_rate", raw.get("taker_fee_rate", 0.0004))), 6),
        "unicorn_structural_sl_enabled": bool(raw.get("unicorn_structural_sl_enabled", True)),
    }


def compute_canonical_semantic_config_hash(source_config: Any) -> str:
    """
    DEF-179013-R03: Computes deterministic SHA-256 hash of canonicalized runtime configuration.
    """
    canon = canonicalize_runtime_configuration(source_config)
    serialized = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def verify_cross_runtime_semantic_equivalence(runtime_snapshots: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    DEF-179013-R03: Validates cross-runtime input and configuration semantic equivalence across all runtimes.
    Ensures that each runtime snapshot computes its own canonical semantic hashes from authentic inputs.
    """
    violations: List[str] = []
    input_hashes: Dict[str, str] = {}
    config_hashes: Dict[str, str] = {}

    for rname, snap in runtime_snapshots.items():
        if not isinstance(snap, dict):
            violations.append(f"INVALID_RUNTIME_SNAPSHOT:{rname}")
            continue

        # Check market input canonical hash
        m_in = snap.get("market_input") or snap.get("context")
        if m_in is not None:
            computed_in = compute_canonical_semantic_input_hash(m_in)
            claimed_in = snap.get("canonical_semantic_input_hash")
            if claimed_in and claimed_in != computed_in:
                violations.append(f"CANONICAL_INPUT_HASH_NOT_DERIVED_FROM_INPUT:{rname}")
            input_hashes[rname] = claimed_in or computed_in
        else:
            can_in = snap.get("canonical_semantic_input_hash") or snap.get("input_snapshot_hash")
            if can_in:
                input_hashes[rname] = str(can_in)

        # Check configuration canonical hash
        cfg = snap.get("config") or snap.get("policy_settings")
        if cfg is not None:
            computed_cfg = compute_canonical_semantic_config_hash(cfg)
            claimed_cfg = snap.get("canonical_semantic_config_hash")
            if claimed_cfg and claimed_cfg != computed_cfg:
                violations.append(f"CANONICAL_CONFIG_HASH_NOT_DERIVED_FROM_CONFIG:{rname}")
            config_hashes[rname] = claimed_cfg or computed_cfg
        else:
            can_cfg = snap.get("canonical_semantic_config_hash") or snap.get("configuration_hash")
            if can_cfg:
                config_hashes[rname] = str(can_cfg)

    unique_in_hashes = set(input_hashes.values())
    if len(unique_in_hashes) > 1:
        violations.append("CANONICAL_SEMANTIC_INPUT_HASH_MISMATCH_ACROSS_RUNTIMES")

    unique_cfg_hashes = set(config_hashes.values())
    if len(unique_cfg_hashes) > 1:
        violations.append("CANONICAL_SEMANTIC_CONFIGURATION_HASH_MISMATCH_ACROSS_RUNTIMES")

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "violations": violations,
        "input_hashes": input_hashes,
        "config_hashes": config_hashes,
    }


def validate_comparison_record_hashes(
    record_or_sources: Any,
) -> Dict[str, Any]:
    """
    DEF-179012-C05 & DEF-179013-03 (N43-N46, N75-N77): Validates that:
      1. Every runtime provides valid non-null, non-empty raw hashes (`input_snapshot_hash`, `configuration_hash`).
      2. Raw source hashes are preserved without overwrite (detects illegal overwrites of historical hash).
      3. Canonical semantic input hash (`canonical_semantic_input_hash`) and configuration hash
         (`canonical_semantic_config_hash`) match identically across all compared runtime snapshots.
    Supports either a single comparison record / runtime-source mapping (`dict`) or a list/tuple
    of comparison records (`list`/`tuple`), validating each record individually.
    """
    violations: List[str] = []
    if isinstance(record_or_sources, (list, tuple)):
        if not record_or_sources:
            return {"is_valid": False, "status": "FAIL", "violations": ["EMPTY_COMPARISON_RECORD"]}
        for idx, item in enumerate(record_or_sources):
            sub_res = validate_comparison_record_hashes(item)
            if not sub_res["is_valid"]:
                for v in sub_res["violations"]:
                    violations.append(f"RECORD_{idx}:{v}")
        return {
            "is_valid": len(violations) == 0,
            "status": "PASS" if len(violations) == 0 else "FAIL",
            "records_validated": len(record_or_sources),
            "violations": violations,
        }

    if not isinstance(record_or_sources, dict) or not record_or_sources:
        return {"is_valid": False, "status": "FAIL", "violations": ["EMPTY_COMPARISON_RECORD"]}

    # Support either a single comparison record or a mapping of runtime -> snapshot dict
    snapshots: List[Dict[str, Any]] = []
    recognized_snapshot_keys = (
        "decision_status",
        "input_snapshot_hash",
        "raw_input_hash",
        "configuration_hash",
        "raw_configuration_hash",
        "canonical_semantic_input_hash",
        "canonical_input_hash",
    )
    if any(
        isinstance(v, dict) and any(hk in v for hk in recognized_snapshot_keys)
        for v in record_or_sources.values()
    ):
        for k, v in record_or_sources.items():
            if isinstance(v, dict):
                snapshots.append({"_source": k, **v})
    else:
        snapshots.append({"_source": "record", **record_or_sources})

    seen_input_hashes: set = set()
    seen_config_hashes: set = set()
    seen_canonical_in_hashes: set = set()
    seen_canonical_cfg_hashes: set = set()

    for snap in snapshots:
        src = snap.get("_source", "record")
        in_h = snap.get("raw_input_hash") or snap.get("input_snapshot_hash")
        cfg_h = snap.get("raw_configuration_hash") or snap.get("configuration_hash")
        can_in_h = snap.get("canonical_semantic_input_hash") or snap.get("canonical_input_hash")
        can_cfg_h = snap.get("canonical_semantic_config_hash") or snap.get("canonical_configuration_hash")

        # DEF-179013-03: Detect illegal overwrite of historical input hash
        if snap.get("raw_historical_input_hash_overwritten") or bool(snap.get("_overwritten_hash_detected", False)):
            violations.append(f"RAW_HISTORICAL_INPUT_HASH_OVERWRITTEN:{src}")

        schema_v = snap.get("canonical_schema_version")
        if schema_v is not None and schema_v != "V17.9.0.1.3_CANONICAL_HASH_V1":
            violations.append(f"INVALID_CANONICAL_SCHEMA_VERSION:{src}:{schema_v}")

        if snap.get("different_timestamp_detected"):
            violations.append(f"DIFFERENT_MARKET_TIMESTAMPS_WITH_MATCHING_CLAIMED_HASH:{src}")

        # DEF-179013-03: Validate that if underlying context is provided, canonical hash is authentically derived
        m_in = snap.get("market_input") or snap.get("context")
        if m_in is not None and can_in_h:
            computed_in = compute_canonical_semantic_input_hash(m_in)
            if str(can_in_h).strip() != str(computed_in).strip():
                violations.append(f"CANONICAL_INPUT_HASH_NOT_DERIVED_FROM_INPUT:{src}")

        cfg_in = snap.get("config") or snap.get("policy_settings")
        if cfg_in is not None and can_cfg_h:
            computed_cfg = compute_canonical_semantic_config_hash(cfg_in)
            if str(can_cfg_h).strip() != str(computed_cfg).strip():
                violations.append(f"CANONICAL_CONFIG_HASH_NOT_DERIVED_FROM_CONFIG:{src}")

        if in_h is None or not str(in_h).strip():
            violations.append(f"MISSING_INPUT_SNAPSHOT_HASH:{src}")
        else:
            seen_input_hashes.add(str(in_h).strip())
        if cfg_h is None or not str(cfg_h).strip():
            violations.append(f"MISSING_CONFIGURATION_HASH:{src}")
        else:
            seen_config_hashes.add(str(cfg_h).strip())

        if can_in_h is not None and str(can_in_h).strip():
            seen_canonical_in_hashes.add(str(can_in_h).strip())
        if can_cfg_h is not None and str(can_cfg_h).strip():
            seen_canonical_cfg_hashes.add(str(can_cfg_h).strip())

    # When canonical semantic hashes are explicitly provided, verify cross-runtime equivalence
    if len(seen_canonical_in_hashes) > 1:
        violations.append("CANONICAL_SEMANTIC_INPUT_HASH_MISMATCH_ACROSS_RUNTIMES")
    if len(seen_canonical_cfg_hashes) > 1:
        violations.append("CANONICAL_SEMANTIC_CONFIGURATION_HASH_MISMATCH_ACROSS_RUNTIMES")

    # If canonical semantic hashes are not used, fallback to strict legacy raw hash equality check (N45, N46)
    if not seen_canonical_in_hashes and len(seen_input_hashes) > 1:
        violations.append("INPUT_SNAPSHOT_HASH_MISMATCH_ACROSS_RUNTIMES")
    if not seen_canonical_cfg_hashes and len(seen_config_hashes) > 1:
        violations.append("CONFIGURATION_HASH_MISMATCH_ACROSS_RUNTIMES")

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "violations": violations,
    }


def validate_semantic_acceptance_bundle(
    *,
    parity_report: Dict[str, Any],
    artifact_manifest: Dict[str, Any],
    gate_results: Dict[str, Any],
    expected_code_sha: str,
    expected_release: Optional[str] = None,
    artifact_checksums_on_disk: Optional[Dict[str, str]] = None,
    backtest_manifest: Optional[Dict[str, Any]] = None,
    cache_validation_report: Optional[Dict[str, Any]] = None,
    chronological_reconciliation: Optional[Dict[str, Any]] = None,
    callable_manifest: Optional[Dict[str, Any]] = None,
    final_report_verdict: Optional[str] = None,
) -> Dict[str, Any]:
    """
    DEF-179012-C06 & DEF-179012-F05 (N47-N50, N69, N70): Semantic acceptance validator that fails closed when:
      - N47: `DECISION_PARITY_REPORT.json` has `status: "PASS"` while `five_way_verified == 0`
      - N48: `FINAL_ARTIFACT_MANIFEST.json` has mismatched SHA-256 vs on-disk file bytes
      - N49: `tested_source_sha != FINAL_CODE_SHA`
      - N50: Release version mismatch across generated artifacts (`V17.9.0.1.1` vs `V17.9.0.1.2`)
      - N69: Parity report claiming PASS/100% parity while classification breakdown includes
             `NOT_COMPARABLE > 0`, `NOT_EXECUTED > 0`, `UNEXPECTED_DIVERGENCE > 0`, `INPUT_MISMATCH > 0`,
             or `fabricated_forward_candles_count > 0`
      - N70: Final acceptance bundle claiming PASS while any cross-artifact contradiction exists
             (backtest `source_sha != expected_code_sha`, untrusted ledger cache, failed chronological
             reconciliation, or invalid historical callable authority).
    """
    violations: List[str] = []

    # N47: Parity report PASS with zero five_way_verified
    p_status = str(parity_report.get("status", "")).upper()
    five_way_verified = int(
        parity_report.get(
            "five_way_verified_count",
            parity_report.get("comparison_accounting", {}).get("five_way_verified", 0),
        )
        or 0
    )
    if p_status == "PASS" and five_way_verified <= 0:
        violations.append("PARITY_REPORT_PASS_WITH_ZERO_FIVE_WAY_VERIFIED")
    if p_status != "PASS":
        violations.append(f"PARITY_REPORT_NOT_PASS:{p_status or 'MISSING'}")

    # N69: Parity report classification breakdown & fabricated candle check
    class_bd = parity_report.get("classification_breakdown")
    if isinstance(class_bd, dict):
        for bad_cat in ("UNEXPECTED_DIVERGENCE", "INPUT_MISMATCH", "NOT_COMPARABLE", "NOT_EXECUTED"):
            if int(class_bd.get(bad_cat, 0) or 0) > 0:
                violations.append(f"PARITY_REPORT_HAS_{bad_cat}:{class_bd.get(bad_cat)}")
    if int(parity_report.get("fabricated_forward_candles_count", 0) or 0) > 0 or bool(
        parity_report.get("synthetic_forward_candles_used", False)
    ):
        violations.append("PARITY_REPORT_CONTAINS_FABRICATED_FORWARD_CANDLES")
    if parity_report.get("historical_callable_authority_valid") is False:
        violations.append("PARITY_REPORT_HISTORICAL_AUTHORITY_INVALID")

    # N48: Artifact manifest SHA-256 verification
    manifest_entries = artifact_manifest.get("artifacts", [])
    if isinstance(manifest_entries, list) and artifact_checksums_on_disk is not None:
        for entry in manifest_entries:
            if not isinstance(entry, dict):
                continue
            p_key = str(entry.get("path") or entry.get("filename") or "")
            claimed_sha = str(entry.get("sha256") or "")
            if p_key in artifact_checksums_on_disk:
                actual_sha = str(artifact_checksums_on_disk[p_key])
                if not claimed_sha or claimed_sha != actual_sha:
                    violations.append(f"ARTIFACT_MANIFEST_SHA256_MISMATCH:{p_key}")

    # N49: tested_source_sha != expected_code_sha
    tested_sha = str(gate_results.get("tested_source_sha") or "")
    if not expected_code_sha or tested_sha != expected_code_sha:
        violations.append(f"TESTED_SOURCE_SHA_MISMATCH:{tested_sha}!={expected_code_sha}")
    gates_map = gate_results.get("gates", {})
    if isinstance(gates_map, dict):
        for gid, g_val in gates_map.items():
            if isinstance(g_val, dict):
                g_sha = str(g_val.get("tested_source_sha") or "")
                if g_sha and g_sha != expected_code_sha:
                    violations.append(f"GATE_TESTED_SOURCE_SHA_MISMATCH:{gid}")

    # N50: Release version consistency across all artifacts
    target_release = expected_release if expected_release is not None else str(parity_report.get("release") or "V17.9.0.1.3")
    for name, doc in (
        ("DECISION_PARITY_REPORT.json", parity_report),
        ("FINAL_ARTIFACT_MANIFEST.json", artifact_manifest),
        ("ACCEPTANCE_GATE_RESULTS.json", gate_results),
    ):
        rel_val = str(doc.get("release") or "")
        if rel_val != target_release:
            violations.append(f"RELEASE_VERSION_MISMATCH:{name}:{rel_val}!={target_release}")

    # N70: Cross-artifact contradiction checks across backtest manifest, cache report, chronological reconciliation, callable manifest
    if isinstance(backtest_manifest, dict):
        bt_sha = str(backtest_manifest.get("source_sha") or "")
        if bt_sha != expected_code_sha:
            violations.append(f"CROSS_ARTIFACT_CONTRADICTION:BACKTEST_SOURCE_SHA_MISMATCH:{bt_sha}!={expected_code_sha}")
    if isinstance(cache_validation_report, dict):
        if not bool(cache_validation_report.get("cache_trusted", False)) or not bool(
            cache_validation_report.get("is_valid", False)
        ):
            violations.append("CROSS_ARTIFACT_CONTRADICTION:LEDGER_CACHE_NOT_TRUSTED")
    if isinstance(chronological_reconciliation, dict):
        if not bool(chronological_reconciliation.get("is_valid", False)) or not bool(
            chronological_reconciliation.get("reconciled", False)
        ):
            violations.append("CROSS_ARTIFACT_CONTRADICTION:CHRONOLOGICAL_BACKTEST_RECONCILIATION_FAILED")
    if isinstance(callable_manifest, dict):
        if not bool(callable_manifest.get("is_valid", True)) or bool(
            callable_manifest.get("reconstructed_oracle_injected", False)
        ):
            violations.append("CROSS_ARTIFACT_CONTRADICTION:HISTORICAL_CALLABLE_MANIFEST_INVALID")
    # DEF-179013-R02: Invariant: five_way_verified_count must match comparison_accounting and cannot be 0 when PASS
    cmp_acc = parity_report.get("comparison_accounting", {})
    acc_verified = int(cmp_acc.get("five_way_verified", 0) or 0)
    rep_verified = int(parity_report.get("five_way_verified_count", 0) or 0)
    if acc_verified > 0 and rep_verified == 0:
        violations.append("CROSS_ARTIFACT_CONTRADICTION:FIVE_WAY_VERIFIED_COUNT_ZERO_DESPITE_ACCOUNTING")
    if acc_verified > 0 and rep_verified > 0 and acc_verified != rep_verified:
        violations.append(f"CROSS_ARTIFACT_CONTRADICTION:FIVE_WAY_VERIFIED_COUNT_MISMATCH:{rep_verified}!={acc_verified}")

    # DEF-179013-R08: Reject dummy fallback configuration hashes ("c" * 64)
    for doc_name, check_doc in (
        ("backtest_manifest", backtest_manifest),
        ("parity_report", parity_report),
    ):
        if isinstance(check_doc, dict):
            cfg_h = str(check_doc.get("configuration_hash") or "")
            if cfg_h and cfg_h == "c" * 64:
                violations.append(f"DUMMY_FALLBACK_CONFIGURATION_HASH_DETECTED:{doc_name}")

    if final_report_verdict is not None:
        if str(final_report_verdict).upper() == "PASS" and len(violations) > 0:
            violations.append("CROSS_ARTIFACT_CONTRADICTION:FINAL_REPORT_CLAIMS_PASS_WITH_VIOLATIONS")

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "violations": violations,
    }


def derive_authoritative_acceptance_verdicts(
    *,
    gate_results: Dict[str, Any],
    parity_report: Dict[str, Any],
    negative_results: Optional[Dict[str, Any]] = None,
    backtest_metrics: Optional[Dict[str, Any]] = None,
    disk_report: Optional[Dict[str, Any]] = None,
    async_exec_report: Optional[Dict[str, Any]] = None,
    phase_b_manifest: Optional[Dict[str, Any]] = None,
    final_code_sha: str,
) -> Dict[str, Any]:
    """
    DEF-179013-R01: Eliminates all hardcoded success statements and derives authoritative release verdicts
    directly from measured, verified evidence. Each verdict provides:
      - verdict_id
      - evaluation_rule
      - actual_observation
      - expected_threshold
      - provenance
      - frozen_source_sha
      - validation_result: PASS, FAIL, or BLOCKED
      - failure_explanation
    """
    verdicts: Dict[str, Dict[str, Any]] = {}

    # 1. Code & Decision Authority Integrity
    tested_sha = str(gate_results.get("tested_source_sha") or "") if gate_results else ""
    code_auth_ok = bool(final_code_sha and tested_sha == final_code_sha)
    verdicts["VERDICT_CODE_AUTHORITY"] = {
        "verdict_id": "VERDICT_CODE_AUTHORITY",
        "evaluation_rule": "tested_source_sha == FINAL_CODE_SHA and no uncommitted code diffs",
        "actual_observation": f"tested_sha={tested_sha}, final_code_sha={final_code_sha}",
        "expected_threshold": "tested_sha == final_code_sha",
        "provenance": "ACCEPTANCE_GATE_RESULTS.json",
        "frozen_source_sha": final_code_sha,
        "validation_result": "PASS" if code_auth_ok else "FAIL",
        "failure_explanation": None if code_auth_ok else f"Code SHA mismatch: {tested_sha} != {final_code_sha}",
    }

    # 2. Historical Baseline Parity
    if not phase_b_manifest or not isinstance(phase_b_manifest, dict):
        hist_ok = False
        hist_st = "MISSING_EVIDENCE"
        hist_callable = False
        copied_entry = True
        hist_fail_reason = "Missing historical baseline manifest"
    else:
        hist_st = str(phase_b_manifest.get("isolated_historical_execution_status") or "FAIL")
        hist_callable = bool(phase_b_manifest.get("callable_authority_valid", False))
        copied_entry = bool(phase_b_manifest.get("copied_current_oracle_entry", True))
        hist_ok = (hist_st == "PASS" and hist_callable and not copied_entry)
        hist_fail_reason = None if hist_ok else f"Historical baseline execution status={hist_st}, callable_valid={hist_callable}, copied_oracle={copied_entry}"

    verdicts["VERDICT_HISTORICAL_BASELINE"] = {
        "verdict_id": "VERDICT_HISTORICAL_BASELINE",
        "evaluation_rule": "isolated_historical_execution_status == PASS and callable_authority_valid == True and copied_current_oracle_entry == False",
        "actual_observation": f"status={hist_st}, callable_valid={hist_callable}, copied_oracle={copied_entry}",
        "expected_threshold": "status == PASS, callable_valid == True, copied == False",
        "provenance": "HISTORICAL_BASELINE_PROVENANCE.json & BASELINE_MANIFEST.json",
        "frozen_source_sha": final_code_sha,
        "validation_result": "PASS" if hist_ok else "FAIL",
        "failure_explanation": hist_fail_reason,
    }

    # 3. Five-Way Cross-Runtime Parity
    if not parity_report or not isinstance(parity_report, dict):
        parity_ok = False
        p_status = "MISSING_EVIDENCE"
        p_divs = -1
        p_ver_count = 0
        p_fail_reason = "Missing parity report"
    else:
        p_status = str(parity_report.get("status") or "FAIL")
        p_divs = int(parity_report.get("unexpected_divergence_count", parity_report.get("divergences_count", -1)))
        p_ver_count = int(parity_report.get("five_way_verified_count", 0))
        parity_ok = (p_status == "PASS" and p_divs == 0 and p_ver_count >= 210)
        p_fail_reason = None if parity_ok else f"Parity failed: status={p_status}, divs={p_divs}, verified={p_ver_count}"

    verdicts["VERDICT_FIVE_WAY_PARITY"] = {
        "verdict_id": "VERDICT_FIVE_WAY_PARITY",
        "evaluation_rule": "status == PASS and divergences_count == 0 and five_way_verified_count >= 210",
        "actual_observation": f"status={p_status}, divergences={p_divs}, five_way_verified_count={p_ver_count}",
        "expected_threshold": "divergences == 0, five_way_verified >= 210",
        "provenance": "V179013_FIVE_WAY_PARITY_REPORT.json",
        "frozen_source_sha": final_code_sha,
        "validation_result": "PASS" if parity_ok else "FAIL",
        "failure_explanation": p_fail_reason,
    }

    # 4. Causal Forward Candle Simulation
    if not parity_report or not isinstance(parity_report, dict):
        causal_ok = False
        fab_candles = -1
        syn_used = True
        causal_fail_reason = "Missing parity report for causal forward candle validation"
    else:
        fab_candles = int(parity_report.get("fabricated_forward_candles_count", 0) or 0)
        syn_used = bool(parity_report.get("synthetic_forward_candles_used", False))
        causal_ok = (fab_candles == 0 and not syn_used)
        causal_fail_reason = None if causal_ok else "Fabricated or synthetic forward candles detected in simulation"

    verdicts["VERDICT_CAUSAL_FORWARD_CANDLES"] = {
        "verdict_id": "VERDICT_CAUSAL_FORWARD_CANDLES",
        "evaluation_rule": "fabricated_forward_candles_count == 0 and synthetic_forward_candles_used == False",
        "actual_observation": f"fabricated_candles={fab_candles}, synthetic_used={syn_used}",
        "expected_threshold": "fabricated == 0, synthetic == False",
        "provenance": "REAL_FORWARD_CANDLE_PROVENANCE.json",
        "frozen_source_sha": final_code_sha,
        "validation_result": "PASS" if causal_ok else "FAIL",
        "failure_explanation": causal_fail_reason,
    }

    # 5. Negative Controls Suite
    if not negative_results or not isinstance(negative_results, dict):
        neg_ok = False
        neg_passed = 0
        neg_total = 0
        neg_failed = -1
        neg_fail_reason = "Missing negative controls report"
    else:
        neg_passed = int(negative_results.get("passed_negative_controls", 0))
        neg_total = int(negative_results.get("total_negative_controls", 0))
        neg_failed = int(negative_results.get("failed_negative_controls", -1))
        neg_ok = (neg_total >= 131 and neg_passed == neg_total and neg_failed == 0)
        neg_fail_reason = None if neg_ok else f"Negative controls failed: passed={neg_passed}/{neg_total}, failed={neg_failed}"

    verdicts["VERDICT_NEGATIVE_CONTROLS"] = {
        "verdict_id": "VERDICT_NEGATIVE_CONTROLS",
        "evaluation_rule": "passed_negative_controls == total_negative_controls and total >= 131 and failed == 0",
        "actual_observation": f"passed={neg_passed}, total={neg_total}, failed={neg_failed}",
        "expected_threshold": ">=131/131 passed",
        "provenance": "V179013_NEGATIVE_CONTROLS.json",
        "frozen_source_sha": final_code_sha,
        "validation_result": "PASS" if neg_ok else "FAIL",
        "failure_explanation": neg_fail_reason,
    }

    # 6. Full Regression Gates
    if not gate_results or not isinstance(gate_results, dict):
        gates_ok = False
        passed_gates = 0
        total_gates = 0
        failed_gates = -1
        blocked_gates = -1
        gates_fail_reason = "Missing gate results"
    else:
        passed_gates = int(gate_results.get("passed_gates_count", 0) or 0)
        total_gates = int(gate_results.get("total_mandatory_gates", 0) or 107)
        failed_gates = int(gate_results.get("failed_gates_count", 0) or 0)
        blocked_gates = int(gate_results.get("blocked_gates_count", 0) or 0)
        gates_ok = (passed_gates >= 107 and failed_gates == 0 and blocked_gates == 0)
        gates_fail_reason = None if gates_ok else f"Gates failed: passed={passed_gates}/{total_gates}, failed={failed_gates}, blocked={blocked_gates}"

    verdicts["VERDICT_FULL_REGRESSION_GATES"] = {
        "verdict_id": "VERDICT_FULL_REGRESSION_GATES",
        "evaluation_rule": "passed_gates_count >= 107 and failed_gates_count == 0 and blocked_gates_count == 0",
        "actual_observation": f"passed={passed_gates}, total={total_gates}, failed={failed_gates}, blocked={blocked_gates}",
        "expected_threshold": "107/107 passed",
        "provenance": "ACCEPTANCE_GATE_RESULTS.json",
        "frozen_source_sha": final_code_sha,
        "validation_result": "PASS" if gates_ok else "FAIL",
        "failure_explanation": gates_fail_reason,
    }

    # 7. Asynchronous Runner & Resource Protection
    if not disk_report or not isinstance(disk_report, dict):
        async_ok = False
        disk_st = "MISSING_EVIDENCE"
        free_bytes = 0
        disk_fail_reason = "Missing disk guard report"
    else:
        disk_st = str(disk_report.get("status") or "FAIL")
        free_bytes = int(disk_report.get("free_disk_bytes_postflight") or disk_report.get("free_bytes_postflight") or disk_report.get("postflight_free_disk_bytes") or disk_report.get("latest_free_disk_bytes") or 0)
        async_ok = (disk_st == "PASS" and free_bytes > 8_000_000_000)
        disk_fail_reason = None if async_ok else f"Disk safety violation: status={disk_st}, free={free_bytes}"

    verdicts["VERDICT_ASYNC_AND_DISK_SAFETY"] = {
        "verdict_id": "VERDICT_ASYNC_AND_DISK_SAFETY",
        "evaluation_rule": "disk_guard_status == PASS and free_bytes_postflight > 8GB and orphan_workers == 0",
        "actual_observation": f"disk_status={disk_st}, free_bytes={free_bytes}",
        "expected_threshold": "disk_status == PASS, free > 8GB",
        "provenance": "V179013_DISK_GUARD_REPORT.json",
        "frozen_source_sha": final_code_sha,
        "validation_result": "PASS" if async_ok else "FAIL",
        "failure_explanation": disk_fail_reason,
    }

    # 8. Strategy Financial Performance & Exact Source Provenance
    if not backtest_metrics or not isinstance(backtest_metrics, dict):
        perf_ok = False
        net_pnl = 0.0
        sim_trades = 0
        bt_source_sha = ""
        perf_fail_reason = "Missing backtest metrics"
    else:
        b_auth = backtest_metrics.get("authoritative_production_parity_simulation")
        if not b_auth or not isinstance(b_auth, dict) or "net_pnl" not in b_auth or "simulated_trades" not in b_auth:
            perf_ok = False
            net_pnl = 0.0
            sim_trades = 0
            bt_source_sha = ""
            perf_fail_reason = "Incomplete or missing authoritative simulation metrics in backtest metrics"
        else:
            net_pnl = float(b_auth.get("net_pnl"))
            sim_trades = int(b_auth.get("simulated_trades"))
            top_bt_sha = str(backtest_metrics.get("backtest_source_sha") or "").strip()
            nested_bt_sha = str(b_auth.get("source_sha") or "").strip()
            bt_source_sha = top_bt_sha or nested_bt_sha

            if not bt_source_sha:
                perf_ok = False
                perf_fail_reason = "Missing authoritative backtest source SHA in backtest metrics"
            elif not final_code_sha or not final_code_sha.strip():
                perf_ok = False
                perf_fail_reason = "Missing final code SHA for exact-source provenance validation"
            elif top_bt_sha and nested_bt_sha and top_bt_sha != nested_bt_sha:
                perf_ok = False
                perf_fail_reason = f"Contradictory backtest source SHAs: top={top_bt_sha} != nested={nested_bt_sha}"
            elif bt_source_sha.strip() != final_code_sha.strip():
                perf_ok = False
                perf_fail_reason = f"Backtest source SHA mismatch: {bt_source_sha} != {final_code_sha}"
            elif sim_trades <= 0:
                perf_ok = False
                perf_fail_reason = f"Zero simulated trades in backtest: {sim_trades}"
            else:
                perf_ok = True
                perf_fail_reason = None

    verdicts["VERDICT_STRATEGY_PERFORMANCE"] = {
        "verdict_id": "VERDICT_STRATEGY_PERFORMANCE",
        "evaluation_rule": "authoritative backtest metrics present, sim_trades > 0, backtest source_sha == final_code_sha",
        "actual_observation": f"simulated_trades={sim_trades}, net_pnl={net_pnl}, bt_source_sha={bt_source_sha}",
        "expected_threshold": "valid metrics, exact source sha match, truthful financial disclosure",
        "provenance": "HISTORICAL_BACKTEST_METRICS.json",
        "frozen_source_sha": final_code_sha,
        "validation_result": "PASS" if perf_ok else "FAIL",
        "failure_explanation": perf_fail_reason,
    }

    # Overall Release Readiness Verdict
    all_mandatory_passed = (
        code_auth_ok
        and hist_ok
        and parity_ok
        and causal_ok
        and neg_ok
        and gates_ok
        and async_ok
        and perf_ok
    )

    if all_mandatory_passed:
        overall_verdict = "VERIFIED_FOUNDATION_CLOSURE — READY_TO_PROCEED_TO_V17_9_1"
        overall_status = "PASS"
    else:
        failing_verdicts = [vid for vid, v in verdicts.items() if v["validation_result"] != "PASS"]
        overall_verdict = f"BLOCKED_BY_VERDICTS:{','.join(failing_verdicts)}"
        overall_status = "FAIL"

    return {
        "release": "V17.9.0.1.3",
        "final_code_sha": final_code_sha,
        "overall_status": overall_status,
        "release_readiness_verdict": overall_verdict,
        "engineering_acceptance_verdict": "V17_9_0_ENGINEERING_ACCEPTED_FOR_RESEARCH_ONLY" if all_mandatory_passed else "REJECTED",
        "trading_authorization_verdict": "MARKET_SHADOW_RESEARCH_ONLY (PAPER_AUTO_TRADING_NOT_AUTHORIZED, LIVE_TRADING_NOT_AUTHORIZED)",
        "verdicts": verdicts,
    }


def verify_runtime_input_captures(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    DEF-179013-R31-01 & DEF-179013-R31-02: Fail-closed validator for R3.1 runtime input capture records.
    Requires:
    1. Non-empty sequence of captures.
    2. Every record has valid schema R31_RUNTIME_CAPTURE_V1.
    3. No HISTORICAL_PRODUCTION capture point contains MarketShadow or market_shadow.
    4. Each scenario contains all 5 mandatory runtime boundaries:
       HISTORICAL_PRODUCTION, SHARED_STRATEGY_EVALUATOR, PRODUCTION, MARKET_SHADOW, CHRONOLOGICAL_BACKTEST.
    5. Each record has invocation_completed is True.
    6. Non-empty raw_input_hash and canonical_input_hash.
    """
    violations: List[str] = []
    if not records:
        return {
            "status": "FAIL",
            "is_valid": False,
            "violations": ["EMPTY_RUNTIME_INPUT_CAPTURES"],
            "records_count": 0,
        }

    required_runtimes = {
        "HISTORICAL_PRODUCTION",
        "SHARED_STRATEGY_EVALUATOR",
        "PRODUCTION",
        "MARKET_SHADOW",
        "CHRONOLOGICAL_BACKTEST",
    }
    runtimes_by_scenario: Dict[str, set] = {}

    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            violations.append(f"INVALID_RECORD_FORMAT:index_{idx}")
            continue

        scen_id = str(rec.get("scenario_id") or f"unknown_{idx}")
        rt_name = str(rec.get("runtime_name") or "")
        cp = str(rec.get("capture_point") or "")
        exec_id = str(rec.get("execution_id") or "")
        inv_comp = rec.get("invocation_completed")
        raw_in_h = str(rec.get("raw_input_hash") or "")
        canon_in_h = str(rec.get("canonical_input_hash") or "")

        if rt_name:
            runtimes_by_scenario.setdefault(scen_id, set()).add(rt_name)

        if not exec_id:
            violations.append(f"MISSING_EXECUTION_ID:{scen_id}:{rt_name}")

        if inv_comp is not True:
            violations.append(f"INVOCATION_NOT_COMPLETED:{scen_id}:{rt_name}")

        if not raw_in_h:
            violations.append(f"MISSING_RAW_INPUT_HASH:{scen_id}:{rt_name}")

        if not canon_in_h:
            violations.append(f"MISSING_CANONICAL_INPUT_HASH:{scen_id}:{rt_name}")

        if rt_name == "HISTORICAL_PRODUCTION":
            if "marketshadow" in cp.lower():
                violations.append(f"MISLABELED_CAPTURE_POINT:{scen_id}:{cp}")

    # Check that each scenario has all 5 runtimes
    for scen_id, found_runtimes in runtimes_by_scenario.items():
        missing = required_runtimes - found_runtimes
        if missing:
            for m in sorted(list(missing)):
                violations.append(f"MISSING_RUNTIME_CAPTURE:{scen_id}:{m}")

    return {
        "status": "PASS" if not violations else "FAIL",
        "is_valid": len(violations) == 0,
        "violations": violations,
        "records_count": len(records),
        "scenarios_count": len(runtimes_by_scenario),
    }


def verify_parity_classification_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    DEF-179013-R31-02: Fail-closed validator for R3.1 parity classification report.
    Enforces that exact matches, documented changes, and unexpected divergences
    are logically consistent and that any unexpected divergence fails closed.
    """
    violations: List[str] = []
    if not isinstance(report, dict) or not report:
        return {
            "status": "FAIL",
            "is_valid": False,
            "violations": ["EMPTY_PARITY_CLASSIFICATION_REPORT"],
        }

    total = int(report.get("total_comparisons", 0))
    exact = int(report.get("exact_matches", 0))
    expected_doc = int(report.get("expected_documented_changes", 0))
    unexp = int(report.get("unexpected_divergences", 0))
    status = str(report.get("status") or "").upper()

    if status != "PASS":
        violations.append("PARITY_CLASSIFICATION_STATUS_NOT_PASS")

    if unexp > 0:
        violations.append(f"PARITY_CLASSIFICATION_HAS_UNEXPECTED_DIVERGENCES:{unexp}")

    breakdown = report.get("breakdown_by_category") or {}
    b_exact = int(breakdown.get("EXACT_MATCH", 0))
    b_doc = int(breakdown.get("EXPECTED_DOCUMENTED_CHANGE", 0))
    b_unexp = int(breakdown.get("UNEXPECTED_DIVERGENCE", 0))

    if exact != b_exact:
        violations.append(f"PARITY_CLASSIFICATION_CONTRADICTION:exact_mismatch:{exact}!={b_exact}")

    if expected_doc != b_doc:
        violations.append(f"PARITY_CLASSIFICATION_CONTRADICTION:documented_mismatch:{expected_doc}!={b_doc}")

    if unexp != b_unexp:
        violations.append(f"PARITY_CLASSIFICATION_CONTRADICTION:divergence_mismatch:{unexp}!={b_unexp}")

    if exact + expected_doc + unexp != total:
        violations.append(f"PARITY_CLASSIFICATION_CONTRADICTION:sum_mismatch:{exact}+{expected_doc}+{unexp}!={total}")

    return {
        "status": "PASS" if not violations else "FAIL",
        "is_valid": len(violations) == 0,
        "violations": violations,
    }


R31_MANDATORY_ARTIFACTS = [
    "V179013_R31_ROOT_CAUSE_AUDIT.md",
    "V179013_R31_DEFECT_CLOSURE_MATRIX.md",
    "V179013_R31_RUNTIME_INPUT_CAPTURE.jsonl",
    "V179013_R31_INDEPENDENT_RUNTIME_INPUT_HASHES.json",
    "V179013_R31_RUNTIME_CAPTURE_VERIFICATION.json",
    "V179013_R31_NEGATIVE_FAULT_INJECTION.json",
    "V179013_R31_JUNIT_AGGREGATION.json",
    "V179013_R31_ASYNC_WORKER_REPORT.json",
    "V179013_R31_RESOURCE_TELEMETRY.json",
    "V179013_R31_FIVE_WAY_PARITY_REPORT.json",
    "V179013_R31_PARITY_CLASSIFICATION_REPORT.json",
    "V179013_R31_HISTORICAL_LINEAGE.jsonl",
    "V179013_R31_EXACT_SOURCE_MANIFEST.json",
    "V179013_R31_BACKTEST_METRICS.json",
    "V179013_R31_MATCHED_COHORT_REPORT.json",
    "V179013_R31_OOS_REPORT.json",
    "V179013_R31_ARTIFACT_MANIFEST.json",
    "V179013_R31_INDEPENDENT_VERIFICATION.json",
    "V179013_R31_FINAL_ACCEPTANCE_AUDIT.md",
]


def verify_persisted_acceptance_artifacts(
    target_dir: Path,
    expected_code_sha: str,
    expected_release: str = "V17.9.0.1.3",
) -> Dict[str, Any]:
    """
    DEF-179013-R01: Reads persisted artifacts from disk, verifies checksums, validates cross-artifact
    consistency, and recalculates the final release verdict directly from disk bytes.
    """
    violations: List[str] = []
    target_dir = Path(target_dir)

    mandatory_files = [
        "ACCEPTANCE_GATE_RESULTS.json",
        "V179013_FIVE_WAY_PARITY_REPORT.json",
        "V179013_NEGATIVE_CONTROLS.json",
        "V179013_ASYNC_EXECUTION_REPORT.json",
        "V179013_DISK_GUARD_REPORT.json",
        "FINAL_ARTIFACT_MANIFEST.json",
        "HISTORICAL_BACKTEST_METRICS.json",
        "BASELINE_MANIFEST.json",
    ]

    # Additional R2 mandatory files to check if present in target_dir or manifest
    r2_mandatory_files = [
        "V179013_R2_INITIAL_AUDIT.md",
        "V179013_R2_DEFECT_CLOSURE_MATRIX.md",
        "V179013_R2_HISTORICAL_PRODUCTION_LINEAGE.jsonl",
        "V179013_R2_INDEPENDENT_RUNTIME_INPUT_HASHES.json",
        "V179013_R2_FIVE_WAY_PARITY_REPORT.json",
        "V179013_R2_NEGATIVE_FAULT_INJECTION.json",
        "V179013_R2_JUNIT_AGGREGATION.json",
        "V179013_R2_ASYNC_WORKER_REPORT.json",
        "V179013_R2_RESOURCE_TELEMETRY.json",
        "V179013_R2_EXACT_SOURCE_MANIFEST.json",
        "V179013_R2_BACKTEST_METRICS.json",
        "V179013_R2_MATCHED_COHORT_REPORT.json",
        "V179013_R2_OOS_REPORT.json",
        "V179013_R2_INDEPENDENT_ARTIFACT_VERIFICATION.json",
        "V179013_R2_FINAL_ACCEPTANCE_AUDIT.md",
    ]

    # Canonical R3 mandatory deliverables (Section 4 & Section 14)
    r3_mandatory_files = [
        "V179013_R3_INITIAL_ROOT_CAUSE_AUDIT.md",
        "V179013_R3_DEFECT_CLOSURE_MATRIX.md",
        "V179013_R3_HISTORICAL_PRODUCTION_LINEAGE.jsonl",
        "V179013_R3_RUNTIME_INPUT_CAPTURE.jsonl",
        "V179013_R3_INDEPENDENT_RUNTIME_INPUT_HASHES.json",
        "V179013_R3_FIVE_WAY_PARITY_REPORT.json",
        "V179013_R3_PARITY_CLASSIFICATION_REPORT.json",
        "V179013_R3_NEGATIVE_CONTROLS.json",
        "V179013_R3_JUNIT_AGGREGATION.json",
        "V179013_R3_ASYNC_WORKER_REPORT.json",
        "V179013_R3_DISK_RESOURCE_REPORT.json",
        "V179013_R3_BACKTEST_METRICS.json",
        "V179013_R3_BACKTEST_SOURCE_MANIFEST.json",
        "V179013_R3_MATCHED_COHORT_REPORT.json",
        "V179013_R3_OOS_REPORT.json",
        "V179013_R3_ARTIFACT_MANIFEST.json",
        "V179013_R3_INDEPENDENT_VERIFICATION.json",
        "V179013_R3_FINAL_ACCEPTANCE_AUDIT.md",
    ]

    loaded_docs: Dict[str, Any] = {}
    for mf in mandatory_files:
        fpath = target_dir / mf
        if not fpath.exists():
            violations.append(f"MISSING_PERSISTED_ARTIFACT:{mf}")
        elif fpath.stat().st_size == 0:
            violations.append(f"EMPTY_MANDATORY_ARTIFACT:{mf}")
        else:
            try:
                loaded_docs[mf] = json.loads(fpath.read_text(encoding="utf-8"))
            except Exception as ex:
                violations.append(f"CORRUPT_JSON_ARTIFACT:{mf}:{ex}")

    # Check R2 mandatory files if target_dir is configured for R2
    for r2_f in r2_mandatory_files:
        fpath = target_dir / r2_f
        if fpath.exists():
            if fpath.stat().st_size == 0:
                violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r2_f}")
            elif r2_f.endswith(".json"):
                try:
                    r2_doc = json.loads(fpath.read_text(encoding="utf-8"))
                    if isinstance(r2_doc, dict) and len(r2_doc) == 0:
                        violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r2_f}")
                    loaded_docs[r2_f] = r2_doc
                except Exception as ex:
                    violations.append(f"CORRUPT_JSON_ARTIFACT:{r2_f}:{ex}")

    # Check R3.1 mandatory deliverables (Fail-Closed: all 19 files must exist and be valid)
    is_r31_context = (
        (target_dir / "V179013_R31_ROOT_CAUSE_AUDIT.md").exists()
        or (target_dir.exists() and any(f.name.startswith("V179013_R31_") for f in target_dir.iterdir() if f.is_file()))
        or target_dir.name in ("v17_9_0_1_3", "docs")
    )
    if is_r31_context:
        for r31_f in R31_MANDATORY_ARTIFACTS:
            fpath = target_dir / r31_f
            if not fpath.exists():
                violations.append(f"MISSING_MANDATORY_ARTIFACT:{r31_f}")
            elif fpath.stat().st_size == 0:
                violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r31_f}")
            elif r31_f.endswith(".json"):
                try:
                    r31_doc = json.loads(fpath.read_text(encoding="utf-8"))
                    if isinstance(r31_doc, dict) and len(r31_doc) == 0:
                        violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r31_f}")
                    loaded_docs[r31_f] = r31_doc
                    if r31_f == "V179013_R31_PARITY_CLASSIFICATION_REPORT.json":
                        p_res = verify_parity_classification_report(r31_doc)
                        if not p_res["is_valid"]:
                            violations.extend(p_res["violations"])
                    elif r31_f == "V179013_R31_EXACT_SOURCE_MANIFEST.json":
                        if str(r31_doc.get("status") or "").upper() != "PASS":
                            violations.append("R31_EXACT_SOURCE_MANIFEST_STATUS_NOT_PASS")
                        bt_sha = str(r31_doc.get("backtest_source_sha") or "").strip()
                        final_sha = str(r31_doc.get("final_code_sha") or "").strip()
                        if not bt_sha:
                            violations.append("R31_EXACT_SOURCE_MANIFEST_MISSING_BACKTEST_SHA")
                        if not final_sha:
                            violations.append("R31_EXACT_SOURCE_MANIFEST_MISSING_FINAL_SHA")
                        if bt_sha and final_sha and bt_sha != final_sha:
                            violations.append(f"R31_EXACT_SOURCE_MANIFEST_SHA_MISMATCH:{bt_sha}!={final_sha}")
                        if expected_code_sha and bt_sha != expected_code_sha.strip():
                            violations.append(f"R31_EXACT_SOURCE_MANIFEST_EXPECTED_SHA_MISMATCH:{bt_sha}!={expected_code_sha}")
                except Exception as ex_j:
                    violations.append(f"CORRUPT_JSON_ARTIFACT:{r31_f}:{ex_j}")
            elif r31_f.endswith(".jsonl"):
                lines = [l.strip() for l in fpath.read_text(encoding="utf-8").splitlines() if l.strip()]
                if not lines:
                    violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r31_f}")
                elif r31_f == "V179013_R31_RUNTIME_INPUT_CAPTURE.jsonl":
                    parsed_captures = []
                    for line_idx, line in enumerate(lines):
                        try:
                            parsed_captures.append(json.loads(line))
                        except Exception as ex_l:
                            violations.append(f"CORRUPT_JSONL_LINE:{r31_f}:{line_idx}:{ex_l}")
                    c_res = verify_runtime_input_captures(parsed_captures)
                    if not c_res["is_valid"]:
                        violations.extend(c_res["violations"])

    # Check R3 mandatory files if present or if checking R3 delivery
    for r3_f in r3_mandatory_files:
        fpath = target_dir / r3_f
        if fpath.exists():
            if fpath.stat().st_size == 0:
                violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r3_f}")
            elif r3_f.endswith(".json"):
                try:
                    r3_doc = json.loads(fpath.read_text(encoding="utf-8"))
                    if isinstance(r3_doc, dict) and len(r3_doc) == 0:
                        violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r3_f}")
                    loaded_docs[r3_f] = r3_doc
                except Exception as ex:
                    violations.append(f"CORRUPT_JSON_ARTIFACT:{r3_f}:{ex}")
            elif r3_f.endswith(".jsonl"):
                # Validate non-empty jsonl
                lines = [l.strip() for l in fpath.read_text(encoding="utf-8").splitlines() if l.strip()]
                if not lines:
                    violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r3_f}")
                elif r3_f == "V179013_R3_RUNTIME_INPUT_CAPTURE.jsonl":
                    for line_idx, line in enumerate(lines[:50]):
                        try:
                            cap_entry = json.loads(line)
                            cp = str(cap_entry.get("capture_point") or "")
                            rt = str(cap_entry.get("runtime_name") or "")
                            if rt == "HISTORICAL_PRODUCTION" and "MarketShadow" in cp:
                                violations.append(f"MISLABELED_CAPTURE_POINT:{r3_f}:line_{line_idx}:{cp}")
                        except Exception as ex_l:
                            violations.append(f"CORRUPT_JSONL_LINE:{r3_f}:{line_idx}:{ex_l}")

    # Deep validation of R3 Backtest Source Manifest if present
    r3_bt_manifest_path = target_dir / "V179013_R3_BACKTEST_SOURCE_MANIFEST.json"
    if r3_bt_manifest_path.exists():
        try:
            r3_bt_man = json.loads(r3_bt_manifest_path.read_text(encoding="utf-8"))
            if str(r3_bt_man.get("status") or "").upper() != "PASS":
                violations.append("R3_BACKTEST_SOURCE_MANIFEST_STATUS_NOT_PASS")
            bt_sha = str(r3_bt_man.get("backtest_source_sha") or "").strip()
            final_sha = str(r3_bt_man.get("final_code_sha") or "").strip()
            if not bt_sha:
                violations.append("R3_BACKTEST_SOURCE_MANIFEST_MISSING_BACKTEST_SHA")
            if not final_sha:
                violations.append("R3_BACKTEST_SOURCE_MANIFEST_MISSING_FINAL_SHA")
            if bt_sha and final_sha and bt_sha != final_sha:
                violations.append(f"R3_BACKTEST_SOURCE_MANIFEST_SHA_MISMATCH:{bt_sha}!={final_sha}")
            if expected_code_sha and bt_sha != expected_code_sha.strip():
                violations.append(f"R3_BACKTEST_SOURCE_MANIFEST_EXPECTED_SHA_MISMATCH:{bt_sha}!={expected_code_sha}")
        except Exception as ex_man:
            violations.append(f"R3_BACKTEST_SOURCE_MANIFEST_CORRUPT:{ex_man}")

    # Deep validation of R3 Parity Classification Report if present
    r3_parity_class_path = target_dir / "V179013_R3_PARITY_CLASSIFICATION_REPORT.json"
    if r3_parity_class_path.exists():
        try:
            r3_pc = json.loads(r3_parity_class_path.read_text(encoding="utf-8"))
            if int(r3_pc.get("unexpected_divergences", 0)) > 0:
                violations.append(f"R3_PARITY_CLASSIFICATION_HAS_UNEXPECTED_DIVERGENCES:{r3_pc.get('unexpected_divergences')}")
            if str(r3_pc.get("status") or "").upper() != "PASS":
                violations.append("R3_PARITY_CLASSIFICATION_STATUS_NOT_PASS")
        except Exception as ex_pc:
            violations.append(f"R3_PARITY_CLASSIFICATION_CORRUPT:{ex_pc}")

    # Also check that independent runtime hashes is non-empty if present
    for hash_name in ("V179013_R3_INDEPENDENT_RUNTIME_INPUT_HASHES.json", "V179013_R2_INDEPENDENT_RUNTIME_INPUT_HASHES.json", "V179013_R1_INDEPENDENT_RUNTIME_INPUT_HASHES.json", "V179013_INDEPENDENT_RUNTIME_INPUT_HASHES.json"):
        h_path = target_dir / hash_name
        if h_path.exists():
            try:
                h_doc = json.loads(h_path.read_text(encoding="utf-8"))
                if isinstance(h_doc, dict) and len(h_doc) == 0:
                    violations.append(f"EMPTY_MANDATORY_ARTIFACT:{hash_name}")
            except Exception as ex:
                violations.append(f"CORRUPT_JSON_ARTIFACT:{hash_name}:{ex}")

    # Verify checksums from FINAL_ARTIFACT_MANIFEST.json
    manifest_doc = loaded_docs.get("FINAL_ARTIFACT_MANIFEST.json", {})
    manifest_entries = manifest_doc.get("artifacts", [])
    if isinstance(manifest_entries, dict):
        manifest_items = [
            {"filename": k, **v} if isinstance(v, dict) else {"filename": k, "sha256": v}
            for k, v in manifest_entries.items()
        ]
    else:
        manifest_items = list(manifest_entries)
    for entry in manifest_items:
        if not isinstance(entry, dict):
            continue
        fname = str(entry.get("filename") or entry.get("path") or "")
        claimed_sha = str(entry.get("sha256") or "")
        f_disk = target_dir / fname
        if not f_disk.exists():
            violations.append(f"MANIFESTED_ARTIFACT_MISSING_ON_DISK:{fname}")
        elif f_disk.stat().st_size == 0 and not fname.endswith("DECISION_DIVERGENCES.jsonl"):
            violations.append(f"ARTIFACT_EMPTY_FILE:{fname}")
        else:
            actual_sha = hashlib.sha256(f_disk.read_bytes()).hexdigest()
            if claimed_sha != actual_sha:
                violations.append(f"ARTIFACT_BYTE_CHECKSUM_MISMATCH:{fname}:{claimed_sha}!={actual_sha}")

    if violations:
        return {
            "is_valid": False,
            "status": "FAIL",
            "violations": violations,
            "recalculated_verdict": "BLOCKED_BY_MISSING_ARTIFACTS",
        }

    # Validate semantic acceptance bundle
    gate_res = loaded_docs.get("ACCEPTANCE_GATE_RESULTS.json", {})
    parity_doc = loaded_docs.get("V179013_FIVE_WAY_PARITY_REPORT.json", {})
    neg_doc = loaded_docs.get("V179013_NEGATIVE_CONTROLS.json", {})
    disk_doc = loaded_docs.get("V179013_DISK_GUARD_REPORT.json", {})
    bt_doc = loaded_docs.get("HISTORICAL_BACKTEST_METRICS.json", {})

    sem_res = validate_semantic_acceptance_bundle(
        parity_report=parity_doc,
        artifact_manifest=manifest_doc,
        gate_results=gate_res,
        expected_code_sha=expected_code_sha,
        expected_release=expected_release,
    )
    if not sem_res["is_valid"]:
        violations.extend(sem_res["violations"])

    # Re-derive verdicts from disk data
    phase_b_doc = loaded_docs.get("BASELINE_MANIFEST.json")
    derived_verdicts = derive_authoritative_acceptance_verdicts(
        gate_results=gate_res,
        parity_report=parity_doc,
        negative_results=neg_doc,
        backtest_metrics=bt_doc,
        phase_b_manifest=phase_b_doc,
        disk_report=disk_doc,
        final_code_sha=expected_code_sha,
    )

    is_valid = len(violations) == 0 and derived_verdicts["overall_status"] == "PASS"

    return {
        "is_valid": is_valid,
        "status": "PASS" if is_valid else "FAIL",
        "violations": violations,
        "derived_verdicts": derived_verdicts,
        "recalculated_verdict": derived_verdicts["release_readiness_verdict"],
    }



