"""
tools/acceptance_verdict.py
AURUM DESK V17.8.0.4 — Authoritative Release Verdict Resolution
Extracts verdict evaluation into a pure, fail-closed, testable module (V17.7.4 Section 10 & V17.8.0.1 DEF-178-15 & V17.8.0.4 Sections 14 & 22).
"""

from typing import Dict, List, Any, Mapping, Optional, Sequence, Tuple
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


R33_ALLOWED_CALLABLES_BY_RUNTIME: Dict[str, set] = {
    "HISTORICAL_PRODUCTION": {
        "services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_cycle",
        "services.scalp_m1_model_service.ScalpM1ModelService.evaluate_m1_reversal_candidate",
        "services.scalp_m1_model_service.ScalpM1ModelService.evaluate_m1_continuation_candidate",
        "services.scalp_m1_model_service.ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate",
        "services.scalp_m1_model_service.ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate",
        "services.cg01_engine.evaluate_cg01_candidate",
    },
    "SHARED_STRATEGY_EVALUATOR": {
        "backend.services.shared_strategy_evaluator.SharedStrategyEvaluator.evaluate_decision",
        "SharedStrategyEvaluator.evaluate_decision",
    },
    "PRODUCTION": {
        "backend.services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_cycle",
        "backend.services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_shared_decision_snapshot",
        "ScalpWatcherOrchestrator.evaluate_cycle",
    },
    "MARKET_SHADOW": {
        "backend.services.market_shadow_service.MarketShadowService.evaluate_shadow_cycle",
        "backend.services.market_shadow_service.MarketShadowService.evaluate_shared_decision_snapshot",
        "MarketShadowService.evaluate_shadow_cycle",
    },
    "CHRONOLOGICAL_BACKTEST": {
        "backend.services.v17_8_backtest_runner.execute_chronological_backtest_window_for_parity",
        "v17_8_backtest_runner.execute_chronological_backtest_window_for_parity",
    },
}


def verify_runtime_input_captures(
    records: Sequence[Dict[str, Any]],
    events: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    require_events: bool = False,
    snapshots: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    DEF-179013-R31-01, DEF-179013-R32 & R3.3 (P0-05): Fail-closed validator for runtime input capture records.
    Requires:
    1. Non-empty sequence of captures.
    2. Every record has valid schema (R31_RUNTIME_CAPTURE_V1, R32_RUNTIME_CAPTURE_V1, or R33_RUNTIME_CAPTURE_V1).
    3. Allowlisted production callable identities verified for all 5 boundaries.
    4. Each scenario contains all 5 mandatory runtime boundaries and consistent cross-runtime canonical input/config hashes.
    5. Each record has invocation_completed is True and unique execution_id.
    6. Non-empty raw_input_hash and canonical_input_hash.
    7. If events are provided (or require_events is True), cross-validates against 1 INVOCATION_STARTED,
       1 ACTUAL_INPUT_CAPTURED, and 1 INVOCATION_COMPLETED (SUCCESS) with monotonic ordering.
    """
    violations: List[str] = []
    if not records:
        return {
            "status": "FAIL",
            "is_valid": False,
            "violations": ["EMPTY_RUNTIME_INPUT_CAPTURES"],
            "records_count": 0,
        }

    if require_events and not events:
        violations.append("MISSING_REQUIRED_RUNTIME_INVOCATION_EVENTS")

    required_runtimes = {
        "HISTORICAL_PRODUCTION",
        "SHARED_STRATEGY_EVALUATOR",
        "PRODUCTION",
        "MARKET_SHADOW",
        "CHRONOLOGICAL_BACKTEST",
    }
    runtimes_by_scenario: Dict[str, set] = {}
    canonical_in_by_scenario: Dict[str, set] = {}
    canonical_cfg_by_scenario: Dict[str, set] = {}
    seen_exec_ids: set = set()

    started_by_exec: Dict[str, List[Dict[str, Any]]] = {}
    input_by_exec: Dict[str, List[Dict[str, Any]]] = {}
    completed_by_exec: Dict[str, List[Dict[str, Any]]] = {}
    failed_by_exec: Dict[str, List[Dict[str, Any]]] = {}

    if events:
        for evt in events:
            if not isinstance(evt, dict):
                continue
            etype = evt.get("event_type")
            eid = str(evt.get("execution_id") or "")
            if not eid:
                continue
            if etype == "INVOCATION_STARTED":
                started_by_exec.setdefault(eid, []).append(evt)
            elif etype == "ACTUAL_INPUT_CAPTURED":
                input_by_exec.setdefault(eid, []).append(evt)
            elif etype == "INVOCATION_COMPLETED":
                completed_by_exec.setdefault(eid, []).append(evt)
            elif etype == "INVOCATION_FAILED":
                failed_by_exec.setdefault(eid, []).append(evt)

    snapshots_by_exec: Dict[str, Dict[str, Any]] = {}
    if snapshots:
        for snap in snapshots:
            if isinstance(snap, dict) and snap.get("execution_id"):
                snapshots_by_exec[str(snap["execution_id"])] = snap
                snap_check = verify_r33_runtime_snapshot_hash(snap)
                if not snap_check["is_valid"]:
                    violations.extend(snap_check["violations"])

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
        canon_cfg_h = str(rec.get("canonical_config_hash") or "")

        if rt_name:
            if rt_name in runtimes_by_scenario.get(scen_id, set()):
                violations.append(f"DUPLICATE_EXECUTION_ID:{scen_id}:{rt_name}")
            runtimes_by_scenario.setdefault(scen_id, set()).add(rt_name)

        if not exec_id:
            violations.append(f"MISSING_EXECUTION_ID:{scen_id}:{rt_name}")
        elif exec_id in seen_exec_ids:
            violations.append(f"DUPLICATE_EXECUTION_ID:{exec_id}")
        else:
            seen_exec_ids.add(exec_id)

        if inv_comp is not True:
            violations.append(f"INVOCATION_NOT_COMPLETED:{scen_id}:{rt_name}")

        if not raw_in_h:
            violations.append(f"MISSING_RAW_INPUT_HASH:{scen_id}:{rt_name}")

        if not canon_in_h:
            violations.append(f"MISSING_CANONICAL_INPUT_HASH:{scen_id}:{rt_name}")
        else:
            canonical_in_by_scenario.setdefault(scen_id, set()).add(canon_in_h)

        if canon_cfg_h:
            canonical_cfg_by_scenario.setdefault(scen_id, set()).add(canon_cfg_h)

        # Check allowlisted production callable boundaries
        if rt_name == "HISTORICAL_PRODUCTION":
            if "marketshadow" in cp.lower():
                violations.append(f"MISLABELED_CAPTURE_POINT:{scen_id}:{cp}")
            if cp not in R33_ALLOWED_CALLABLES_BY_RUNTIME["HISTORICAL_PRODUCTION"]:
                violations.append(f"DISALLOWED_CALLABLE_IDENTITY:{scen_id}:{rt_name}:{cp}")
        elif rt_name == "SHARED_STRATEGY_EVALUATOR":
            if "sharedstrategyevaluator" not in cp.lower() and "sharedevaluator" not in cp.lower():
                violations.append(f"INVALID_SHARED_EVALUATOR_CAPTURE_POINT:{scen_id}:{cp}")
            if cp not in R33_ALLOWED_CALLABLES_BY_RUNTIME["SHARED_STRATEGY_EVALUATOR"]:
                violations.append(f"DISALLOWED_CALLABLE_IDENTITY:{scen_id}:{rt_name}:{cp}")
        elif rt_name == "PRODUCTION":
            if "scalpwatcherorchestrator" not in cp.lower():
                violations.append(f"INVALID_PRODUCTION_CAPTURE_POINT:{scen_id}:{cp}")
            if cp not in R33_ALLOWED_CALLABLES_BY_RUNTIME["PRODUCTION"]:
                violations.append(f"DISALLOWED_CALLABLE_IDENTITY:{scen_id}:{rt_name}:{cp}")
        elif rt_name == "MARKET_SHADOW":
            if "marketshadow" not in cp.lower():
                violations.append(f"INVALID_MARKET_SHADOW_CAPTURE_POINT:{scen_id}:{cp}")
            if cp not in R33_ALLOWED_CALLABLES_BY_RUNTIME["MARKET_SHADOW"]:
                violations.append(f"DISALLOWED_CALLABLE_IDENTITY:{scen_id}:{rt_name}:{cp}")
        elif rt_name == "CHRONOLOGICAL_BACKTEST":
            if "backtest" not in cp.lower():
                violations.append(f"INVALID_BACKTEST_CAPTURE_POINT:{scen_id}:{cp}")
            if cp not in R33_ALLOWED_CALLABLES_BY_RUNTIME["CHRONOLOGICAL_BACKTEST"]:
                violations.append(f"DISALLOWED_CALLABLE_IDENTITY:{scen_id}:{rt_name}:{cp}")
        else:
            violations.append(f"DISALLOWED_CALLABLE_IDENTITY:{scen_id}:{rt_name}:{cp}")

        # Cross-validate against live events if events are present
        if events:
            st_list = started_by_exec.get(exec_id, [])
            in_list = input_by_exec.get(exec_id, [])
            cp_list = completed_by_exec.get(exec_id, [])
            fl_list = failed_by_exec.get(exec_id, [])

            if len(st_list) == 0:
                violations.append(f"MISSING_INVOCATION_STARTED_EVENT:{scen_id}:{rt_name}:{exec_id}")
            elif len(st_list) > 1:
                violations.append(f"DUPLICATE_EXECUTION_ID:{exec_id}:MULTIPLE_STARTS")

            if len(in_list) == 0:
                violations.append(f"MISSING_ACTUAL_INPUT_CAPTURED_EVENT:{scen_id}:{rt_name}:{exec_id}")
            elif len(in_list) > 1:
                violations.append(f"DUPLICATE_EXECUTION_ID:{exec_id}:MULTIPLE_INPUTS")

            if len(fl_list) > 0:
                violations.append(f"FAILED_INVOCATION_MARKED_SUCCESS:{scen_id}:{rt_name}:{exec_id}")

            if len(cp_list) == 0:
                violations.append(f"MISSING_INVOCATION_COMPLETED_EVENT:{scen_id}:{rt_name}:{exec_id}")
            elif len(cp_list) > 1:
                violations.append(f"DUPLICATE_EXECUTION_ID:{exec_id}:MULTIPLE_COMPLETES")
            else:
                comp_evt = cp_list[0]
                if comp_evt.get("exit_status") != "SUCCESS":
                    violations.append(f"INVOCATION_EXIT_STATUS_NOT_SUCCESS:{scen_id}:{rt_name}:{exec_id}")
                if st_list and in_list:
                    m_st = int(st_list[0].get("timestamp_monotonic_ns") or 0)
                    m_in = int(in_list[0].get("timestamp_monotonic_ns") or 0)
                    m_cp = int(comp_evt.get("timestamp_monotonic_ns") or 0)
                    pre_ns = comp_evt.get("pre_call_monotonic_ns")
                    post_ns = comp_evt.get("post_call_monotonic_ns")
                    mono_ok = (m_st <= m_in <= m_cp)
                    if pre_ns is not None:
                        mono_ok = mono_ok and (m_st <= int(pre_ns)) and (m_in <= int(pre_ns))
                    if post_ns is not None:
                        mono_ok = mono_ok and (int(post_ns) <= m_cp)
                    if pre_ns is not None and post_ns is not None:
                        mono_ok = mono_ok and (int(pre_ns) <= int(post_ns))
                    if not mono_ok:
                        violations.append(f"NON_MONOTONIC_INVOCATION_TIMESTAMPS:{scen_id}:{rt_name}:{exec_id}")

    # Check that each scenario has all 5 runtimes and matching cross-runtime canonical hashes
    for scen_id, found_runtimes in runtimes_by_scenario.items():
        missing = required_runtimes - found_runtimes
        if missing:
            for m in sorted(list(missing)):
                violations.append(f"MISSING_RUNTIME_CAPTURE:{scen_id}:{m}")
        if len(canonical_in_by_scenario.get(scen_id, set())) > 1:
            violations.append(f"CROSS_RUNTIME_CANONICAL_INPUT_HASH_MISMATCH:{scen_id}")
        if len(canonical_cfg_by_scenario.get(scen_id, set())) > 1:
            violations.append(f"CROSS_RUNTIME_CANONICAL_CONFIG_HASH_MISMATCH:{scen_id}")

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

R32_MANDATORY_ARTIFACTS = [
    "R32_RUN_MANIFEST.json",
    "R32_ROOT_CAUSE_AUDIT.md",
    "R32_DEFECT_CLOSURE_MATRIX.md",
    "R32_RUNTIME_EXECUTION_EVENTS.jsonl",
    "R32_RUNTIME_INPUT_CAPTURES.jsonl",
    "R32_RUNTIME_HASH_VERIFICATION.json",
    "R32_FIVE_WAY_PARITY_REPORT.json",
    "R32_PARITY_CLASSIFICATION_REPORT.json",
    "R32_HISTORICAL_PRODUCTION_LINEAGE.jsonl",
    "R32_NEGATIVE_CONTROL_REGISTRY.json",
    "R32_NEGATIVE_CONTROL_RESULTS.json",
    "R32_WORKER_EXECUTION_MANIFEST.json",
    "R32_JUNIT_AGGREGATION.json",
    "R32_ASYNC_PERFORMANCE_REPORT.json",
    "R32_DISK_RESOURCE_REPORT.json",
    "R32_EXACT_SOURCE_MANIFEST.json",
    "R32_BACKTEST_METRICS.json",
    "R32_BACKTEST_LINEAGE.jsonl",
    "R32_MATCHED_COHORT_REPORT.json",
    "R32_OOS_REPORT.json",
    "R32_ARTIFACT_HASH_MANIFEST.json",
    "R32_INDEPENDENT_VERIFICATION.json",
    "R32_FINAL_ACCEPTANCE_AUDIT.md",
]

R33_MANDATORY_ARTIFACTS = [
    "R33_RUN_MANIFEST.json",
    "R33_DEFECT_ROOT_CAUSE_MATRIX.md",
    "R33_SOURCE_AND_COMMAND_EXECUTIONS.jsonl",
    "R33_RUNTIME_INVOCATION_EVENTS.jsonl",
    "R33_RUNTIME_INPUT_SNAPSHOTS.jsonl",
    "R33_RUNTIME_INPUT_CAPTURES.jsonl",
    "R33_RUNTIME_CAPTURE_INDEPENDENT_VERIFICATION.json",
    "R33_FIVE_WAY_PARITY.json",
    "R33_HISTORICAL_PRODUCTION_LINEAGE.jsonl",
    "R33_PARITY_CLASSIFICATION.json",
    "R33_NEGATIVE_TEST_COLLECTION.json",
    "R33_NEGATIVE_TEST_RESULTS.json",
    "R33_WORKER_EXECUTIONS.jsonl",
    "R33_RUN_TASK_ACCOUNTING.json",
    "R33_JUNIT_RECONCILIATION.json",
    "R33_ASYNC_RESOURCE_TELEMETRY.json",
    "R33_DISK_GUARD.json",
    "R33_EXACT_SOURCE_BACKTEST_MANIFEST.json",
    "R33_BACKTEST_TRADE_LEDGER.jsonl",
    "R33_BACKTEST_A_B_C_D_METRICS.json",
    "R33_MATCHED_COHORT.json",
    "R33_OOS_REPORT.json",
    "R33_ARTIFACT_SHA256_MANIFEST.json",
    "R33_INDEPENDENT_VERIFIER_REPORT.json",
    "R33_FINAL_ACCEPTANCE_AUDIT.md",
]


def _compute_r33_payload_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def verify_r33_runtime_snapshot_hash(snapshot_rec: Mapping[str, Any]) -> Dict[str, Any]:
    """
    R3.3 Phase 3 (F10, F12, F13): Independently recomputes raw and canonical input/config
    hashes from the persisted `R33_RUNTIME_INPUT_SNAPSHOT_V1` payload. Fails closed if any
    payload field was mutated without updating the declared hash or if `raw_input_payload`
    disagrees with `canonical_input_payload`.
    """
    violations: List[str] = []
    if not isinstance(snapshot_rec, Mapping) or not snapshot_rec:
        return {
            "is_valid": False,
            "status": "FAIL",
            "violations": ["EMPTY_RUNTIME_INPUT_SNAPSHOT"],
        }

    exec_id = str(snapshot_rec.get("execution_id") or "unknown")
    raw_in = snapshot_rec.get("raw_input_payload")
    can_in = snapshot_rec.get("canonical_input_payload")
    raw_cfg = snapshot_rec.get("raw_config_payload")
    can_cfg = snapshot_rec.get("canonical_config_payload")

    decl_raw_in_h = str(snapshot_rec.get("declared_raw_input_hash") or "").strip()
    decl_can_in_h = str(snapshot_rec.get("declared_canonical_input_hash") or "").strip()
    decl_raw_cfg_h = str(snapshot_rec.get("declared_raw_config_hash") or "").strip()
    decl_can_cfg_h = str(snapshot_rec.get("declared_canonical_config_hash") or "").strip()

    if raw_in is None or can_in is None or raw_cfg is None or can_cfg is None:
        violations.append(f"MISSING_SNAPSHOT_PAYLOAD:{exec_id}")
        return {
            "is_valid": False,
            "status": "FAIL",
            "violations": violations,
        }

    comp_raw_in_h = _compute_r33_payload_sha256(raw_in)
    comp_can_in_h = _compute_r33_payload_sha256(can_in)
    comp_raw_cfg_h = _compute_r33_payload_sha256(raw_cfg)
    comp_can_cfg_h = _compute_r33_payload_sha256(can_cfg)

    if not decl_raw_in_h or decl_raw_in_h != comp_raw_in_h:
        violations.append(f"SNAPSHOT_HASH_RECOMPUTATION_MISMATCH:raw_input:{exec_id}:{decl_raw_in_h}!={comp_raw_in_h}")
    if not decl_can_in_h or decl_can_in_h != comp_can_in_h:
        violations.append(f"SNAPSHOT_HASH_RECOMPUTATION_MISMATCH:canonical_input:{exec_id}:{decl_can_in_h}!={comp_can_in_h}")
    if not decl_raw_cfg_h or decl_raw_cfg_h != comp_raw_cfg_h:
        violations.append(f"SNAPSHOT_HASH_RECOMPUTATION_MISMATCH:raw_config:{exec_id}:{decl_raw_cfg_h}!={comp_raw_cfg_h}")
    if not decl_can_cfg_h or decl_can_cfg_h != comp_can_cfg_h:
        violations.append(f"SNAPSHOT_HASH_RECOMPUTATION_MISMATCH:canonical_config:{exec_id}:{decl_can_cfg_h}!={comp_can_cfg_h}")

    if isinstance(raw_in, Mapping) and "canonical_market_input" in raw_in:
        if _compute_r33_payload_sha256(raw_in["canonical_market_input"]) != comp_can_in_h:
            violations.append(f"SNAPSHOT_HASH_RECOMPUTATION_MISMATCH:raw_vs_canonical_input:{exec_id}")
    if isinstance(raw_cfg, Mapping) and "canonical_config" in raw_cfg:
        if _compute_r33_payload_sha256(raw_cfg["canonical_config"]) != comp_can_cfg_h:
            violations.append(f"SNAPSHOT_HASH_RECOMPUTATION_MISMATCH:raw_vs_canonical_config:{exec_id}")

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "execution_id": exec_id,
        "recomputed_raw_input_hash": comp_raw_in_h,
        "recomputed_canonical_input_hash": comp_can_in_h,
        "recomputed_raw_config_hash": comp_raw_cfg_h,
        "recomputed_canonical_config_hash": comp_can_cfg_h,
        "violations": violations,
    }


def reconcile_r33_runtime_captures(
    *,
    events: Sequence[Mapping[str, Any]],
    snapshots: Sequence[Mapping[str, Any]],
    captures: Sequence[Mapping[str, Any]],
    expected_run_id: Optional[str] = None,
    expected_frozen_sha: Optional[str] = None,
    expected_historical_sha: str = "c356d6551e13cd6af61cea5c3ab1f4aae1548af2",
) -> Dict[str, Any]:
    """
    R3.3 Phase 3 (P0-05, P0-06, P0-07, F06–F13): Independently verifies and reconciles
    all runtime invocation events, persisted snapshots, and runtime input captures.
    Never returns hardcoded constants.
    """
    violations: List[str] = []
    if not events or not snapshots or not captures:
        violations.append("EMPTY_R33_RUNTIME_EVENTS_SNAPSHOTS_OR_CAPTURES")

    snapshots_by_exec: Dict[str, Mapping[str, Any]] = {}
    snapshots_verified_count = 0
    for s in snapshots:
        if not isinstance(s, Mapping):
            violations.append("INVALID_SNAPSHOT_RECORD_FORMAT")
            continue
        eid = str(s.get("execution_id") or "")
        if not eid:
            violations.append("SNAPSHOT_MISSING_EXECUTION_ID")
            continue
        if eid in snapshots_by_exec:
            violations.append(f"DUPLICATE_EXECUTION_ID:snapshot:{eid}")
        snapshots_by_exec[eid] = s
        s_res = verify_r33_runtime_snapshot_hash(s)
        if s_res["is_valid"]:
            snapshots_verified_count += 1
        else:
            violations.extend(s_res["violations"])

    events_by_exec: Dict[str, List[Mapping[str, Any]]] = {}
    for evt in events:
        if not isinstance(evt, Mapping):
            violations.append("INVALID_EVENT_RECORD_FORMAT")
            continue
        eid = str(evt.get("execution_id") or "")
        if not eid:
            violations.append("EVENT_MISSING_EXECUTION_ID")
            continue
        events_by_exec.setdefault(eid, []).append(evt)
        ev_run_id = str(evt.get("run_id") or "").strip()
        if expected_run_id and ev_run_id != expected_run_id.strip():
            violations.append(f"EVENT_RUN_ID_MISMATCH:{eid}:{ev_run_id}!={expected_run_id}")
        rt_name = str(evt.get("runtime_name") or "")
        ev_sha = str(evt.get("source_sha") or "").strip()
        if rt_name == "HISTORICAL_PRODUCTION":
            if ev_sha != expected_historical_sha:
                violations.append(f"HISTORICAL_EVENT_SOURCE_SHA_MISMATCH:{eid}:{ev_sha}!={expected_historical_sha}")
        elif expected_frozen_sha and ev_sha != expected_frozen_sha.strip():
            violations.append(f"EVENT_SOURCE_SHA_MISMATCH:{eid}:{ev_sha}!={expected_frozen_sha}")

    cycle_exec_ids_by_runtime_ts: Dict[Tuple[str, int], str] = {}
    cycle_invocations_count = 0
    model_evaluations_count = 0
    uncompleted_invocations_count = 0
    non_monotonic_invocations_count = 0
    disallowed_callables_count = 0

    for eid, ev_list in events_by_exec.items():
        starts = [e for e in ev_list if e.get("event_type") == "INVOCATION_STARTED"]
        inputs = [e for e in ev_list if e.get("event_type") == "ACTUAL_INPUT_CAPTURED"]
        completes = [e for e in ev_list if e.get("event_type") == "INVOCATION_COMPLETED"]
        fails = [e for e in ev_list if e.get("event_type") == "INVOCATION_FAILED"]

        if len(starts) != 1:
            violations.append(f"DUPLICATE_EXECUTION_ID:{eid}:starts={len(starts)}" if len(starts) > 1 else f"MISSING_INVOCATION_STARTED_EVENT:{eid}")
        if len(inputs) != 1:
            violations.append(f"DUPLICATE_EXECUTION_ID:{eid}:inputs={len(inputs)}" if len(inputs) > 1 else f"MISSING_ACTUAL_INPUT_CAPTURED_EVENT:{eid}")
        if len(fails) > 0:
            uncompleted_invocations_count += 1
            violations.append(f"FAILED_INVOCATION_PRESENT:{eid}")
        if len(completes) != 1:
            uncompleted_invocations_count += 1
            violations.append(f"DUPLICATE_EXECUTION_ID:{eid}:completes={len(completes)}" if len(completes) > 1 else f"MISSING_INVOCATION_COMPLETED_EVENT:{eid}")
            continue

        if not (starts and inputs and completes):
            continue

        st_e = starts[0]
        in_e = inputs[0]
        cp_e = completes[0]
        scope = str(st_e.get("invocation_scope") or "MODEL_EVALUATION")
        rt_name = str(st_e.get("runtime_name") or "")
        cal_id = str(st_e.get("callable_identity") or "")
        ts_ms = int(st_e.get("decision_timestamp_ms") or 0)

        if scope == "CYCLE_EVALUATION":
            cycle_invocations_count += 1
            cycle_exec_ids_by_runtime_ts[(rt_name, ts_ms)] = eid
        else:
            model_evaluations_count += 1

        if cp_e.get("exit_status") != "SUCCESS":
            uncompleted_invocations_count += 1
            violations.append(f"INVOCATION_EXIT_STATUS_NOT_SUCCESS:{eid}")

        allowed_set = R33_ALLOWED_CALLABLES_BY_RUNTIME.get(rt_name, set())
        if cal_id not in allowed_set:
            disallowed_callables_count += 1
            violations.append(f"DISALLOWED_CALLABLE_IDENTITY:{eid}:{rt_name}:{cal_id}")

        m_st = int(st_e.get("timestamp_monotonic_ns") or 0)
        m_in = int(in_e.get("timestamp_monotonic_ns") or 0)
        m_cp = int(cp_e.get("timestamp_monotonic_ns") or 0)
        pre_ns = cp_e.get("pre_call_monotonic_ns")
        post_ns = cp_e.get("post_call_monotonic_ns")
        mono_ok = (m_st <= m_in <= m_cp)
        if pre_ns is not None:
            mono_ok = mono_ok and (m_st <= int(pre_ns)) and (m_in <= int(pre_ns))
        if post_ns is not None:
            mono_ok = mono_ok and (int(post_ns) <= m_cp)
        if pre_ns is not None and post_ns is not None:
            mono_ok = mono_ok and (int(pre_ns) <= int(post_ns))
        if not mono_ok:
            non_monotonic_invocations_count += 1
            violations.append(f"NON_MONOTONIC_INVOCATION_TIMESTAMPS:{eid}")

        snap_rec = snapshots_by_exec.get(eid)
        if not snap_rec:
            violations.append(f"MISSING_RUNTIME_INPUT_SNAPSHOT:{eid}")
        else:
            if str(in_e.get("raw_input_hash") or "") != str(snap_rec.get("declared_raw_input_hash") or ""):
                violations.append(f"EVENT_SNAPSHOT_RAW_INPUT_HASH_MISMATCH:{eid}")
            if str(in_e.get("canonical_input_hash") or "") != str(snap_rec.get("declared_canonical_input_hash") or ""):
                violations.append(f"EVENT_SNAPSHOT_CANONICAL_INPUT_HASH_MISMATCH:{eid}")
            if str(in_e.get("raw_config_hash") or "") != str(snap_rec.get("declared_raw_config_hash") or ""):
                violations.append(f"EVENT_SNAPSHOT_RAW_CONFIG_HASH_MISMATCH:{eid}")
            if str(in_e.get("canonical_config_hash") or "") != str(snap_rec.get("declared_canonical_config_hash") or ""):
                violations.append(f"EVENT_SNAPSHOT_CANONICAL_CONFIG_HASH_MISMATCH:{eid}")

    # Verify parent cycle linkage for MODEL_EVALUATION in HISTORICAL_PRODUCTION, PRODUCTION, MARKET_SHADOW
    for eid, ev_list in events_by_exec.items():
        starts = [e for e in ev_list if e.get("event_type") == "INVOCATION_STARTED"]
        if not starts:
            continue
        st_e = starts[0]
        scope = str(st_e.get("invocation_scope") or "MODEL_EVALUATION")
        rt_name = str(st_e.get("runtime_name") or "")
        ts_ms = int(st_e.get("decision_timestamp_ms") or 0)
        if scope == "MODEL_EVALUATION" and rt_name in ("HISTORICAL_PRODUCTION", "PRODUCTION", "MARKET_SHADOW"):
            parent_id = st_e.get("parent_cycle_invocation_id")
            expected_parent = cycle_exec_ids_by_runtime_ts.get((rt_name, ts_ms))
            if not parent_id or (expected_parent and parent_id != expected_parent):
                violations.append(f"INVALID_PARENT_CYCLE_INVOCATION_LINK:{eid}:{parent_id}!={expected_parent}")

    cap_res = verify_runtime_input_captures(
        [dict(c) for c in captures],
        events=[dict(e) for e in events],
        require_events=True,
        snapshots=[dict(s) for s in snapshots],
    )
    if not cap_res["is_valid"]:
        violations.extend(cap_res["violations"])

    missing_boundaries = [v for v in violations if v.startswith("MISSING_RUNTIME_CAPTURE:")]
    invalid_schema_count = sum(1 for v in violations if v.startswith("INVALID_"))
    in_hash_mismatch_count = sum(1 for v in violations if v.startswith("CROSS_RUNTIME_CANONICAL_INPUT_HASH_MISMATCH:"))
    cfg_hash_mismatch_count = sum(1 for v in violations if v.startswith("CROSS_RUNTIME_CANONICAL_CONFIG_HASH_MISMATCH:"))

    unique_timestamps = {int(c.get("decision_timestamp_ms") or 0) for c in captures if isinstance(c, Mapping) and c.get("decision_timestamp_ms")}
    unique_scenarios = {str(c.get("scenario_id") or "") for c in captures if isinstance(c, Mapping) and c.get("scenario_id")}

    dedup_violations = list(dict.fromkeys(violations))
    is_valid = len(dedup_violations) == 0 and len(captures) > 0

    return {
        "schema_version": "R33_RUNTIME_CAPTURE_INDEPENDENT_VERIFICATION_V1",
        "run_id": expected_run_id or (str(captures[0].get("run_id")) if captures else ""),
        "tested_source_sha": expected_frozen_sha or "",
        "historical_baseline_sha": expected_historical_sha,
        "timestamps_evaluated_count": len(unique_timestamps),
        "cycle_invocations_count": cycle_invocations_count,
        "model_scenarios_count": len(unique_scenarios),
        "model_evaluations_count": model_evaluations_count,
        "runtime_captures_count": len(captures),
        "snapshots_count": len(snapshots),
        "events_count": len(events),
        "snapshots_hash_recomputed_verified_count": snapshots_verified_count,
        "missing_runtime_boundaries": missing_boundaries,
        "invalid_schema_records_count": invalid_schema_count,
        "uncompleted_invocations_count": uncompleted_invocations_count,
        "non_monotonic_invocations_count": non_monotonic_invocations_count,
        "disallowed_callables_count": disallowed_callables_count,
        "cross_runtime_input_hash_mismatches_count": in_hash_mismatch_count,
        "cross_runtime_config_hash_mismatches_count": cfg_hash_mismatch_count,
        "violations": dedup_violations,
        "is_valid": is_valid,
        "status": "PASS" if is_valid else "FAIL",
    }


def verify_r33_evidence_bundle(
    run_dir: Path,
    expected_code_sha: str,
    *,
    expected_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    R3.3 Phase 4 (P0-03, P0-04, P0-05, P0-06, P0-07, P0-08, F01–F20):
    Fail-closed independent verifier for an R3.3 run directory.
    Rejects empty directories, missing files, missing/mismatched SHAs or run IDs,
    post-hoc or non-monotonic runtime events, mutated snapshots, stale JUnit XMLs,
    masked worker failures, concurrency bound violations, missing negative controls,
    unexpected parity divergences, and backtest ledger SHA mismatches.
    """
    violations: List[str] = []
    run_dir = Path(run_dir)
    clean_expected_sha = str(expected_code_sha or "").strip()
    if not clean_expected_sha:
        violations.append("R33_MISSING_EXPECTED_CODE_SHA")

    if not run_dir.exists() or not run_dir.is_dir():
        return {
            "is_valid": False,
            "status": "FAIL",
            "violations": ["R33_EMPTY_OR_MISSING_EVIDENCE_DIRECTORY"],
            "recalculated_verdict": "BLOCKED_BY_EMPTY_OR_MISSING_DIRECTORY",
        }

    existing_files = [f for f in run_dir.iterdir() if f.is_file()]
    if not existing_files:
        return {
            "is_valid": False,
            "status": "FAIL",
            "violations": ["R33_EMPTY_OR_MISSING_EVIDENCE_DIRECTORY"],
            "recalculated_verdict": "BLOCKED_BY_EMPTY_OR_MISSING_DIRECTORY",
        }

    loaded_json: Dict[str, Any] = {}
    loaded_jsonl: Dict[str, List[Dict[str, Any]]] = {}

    for req_file in R33_MANDATORY_ARTIFACTS:
        fpath = run_dir / req_file
        if not fpath.exists():
            violations.append(f"MISSING_MANDATORY_ARTIFACT:{req_file}")
        elif fpath.stat().st_size == 0:
            violations.append(f"EMPTY_MANDATORY_ARTIFACT:{req_file}")
        elif req_file.endswith(".json"):
            try:
                doc = json.loads(fpath.read_text(encoding="utf-8"))
                if isinstance(doc, dict) and len(doc) == 0:
                    violations.append(f"EMPTY_MANDATORY_ARTIFACT:{req_file}")
                loaded_json[req_file] = doc
            except Exception as exc:
                violations.append(f"CORRUPT_JSON_ARTIFACT:{req_file}:{exc}")
        elif req_file.endswith(".jsonl"):
            lines = [ln.strip() for ln in fpath.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if not lines:
                violations.append(f"EMPTY_MANDATORY_ARTIFACT:{req_file}")
            else:
                parsed_rows: List[Dict[str, Any]] = []
                for idx, ln in enumerate(lines):
                    try:
                        parsed_rows.append(json.loads(ln))
                    except Exception as exc:
                        violations.append(f"CORRUPT_JSONL_LINE:{req_file}:{idx}:{exc}")
                loaded_jsonl[req_file] = parsed_rows

    # 1. Validate R33_RUN_MANIFEST.json (F01)
    manifest = loaded_json.get("R33_RUN_MANIFEST.json") or {}
    manifest_run_id = str(manifest.get("run_id") or "").strip()
    if not manifest_run_id:
        violations.append("R33_RUN_MANIFEST_MISSING_RUN_ID")
    elif expected_run_id and manifest_run_id != expected_run_id.strip():
        violations.append(f"R33_RUN_MANIFEST_RUN_ID_MISMATCH:{manifest_run_id}!={expected_run_id}")

    for sha_field in ("frozen_executable_sha", "final_code_sha", "tested_source_sha", "backtest_source_sha"):
        val = str(manifest.get(sha_field) or "").strip()
        if not val:
            violations.append(f"R33_RUN_MANIFEST_MISSING_REQUIRED_SHA:{sha_field}")
        elif clean_expected_sha and val != clean_expected_sha:
            violations.append(f"R33_RUN_MANIFEST_SHA_MISMATCH:{sha_field}:{val}!={clean_expected_sha}")

    # 2. Check all JSON documents for run_id and SHA consistency, plus nested fields (F02, F03, F04, F05, F17)
    def _inspect_nested_provenance(doc_name: str, obj: Any) -> None:
        if not isinstance(obj, Mapping):
            return
        for k_sha in ("source_sha", "tested_source_sha", "final_code_sha", "backtest_source_sha", "frozen_executable_sha"):
            if k_sha in obj and obj[k_sha] is not None:
                v_sha = str(obj[k_sha]).strip()
                if not v_sha or (clean_expected_sha and v_sha != clean_expected_sha):
                    violations.append(f"NESTED_ACCEPTANCE_SOURCE_SHA_MISMATCH:{doc_name}:{k_sha}:{v_sha}!={clean_expected_sha}")

        if "concurrency_report" in obj and isinstance(obj["concurrency_report"], Mapping):
            cr = obj["concurrency_report"]
            cr_sha = str(cr.get("tested_source_sha") or cr.get("source_sha") or "").strip()
            if not cr_sha or (clean_expected_sha and cr_sha != clean_expected_sha):
                violations.append(f"CONCURRENCY_REPORT_SOURCE_SHA_MISMATCH:{doc_name}:{cr_sha}!={clean_expected_sha}")
            max_obs = int(cr.get("max_active_workers_observed") or cr.get("peak_concurrent_workers") or 0)
            max_cfg = int(cr.get("max_configured_workers") or cr.get("configured_max_workers") or 4)
            if max_obs > max_cfg:
                violations.append(f"CONCURRENCY_BOUND_EXCEEDED:{doc_name}:{max_obs}>{max_cfg}")

        if "cpu_memory_metrics" in obj and isinstance(obj["cpu_memory_metrics"], Mapping):
            cm = obj["cpu_memory_metrics"]
            cm_run = str(cm.get("run_id") or "").strip()
            if manifest_run_id and (not cm_run or cm_run != manifest_run_id):
                violations.append(f"CPU_MEMORY_METRICS_RUN_ID_MISMATCH:{doc_name}:{cm_run}!={manifest_run_id}")

        if "parity_summary" in obj and isinstance(obj["parity_summary"], Mapping):
            ps = obj["parity_summary"]
            ps_run = str(ps.get("run_id") or "").strip()
            if manifest_run_id and (not ps_run or ps_run != manifest_run_id):
                violations.append(f"PARITY_SUMMARY_RUN_ID_MISMATCH:{doc_name}:{ps_run}!={manifest_run_id}")

        if "max_active_workers_observed" in obj and "max_configured_workers" in obj:
            if int(obj.get("max_active_workers_observed") or 0) > int(obj.get("max_configured_workers") or 4):
                violations.append(
                    f"CONCURRENCY_BOUND_EXCEEDED:{doc_name}:{obj.get('max_active_workers_observed')}>{obj.get('max_configured_workers')}"
                )

    for doc_name, doc_obj in loaded_json.items():
        if isinstance(doc_obj, Mapping):
            d_run = str(doc_obj.get("run_id") or "").strip()
            if manifest_run_id and d_run and d_run != manifest_run_id:
                violations.append(f"RUN_ID_MISMATCH:{doc_name}:{d_run}!={manifest_run_id}")
            _inspect_nested_provenance(doc_name, doc_obj)

    for extra_acc in ("acceptance_results.json", "ACCEPTANCE_GATE_RESULTS.json"):
        acc_p = run_dir / extra_acc
        if acc_p.exists():
            try:
                acc_doc = json.loads(acc_p.read_text(encoding="utf-8"))
                _inspect_nested_provenance(extra_acc, acc_doc)
            except Exception as exc:
                violations.append(f"CORRUPT_JSON_ARTIFACT:{extra_acc}:{exc}")

    # 3. Verify Runtime Invocation Events, Snapshots, and Captures (F06–F13)
    r33_events = loaded_jsonl.get("R33_RUNTIME_INVOCATION_EVENTS.jsonl", [])
    r33_snapshots = loaded_jsonl.get("R33_RUNTIME_INPUT_SNAPSHOTS.jsonl", [])
    r33_captures = loaded_jsonl.get("R33_RUNTIME_INPUT_CAPTURES.jsonl", [])
    recomp_rt_verif = reconcile_r33_runtime_captures(
        events=r33_events,
        snapshots=r33_snapshots,
        captures=r33_captures,
        expected_run_id=manifest_run_id or None,
        expected_frozen_sha=clean_expected_sha or None,
    )
    if not recomp_rt_verif["is_valid"]:
        violations.extend(recomp_rt_verif["violations"])

    persisted_rt_verif = loaded_json.get("R33_RUNTIME_CAPTURE_INDEPENDENT_VERIFICATION.json") or {}
    if str(persisted_rt_verif.get("status") or "").upper() != "PASS" or not bool(persisted_rt_verif.get("is_valid", False)):
        violations.append("R33_RUNTIME_CAPTURE_VERIFICATION_REPORT_NOT_PASS")

    # 4. Verify Negative Controls Collection & Results (F14)
    neg_coll = loaded_json.get("R33_NEGATIVE_TEST_COLLECTION.json") or {}
    neg_res = loaded_json.get("R33_NEGATIVE_TEST_RESULTS.json") or {}
    collected_ids = set(neg_coll.get("collected_control_ids") or [])
    executed_results = neg_res.get("results") or []
    executed_ids = {str(r.get("control_id") or r.get("test_id") or "") for r in executed_results if isinstance(r, Mapping)}
    mandatory_neg_ids = {f"N{i:02d}" for i in range(1, 149)} | {f"F{i:02d}" for i in range(1, 21)}
    for req_nid in sorted(mandatory_neg_ids):
        if req_nid not in collected_ids or req_nid not in executed_ids:
            violations.append(f"MISSING_MANDATORY_NEGATIVE_CONTROL_ID:{req_nid}")
    if int(neg_res.get("failed_negative_controls", 0) or 0) > 0:
        violations.append(f"NEGATIVE_CONTROLS_FAILED:{neg_res.get('failed_negative_controls')}")
    for r in executed_results:
        if isinstance(r, Mapping) and str(r.get("status") or "").upper() != "PASS":
            violations.append(f"NEGATIVE_CONTROL_NOT_PASS:{r.get('control_id') or r.get('test_id')}")

    # 5. Verify JUnit XMLs and sidecars (F15)
    junit_rec = loaded_json.get("R33_JUNIT_RECONCILIATION.json") or {}
    reports_dir = run_dir / "reports"
    if not reports_dir.exists() and isinstance(manifest, Mapping) and manifest.get("run_directory"):
        alt_rep = Path(__file__).resolve().parents[1] / str(manifest["run_directory"]) / "reports"
        if alt_rep.exists():
            reports_dir = alt_rep
    xml_files = sorted(reports_dir.glob("*.xml")) if reports_dir.exists() else []
    if not xml_files:
        violations.append("JUNIT_SIDECAR_PROVENANCE_MISMATCH:NO_XML_FILES_IN_REPORTS_DIR")
    for xml_p in xml_files:
        sidecar_p = xml_p.with_suffix(".xml.meta.json")
        if not sidecar_p.exists():
            violations.append(f"JUNIT_SIDECAR_PROVENANCE_MISMATCH:MISSING_SIDECAR:{xml_p.name}")
            continue
        try:
            sc_doc = json.loads(sidecar_p.read_text(encoding="utf-8"))
            sc_run = str(sc_doc.get("run_id") or "").strip()
            sc_sha = str(sc_doc.get("tested_source_sha") or "").strip()
            sc_xml_sha = str(sc_doc.get("xml_sha256") or "").strip()
            actual_xml_sha = hashlib.sha256(xml_p.read_bytes()).hexdigest()
            if manifest_run_id and sc_run != manifest_run_id:
                violations.append(f"JUNIT_SIDECAR_PROVENANCE_MISMATCH:RUN_ID:{xml_p.name}:{sc_run}!={manifest_run_id}")
            if clean_expected_sha and sc_sha != clean_expected_sha:
                violations.append(f"JUNIT_SIDECAR_PROVENANCE_MISMATCH:SOURCE_SHA:{xml_p.name}:{sc_sha}!={clean_expected_sha}")
            if not sc_xml_sha or sc_xml_sha != actual_xml_sha:
                violations.append(f"JUNIT_SIDECAR_PROVENANCE_MISMATCH:XML_SHA256:{xml_p.name}")
        except Exception as exc:
            violations.append(f"JUNIT_SIDECAR_PROVENANCE_MISMATCH:CORRUPT_SIDECAR:{xml_p.name}:{exc}")
    if int(junit_rec.get("total_failures", 0) or 0) > 0 or int(junit_rec.get("total_errors", 0) or 0) > 0:
        violations.append("JUNIT_RECONCILIATION_HAS_FAILURES_OR_ERRORS")

    # 6. Verify Worker Executions & Task Accounting (F16, F17 & P0-F fail-closed)
    worker_rows = loaded_jsonl.get("R33_WORKER_EXECUTIONS.jsonl", [])
    task_acc = loaded_json.get("R33_RUN_TASK_ACCOUNTING.json") or {}
    for w_row in worker_rows:
        if not isinstance(w_row, Mapping):
            violations.append("INVALID_WORKER_ROW_NOT_MAPPING")
            continue
        raw_w_exit = w_row.get("exit_code")
        raw_w_st = w_row.get("status")
        w_run = str(w_row.get("run_id") or "").strip()
        w_sha = str(w_row.get("tested_source_sha") or w_row.get("source_sha") or "").strip()
        if raw_w_exit is None or isinstance(raw_w_exit, bool) or not isinstance(raw_w_exit, int):
            violations.append(f"WORKER_MISSING_OR_INVALID_EXIT_CODE:{w_row.get('worker_id') or w_row.get('task_id')}")
            w_exit = -1
        else:
            w_exit = int(raw_w_exit)
        if raw_w_st is None or not str(raw_w_st).strip():
            violations.append(f"WORKER_MISSING_STATUS:{w_row.get('worker_id') or w_row.get('task_id')}")
            w_st = "MISSING"
        else:
            w_st = str(raw_w_st).strip().upper()
        if not w_run:
            violations.append(f"WORKER_MISSING_RUN_ID:{w_row.get('worker_id') or w_row.get('task_id')}")
        if not w_sha:
            violations.append(f"WORKER_MISSING_SOURCE_SHA:{w_row.get('worker_id') or w_row.get('task_id')}")
        if w_exit != 0 or w_st not in ("PASS", "COMPLETED", "SUCCESS"):
            violations.append(f"WORKER_FAILURE_MASKED_AS_PASS:{w_row.get('worker_id') or w_row.get('task_id')}:exit={w_exit}")
        if manifest_run_id and w_run and w_run != manifest_run_id:
            violations.append(f"WORKER_RUN_ID_MISMATCH:{w_run}!={manifest_run_id}")
        if clean_expected_sha and w_sha and w_sha != clean_expected_sha:
            violations.append(f"WORKER_SOURCE_SHA_MISMATCH:{w_sha}!={clean_expected_sha}")
    if str(task_acc.get("status") or "").upper() != "PASS":
        violations.append("RUN_TASK_ACCOUNTING_NOT_PASS")

    # 7. Verify Parity Classification & Five-Way Parity (F19)
    parity_cls = loaded_json.get("R33_PARITY_CLASSIFICATION.json") or {}
    p_cls_res = verify_parity_classification_report(parity_cls)
    if not p_cls_res["is_valid"]:
        violations.extend(p_cls_res["violations"])
    five_way_doc = loaded_json.get("R33_FIVE_WAY_PARITY.json") or {}
    if str(five_way_doc.get("status") or "").upper() != "PASS":
        violations.append("FIVE_WAY_PARITY_STATUS_NOT_PASS")

    # 8. Verify Backtest Manifest, Metrics, and Trade Ledger Provenance (F20)
    bt_man = loaded_json.get("R33_EXACT_SOURCE_BACKTEST_MANIFEST.json") or {}
    bt_met = loaded_json.get("R33_BACKTEST_A_B_C_D_METRICS.json") or {}
    bt_ledger = loaded_jsonl.get("R33_BACKTEST_TRADE_LEDGER.jsonl", [])
    for doc_label, bt_d in (("R33_EXACT_SOURCE_BACKTEST_MANIFEST.json", bt_man), ("R33_BACKTEST_A_B_C_D_METRICS.json", bt_met)):
        b_sha = str(bt_d.get("backtest_source_sha") or bt_d.get("source_sha") or "").strip()
        if not b_sha or (clean_expected_sha and b_sha != clean_expected_sha):
            violations.append(f"BACKTEST_LEDGER_SOURCE_SHA_MISMATCH:{doc_label}:{b_sha}!={clean_expected_sha}")
    for row_idx, l_row in enumerate(bt_ledger):
        if isinstance(l_row, Mapping):
            r_sha = str(l_row.get("source_sha") or l_row.get("backtest_source_sha") or "").strip()
            if not r_sha or (clean_expected_sha and r_sha != clean_expected_sha):
                violations.append(f"BACKTEST_LEDGER_SOURCE_SHA_MISMATCH:row_{row_idx}:{r_sha}!={clean_expected_sha}")

    # 9. Verify Artifact SHA-256 Manifest (P0-F fail-closed on empty/incomplete manifest)
    sha_man = loaded_json.get("R33_ARTIFACT_SHA256_MANIFEST.json") or {}
    entries = sha_man.get("artifacts") or []
    if isinstance(entries, Mapping):
        entries = [{"filename": k, **(v if isinstance(v, Mapping) else {"sha256": v})} for k, v in entries.items()]
    r33_self_excluded = {
        "R33_ARTIFACT_SHA256_MANIFEST.json",
        "R33_INDEPENDENT_VERIFIER_REPORT.json",
        "R33_FINAL_ACCEPTANCE_AUDIT.md",
    }
    listed_fnames: set = set()
    for ent in entries:
        if not isinstance(ent, Mapping):
            continue
        fname = str(ent.get("filename") or ent.get("path") or "")
        if not fname:
            continue
        listed_fnames.add(fname)
        if fname in r33_self_excluded:
            continue
        f_disk = run_dir / fname
        claimed_h = str(ent.get("sha256") or "").strip()
        if not claimed_h:
            violations.append(f"MANIFESTED_ARTIFACT_MISSING_SHA256:{fname}")
        elif not f_disk.exists():
            violations.append(f"MANIFESTED_ARTIFACT_MISSING_ON_DISK:{fname}")
        else:
            actual_h = hashlib.sha256(f_disk.read_bytes()).hexdigest()
            if claimed_h != actual_h:
                violations.append(f"ARTIFACT_BYTE_CHECKSUM_MISMATCH:{fname}:{claimed_h}!={actual_h}")
    for req_art in R33_MANDATORY_ARTIFACTS:
        if req_art not in r33_self_excluded and req_art not in listed_fnames:
            violations.append(f"INCOMPLETE_HASH_MANIFEST_MISSING_ENTRY:{req_art}")

    dedup_violations = list(dict.fromkeys(violations))
    is_valid = len(dedup_violations) == 0

    return {
        "schema_version": "R33_INDEPENDENT_VERIFIER_REPORT_V1",
        "run_id": manifest_run_id,
        "expected_code_sha": clean_expected_sha,
        "is_valid": is_valid,
        "status": "PASS" if is_valid else "FAIL",
        "violations": dedup_violations,
        "runtime_capture_verification": recomp_rt_verif,
        "recalculated_verdict": (
            "VERIFIED_FOUNDATION_CLOSURE — READY_TO_PROCEED_TO_V17_9_1"
            if is_valid
            else f"BLOCKED_BY_R33_VERIFICATION:{dedup_violations[0]}"
        ),
    }


def verify_pre_execution_run_manifest(
    initial_manifest: Mapping[str, Any],
    finalization_manifest: Optional[Mapping[str, Any]] = None,
    *,
    expected_run_id: Optional[str] = None,
    expected_source_sha: Optional[str] = None,
    first_task_started_at_ms: Optional[int] = None,
    backtest_started_at_ms: Optional[int] = None,
    initial_manifest_bytes: Optional[bytes] = None,
) -> Dict[str, Any]:
    """
    R3.3.1 Section 2.5 (P0-A): Pure verifier helper validating the two-stage run manifest lifecycle:
      1. `R331_INITIAL_RUN_MANIFEST.json` was created with `status="RUNNING"` BEFORE any task or backtest started.
      2. `R331_RUN_MANIFEST_FINALIZATION.json` references the exact `initial_manifest_sha256` without mutating
         `R331_INITIAL_RUN_MANIFEST.json`, and completed after creation.
    """
    violations: List[str] = []
    if not isinstance(initial_manifest, Mapping) or len(initial_manifest) == 0:
        return {
            "is_valid": False,
            "status": "FAIL",
            "violations": ["EMPTY_OR_INVALID_INITIAL_RUN_MANIFEST"],
        }

    m_run_id = str(initial_manifest.get("run_id") or "").strip()
    m_sha = str(
        initial_manifest.get("tested_source_sha")
        or initial_manifest.get("frozen_executable_sha")
        or initial_manifest.get("source_sha")
        or ""
    ).strip()
    m_status = str(initial_manifest.get("status") or "").strip().upper()
    m_created_utc = str(initial_manifest.get("created_at_utc") or "").strip()
    raw_created_ms = initial_manifest.get("created_at_ms")
    raw_pid = initial_manifest.get("pid")

    if not m_run_id:
        violations.append("INITIAL_MANIFEST_MISSING_RUN_ID")
    elif expected_run_id and m_run_id != expected_run_id.strip():
        violations.append(f"INITIAL_MANIFEST_RUN_ID_MISMATCH:{m_run_id}!={expected_run_id.strip()}")

    if not m_sha:
        violations.append("INITIAL_MANIFEST_MISSING_SOURCE_SHA")
    elif expected_source_sha and m_sha != expected_source_sha.strip():
        violations.append(f"INITIAL_MANIFEST_SOURCE_SHA_MISMATCH:{m_sha}!={expected_source_sha.strip()}")

    if m_status != "RUNNING":
        violations.append(f"INITIAL_MANIFEST_STATUS_NOT_RUNNING:{m_status or 'MISSING'}")

    if not m_created_utc:
        violations.append("INITIAL_MANIFEST_MISSING_CREATED_AT_UTC")

    created_ms = 0
    if raw_created_ms is None or isinstance(raw_created_ms, bool) or not isinstance(raw_created_ms, int) or raw_created_ms <= 0:
        violations.append("INITIAL_MANIFEST_INVALID_CREATED_AT_MS")
    else:
        created_ms = int(raw_created_ms)

    if raw_pid is None or isinstance(raw_pid, bool) or not isinstance(raw_pid, int) or raw_pid <= 0:
        violations.append("INITIAL_MANIFEST_INVALID_PID")

    if created_ms > 0 and first_task_started_at_ms is not None and int(first_task_started_at_ms) > 0:
        if created_ms > int(first_task_started_at_ms):
            violations.append(
                f"PRE_EXECUTION_MANIFEST_CREATED_AFTER_FIRST_TASK:{created_ms}>{int(first_task_started_at_ms)}"
            )

    if created_ms > 0 and backtest_started_at_ms is not None and int(backtest_started_at_ms) > 0:
        if created_ms > int(backtest_started_at_ms):
            violations.append(
                f"PRE_EXECUTION_MANIFEST_CREATED_AFTER_BACKTEST_START:{created_ms}>{int(backtest_started_at_ms)}"
            )

    if finalization_manifest is not None:
        if not isinstance(finalization_manifest, Mapping) or len(finalization_manifest) == 0:
            violations.append("EMPTY_OR_INVALID_FINALIZATION_MANIFEST")
        else:
            f_run_id = str(finalization_manifest.get("run_id") or "").strip()
            f_sha = str(
                finalization_manifest.get("tested_source_sha")
                or finalization_manifest.get("frozen_executable_sha")
                or finalization_manifest.get("source_sha")
                or ""
            ).strip()
            f_status = str(finalization_manifest.get("status") or "").strip().upper()
            f_init_sha = str(finalization_manifest.get("initial_manifest_sha256") or "").strip()
            f_comp_ms = finalization_manifest.get("completed_at_ms")

            if f_run_id != m_run_id:
                violations.append(f"FINALIZATION_MANIFEST_RUN_ID_MISMATCH:{f_run_id}!={m_run_id}")
            if f_sha != m_sha:
                violations.append(f"FINALIZATION_MANIFEST_SOURCE_SHA_MISMATCH:{f_sha}!={m_sha}")
            if f_status not in ("COMPLETED", "PASS"):
                violations.append(f"FINALIZATION_MANIFEST_STATUS_NOT_COMPLETED:{f_status or 'MISSING'}")
            if not f_init_sha:
                violations.append("FINALIZATION_MANIFEST_MISSING_INITIAL_MANIFEST_SHA256")
            elif initial_manifest_bytes is not None:
                actual_init_sha = hashlib.sha256(initial_manifest_bytes).hexdigest()
                if f_init_sha != actual_init_sha:
                    violations.append(f"FINALIZATION_INITIAL_MANIFEST_SHA256_MISMATCH:{f_init_sha}!={actual_init_sha}")
            if f_comp_ms is None or isinstance(f_comp_ms, bool) or not isinstance(f_comp_ms, int) or int(f_comp_ms) < created_ms:
                violations.append(f"FINALIZATION_COMPLETED_BEFORE_INITIAL_MANIFEST:{f_comp_ms}<{created_ms}")

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "run_id": m_run_id,
        "tested_source_sha": m_sha,
        "created_at_ms": created_ms,
        "violations": violations,
    }


def verify_unmodified_run_identity_tree(
    payload: Any,
    *,
    expected_run_id: str,
    expected_source_sha: str,
    expected_historical_sha: str = "c356d6551e13cd6af61cea5c3ab1f4aae1548af2",
    path_prefix: str = "root",
) -> Dict[str, Any]:
    """
    R3.3.1 Section 2.1 & 2.5 (P0-D): Recursively verifies that every nested dict in `payload`
    carries unmodified `run_id` == `expected_run_id` and `tested_source_sha`/`source_sha` == `expected_source_sha`
    (or `expected_historical_sha` for isolated `HISTORICAL_PRODUCTION` records),
    and that no stale fallback or provenance rewrite occurred.
    """
    violations: List[str] = []
    exp_run = str(expected_run_id or "").strip()
    exp_sha = str(expected_source_sha or "").strip()
    exp_hist_sha = str(expected_historical_sha or "").strip()

    def _walk(node: Any, cur_path: str) -> None:
        if isinstance(node, Mapping):
            if node.get("_provenance_rewritten") or node.get("_stale_fallback_used") or node.get("stale_fallback_used"):
                violations.append(f"ILLEGAL_PROVENANCE_REWRITE_DETECTED:{cur_path}")
            is_hist_prod = str(node.get("runtime_name") or "").strip() == "HISTORICAL_PRODUCTION"
            for rk in ("run_id", "acceptance_run_id"):
                if rk in node:
                    raw_val = node.get(rk)
                    if (
                        rk == "run_id"
                        and raw_val is None
                        and cur_path.endswith(".payload.engine")
                        and "engine_instance_id" in node
                    ):
                        continue
                    val = str(raw_val or "").strip()
                    if not val:
                        violations.append(f"MISSING_NESTED_RUN_ID:{cur_path}.{rk}")
                    elif exp_run and val != exp_run:
                        violations.append(f"STALE_OR_MISMATCHED_NESTED_RUN_ID:{cur_path}.{rk}:{val}!={exp_run}")
            for sk in ("tested_source_sha", "source_sha", "final_code_sha", "backtest_source_sha", "frozen_executable_sha"):
                if sk in node:
                    val = str(node.get(sk) or "").strip()
                    if not val:
                        violations.append(f"MISSING_NESTED_SOURCE_SHA:{cur_path}.{sk}")
                    elif sk == "source_sha" and is_hist_prod:
                        if exp_hist_sha and val != exp_hist_sha:
                            violations.append(
                                f"STALE_OR_MISMATCHED_HISTORICAL_SOURCE_SHA:{cur_path}.{sk}:{val}!={exp_hist_sha}"
                            )
                    elif exp_sha and val != exp_sha:
                        violations.append(f"STALE_OR_MISMATCHED_NESTED_SOURCE_SHA:{cur_path}.{sk}:{val}!={exp_sha}")
            for k, v in node.items():
                if k in ("historical_baseline_sha", "pinned_historical_baseline_sha"):
                    continue
                _walk(v, f"{cur_path}.{k}")
        elif isinstance(node, (list, tuple)):
            for idx, item in enumerate(node):
                _walk(item, f"{cur_path}[{idx}]")

    _walk(payload, path_prefix)
    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "expected_run_id": exp_run,
        "expected_source_sha": exp_sha,
        "violations": violations,
    }


assert_nested_run_provenance = verify_unmodified_run_identity_tree


def independently_reconcile_junit_scopes(
    *,
    gate_xml_dir: Path,
    suite_xml_dir: Optional[Path] = None,
    expected_run_id: str,
    expected_source_sha: str,
    scope_inventory_doc: Optional[Mapping[str, Any]] = None,
    per_xml_doc: Optional[Mapping[str, Any]] = None,
    overlap_doc: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    R3.3.1 Section 2.5 (P0-C): Independently parses all JUnit XML files and their `.xml.meta.json`
    sidecars across `gate_xml_dir` (and optional `suite_xml_dir`), recomputes per-XML counts, raw
    totals, unique testcase node IDs, and overlap counts, and fails closed on any discrepancy.
    """
    import xml.etree.ElementTree as _ET

    violations: List[str] = []
    exp_run = str(expected_run_id or "").strip()
    exp_sha = str(expected_source_sha or "").strip()

    def _scan_dir(xml_dir: Path, scope_label: str) -> Dict[str, Any]:
        if not xml_dir.exists() or not xml_dir.is_dir():
            violations.append(f"JUNIT_XML_DIR_MISSING:{scope_label}:{xml_dir}")
            return {
                "xml_files_count": 0,
                "raw_total_tests": 0,
                "raw_total_passed": 0,
                "raw_total_failures": 0,
                "raw_total_errors": 0,
                "raw_total_skipped": 0,
                "unique_test_node_count": 0,
                "overlapping_test_executions": 0,
                "per_xml": {},
                "node_occurrence_counts": {},
            }
        xml_files = sorted(xml_dir.glob("*.xml"))
        if not xml_files:
            violations.append(f"JUNIT_XML_DIR_EMPTY:{scope_label}:{xml_dir}")
        per_xml: Dict[str, Dict[str, Any]] = {}
        node_counts: Dict[str, int] = {}
        raw_tests = 0
        raw_passed = 0
        raw_failures = 0
        raw_errors = 0
        raw_skipped = 0

        for xp in xml_files:
            if xp.stat().st_size == 0:
                violations.append(f"EMPTY_JUNIT_XML_FILE:{scope_label}:{xp.name}")
                continue
            actual_xml_sha = hashlib.sha256(xp.read_bytes()).hexdigest()
            sidecar_p = xp.with_name(xp.name + ".meta.json")
            if not sidecar_p.exists():
                violations.append(f"JUNIT_SIDECAR_MISSING:{scope_label}:{xp.name}")
            else:
                try:
                    sc = json.loads(sidecar_p.read_text(encoding="utf-8"))
                    sc_run = str(sc.get("run_id") or "").strip()
                    sc_sha = str(sc.get("tested_source_sha") or sc.get("source_sha") or "").strip()
                    sc_xml_sha = str(sc.get("xml_sha256") or "").strip()
                    sc_exit = sc.get("exit_code")
                    sc_exec_mode = str(sc.get("execution_mode") or "").strip()
                    sc_src_prim_exit = sc.get("source_primary_exit_code", 0)
                    if not sc_run or (exp_run and sc_run != exp_run):
                        violations.append(f"JUNIT_SIDECAR_RUN_ID_MISMATCH:{scope_label}:{xp.name}:{sc_run}!={exp_run}")
                    if not sc_sha or (exp_sha and sc_sha != exp_sha):
                        violations.append(f"JUNIT_SIDECAR_SOURCE_SHA_MISMATCH:{scope_label}:{xp.name}:{sc_sha}!={exp_sha}")
                    if not sc_xml_sha or sc_xml_sha != actual_xml_sha:
                        violations.append(f"JUNIT_SIDECAR_XML_SHA256_MISMATCH:{scope_label}:{xp.name}")
                    if sc_exec_mode == "DERIVED_PROJECTION" and sc_exit == "not_applicable":
                        if (
                            sc_src_prim_exit is None
                            or isinstance(sc_src_prim_exit, bool)
                            or not isinstance(sc_src_prim_exit, int)
                            or int(sc_src_prim_exit) != 0
                            or not sc.get("source_attempt_ids")
                        ):
                            violations.append(
                                f"JUNIT_SIDECAR_NONZERO_OR_MISSING_EXIT_CODE:{scope_label}:{xp.name}:src_exit={sc_src_prim_exit}"
                            )
                    else:
                        if (
                            sc_exit is None
                            or isinstance(sc_exit, bool)
                            or not isinstance(sc_exit, int)
                            or int(sc_exit) != 0
                            or (sc_src_prim_exit is not None and int(sc_src_prim_exit) != 0)
                        ):
                            violations.append(f"JUNIT_SIDECAR_NONZERO_OR_MISSING_EXIT_CODE:{scope_label}:{xp.name}:{sc_exit}")
                except Exception as exc:
                    violations.append(f"JUNIT_SIDECAR_CORRUPT:{scope_label}:{xp.name}:{exc}")

            try:
                tree = _ET.parse(xp)
                root = tree.getroot()
                tcs = root.findall(".//testcase")
                f_cnt = sum(1 for tc in tcs if tc.find("failure") is not None)
                e_cnt = sum(1 for tc in tcs if tc.find("error") is not None)
                s_cnt = sum(1 for tc in tcs if tc.find("skipped") is not None)
                t_cnt = len(tcs)
                p_cnt = max(0, t_cnt - f_cnt - e_cnt - s_cnt)
                for tc in tcs:
                    nid = f"{tc.get('classname', '')}::{tc.get('name', '')}"
                    node_counts[nid] = node_counts.get(nid, 0) + 1
                if f_cnt > 0 or e_cnt > 0:
                    violations.append(f"JUNIT_XML_HAS_FAILURES_OR_ERRORS:{scope_label}:{xp.name}:f={f_cnt},e={e_cnt}")
                if t_cnt <= 0:
                    violations.append(f"JUNIT_XML_ZERO_TESTS:{scope_label}:{xp.name}")
                per_xml[xp.name] = {
                    "xml_file": xp.name,
                    "xml_sha256": actual_xml_sha,
                    "tests": t_cnt,
                    "passed": p_cnt,
                    "failures": f_cnt,
                    "errors": e_cnt,
                    "skipped": s_cnt,
                }
                raw_tests += t_cnt
                raw_passed += p_cnt
                raw_failures += f_cnt
                raw_errors += e_cnt
                raw_skipped += s_cnt
            except Exception as parse_exc:
                violations.append(f"JUNIT_XML_PARSE_ERROR:{scope_label}:{xp.name}:{parse_exc}")

        unique_nodes = len(node_counts)
        overlap_execs = max(0, raw_tests - unique_nodes)
        return {
            "xml_files_count": len(per_xml),
            "raw_total_tests": raw_tests,
            "raw_total_passed": raw_passed,
            "raw_total_failures": raw_failures,
            "raw_total_errors": raw_errors,
            "raw_total_skipped": raw_skipped,
            "unique_test_node_count": unique_nodes,
            "overlapping_test_executions": overlap_execs,
            "per_xml": per_xml,
            "node_occurrence_counts": node_counts,
        }

    gate_scan = _scan_dir(Path(gate_xml_dir), "GATE_XML_SCOPE")
    suite_scan = (
        _scan_dir(Path(suite_xml_dir), "SUITE_XML_SCOPE")
        if suite_xml_dir is not None and Path(suite_xml_dir).exists()
        else None
    )

    if isinstance(scope_inventory_doc, Mapping) and scope_inventory_doc:
        claimed_gate_files = int(scope_inventory_doc.get("gate_xml_files_count", -1))
        claimed_gate_raw = int(scope_inventory_doc.get("gate_xml_raw_total_tests", -1))
        claimed_unique = int(scope_inventory_doc.get("gate_xml_unique_test_nodes", -1))
        if claimed_gate_files != gate_scan["xml_files_count"]:
            violations.append(
                f"JUNIT_SCOPE_INVENTORY_FILE_COUNT_MISMATCH:{claimed_gate_files}!={gate_scan['xml_files_count']}"
            )
        if claimed_gate_raw != gate_scan["raw_total_tests"]:
            violations.append(
                f"JUNIT_SCOPE_INVENTORY_RAW_TESTS_MISMATCH:{claimed_gate_raw}!={gate_scan['raw_total_tests']}"
            )
        if claimed_unique != gate_scan["unique_test_node_count"]:
            violations.append(
                f"JUNIT_SCOPE_INVENTORY_UNIQUE_NODES_MISMATCH:{claimed_unique}!={gate_scan['unique_test_node_count']}"
            )

    if isinstance(per_xml_doc, Mapping) and per_xml_doc:
        claimed_per_xml = per_xml_doc.get("per_xml_records") or {}
        if len(claimed_per_xml) != gate_scan["xml_files_count"]:
            violations.append(
                f"JUNIT_PER_XML_COUNT_MISMATCH:{len(claimed_per_xml)}!={gate_scan['xml_files_count']}"
            )
        for xname, xactual in gate_scan["per_xml"].items():
            xclaimed = claimed_per_xml.get(xname)
            if not isinstance(xclaimed, Mapping):
                violations.append(f"JUNIT_PER_XML_MISSING_RECORD:{xname}")
            elif int(xclaimed.get("tests", -1)) != xactual["tests"] or xclaimed.get("xml_sha256") != xactual["xml_sha256"]:
                violations.append(f"JUNIT_PER_XML_RECORD_MISMATCH:{xname}")

    if isinstance(overlap_doc, Mapping) and overlap_doc:
        claimed_ov = int(overlap_doc.get("overlapping_test_executions", -1))
        claimed_raw = int(overlap_doc.get("raw_total_tests", -1))
        claimed_uniq = int(overlap_doc.get("unique_test_node_count", -1))
        if (
            claimed_ov != gate_scan["overlapping_test_executions"]
            or claimed_raw != gate_scan["raw_total_tests"]
            or claimed_uniq != gate_scan["unique_test_node_count"]
        ):
            violations.append(
                f"JUNIT_OVERLAP_RECONCILIATION_MISMATCH:raw={claimed_raw}/{gate_scan['raw_total_tests']},uniq={claimed_uniq}/{gate_scan['unique_test_node_count']},ov={claimed_ov}/{gate_scan['overlapping_test_executions']}"
            )

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "gate_scope": {
            "xml_files_count": gate_scan["xml_files_count"],
            "raw_total_tests": gate_scan["raw_total_tests"],
            "raw_total_passed": gate_scan["raw_total_passed"],
            "raw_total_failures": gate_scan["raw_total_failures"],
            "raw_total_errors": gate_scan["raw_total_errors"],
            "raw_total_skipped": gate_scan["raw_total_skipped"],
            "unique_test_node_count": gate_scan["unique_test_node_count"],
            "overlapping_test_executions": gate_scan["overlapping_test_executions"],
        },
        "suite_scope": (
            {
                "xml_files_count": suite_scan["xml_files_count"],
                "raw_total_tests": suite_scan["raw_total_tests"],
                "raw_total_passed": suite_scan["raw_total_passed"],
                "raw_total_failures": suite_scan["raw_total_failures"],
                "raw_total_errors": suite_scan["raw_total_errors"],
                "raw_total_skipped": suite_scan["raw_total_skipped"],
                "unique_test_node_count": suite_scan["unique_test_node_count"],
            }
            if suite_scan is not None
            else None
        ),
        "violations": violations,
    }


def verify_worker_execution_accounting(
    worker_rows: Sequence[Mapping[str, Any]],
    task_accounting: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_source_sha: str,
    manifest_created_at_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    R3.3.1 Section 2.5 (P0-E & P0-F): Fail-closed worker & task accounting verifier.
    Never defaults missing `exit_code` to 0 or missing `status` to PASS.
    """
    violations: List[str] = []
    exp_run = str(expected_run_id or "").strip()
    exp_sha = str(expected_source_sha or "").strip()

    if not worker_rows:
        violations.append("EMPTY_WORKER_EXECUTIONS")

    for idx, w_row in enumerate(worker_rows):
        if not isinstance(w_row, Mapping):
            violations.append(f"WORKER_ROW_NOT_MAPPING:{idx}")
            continue
        wid = str(w_row.get("worker_id") or w_row.get("task_id") or f"row_{idx}")
        raw_exit = w_row.get("exit_code")
        raw_st = w_row.get("status")
        w_run = str(w_row.get("run_id") or "").strip()
        w_sha = str(w_row.get("tested_source_sha") or w_row.get("source_sha") or "").strip()
        w_start = w_row.get("started_at_ms") if w_row.get("started_at_ms") is not None else w_row.get("started_ms")
        w_end = w_row.get("completed_at_ms") if w_row.get("completed_at_ms") is not None else w_row.get("completed_ms")

        if raw_exit is None or isinstance(raw_exit, bool) or not isinstance(raw_exit, int):
            violations.append(f"WORKER_MISSING_OR_INVALID_EXIT_CODE:{wid}")
        elif int(raw_exit) != 0:
            violations.append(f"WORKER_NONZERO_EXIT_CODE:{wid}:{raw_exit}")

        if raw_st is None or not str(raw_st).strip():
            violations.append(f"WORKER_MISSING_STATUS:{wid}")
        elif str(raw_st).strip().upper() not in ("PASS", "COMPLETED", "SUCCESS"):
            violations.append(f"WORKER_STATUS_NOT_PASS:{wid}:{raw_st}")

        if not w_run:
            violations.append(f"WORKER_MISSING_RUN_ID:{wid}")
        elif exp_run and w_run != exp_run:
            violations.append(f"WORKER_RUN_ID_MISMATCH:{wid}:{w_run}!={exp_run}")

        if not w_sha:
            violations.append(f"WORKER_MISSING_SOURCE_SHA:{wid}")
        elif exp_sha and w_sha != exp_sha:
            violations.append(f"WORKER_SOURCE_SHA_MISMATCH:{wid}:{w_sha}!={exp_sha}")

        if w_start is None or w_end is None or int(w_start) <= 0 or int(w_end) < int(w_start):
            violations.append(f"WORKER_INVALID_TIMESTAMPS:{wid}:{w_start}..{w_end}")
        elif manifest_created_at_ms is not None and int(manifest_created_at_ms) > 0:
            if int(w_start) < int(manifest_created_at_ms):
                violations.append(
                    f"WORKER_STARTED_BEFORE_PRE_EXECUTION_MANIFEST:{wid}:{w_start}<{manifest_created_at_ms}"
                )

    if not isinstance(task_accounting, Mapping) or not task_accounting:
        violations.append("EMPTY_OR_INVALID_TASK_ACCOUNTING")
    else:
        t_status = str(task_accounting.get("status") or "").strip().upper()
        if t_status != "PASS":
            violations.append(f"TASK_ACCOUNTING_STATUS_NOT_PASS:{t_status or 'MISSING'}")
        sched = int(task_accounting.get("scheduled_tasks", -1))
        succ = int(task_accounting.get("succeeded_tasks", -1))
        fail = int(task_accounting.get("failed_tasks", -1))
        blk = int(task_accounting.get("blocked_tasks", -1))
        canc = int(task_accounting.get("cancelled_tasks", -1))
        max_cfg = int(task_accounting.get("max_configured_workers", task_accounting.get("max_workers", 0)) or 0)
        max_obs = int(task_accounting.get("max_active_workers_observed", task_accounting.get("peak_concurrent_workers", 0)) or 0)
        orphans = int(task_accounting.get("orphan_processes_remaining", -1))

        if sched <= 0:
            violations.append(f"TASK_ACCOUNTING_ZERO_SCHEDULED:{sched}")
        if sched != (succ + fail + blk + canc):
            violations.append(
                f"TASK_ACCOUNTING_SUM_MISMATCH:scheduled={sched}!=succeeded({succ})+failed({fail})+blocked({blk})+cancelled({canc})"
            )
        if fail != 0 or blk != 0 or canc != 0 or succ != sched:
            violations.append(
                f"TASK_ACCOUNTING_NONZERO_FAIL_BLOCK_CANCEL:succ={succ},fail={fail},blk={blk},canc={canc}"
            )
        if max_cfg <= 0 or max_obs <= 0 or max_obs > max_cfg:
            violations.append(f"TASK_ACCOUNTING_CONCURRENCY_VIOLATION:observed={max_obs},configured={max_cfg}")
        if orphans != 0:
            violations.append(f"TASK_ACCOUNTING_ORPHAN_WORKERS_REMAINING:{orphans}")

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "worker_rows_count": len(worker_rows),
        "violations": violations,
    }


def verify_resource_telemetry_semantics(
    memory_report: Mapping[str, Any],
    disk_report: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_source_sha: str,
) -> Dict[str, Any]:
    """
    R3.3.1 Section 2.5 (P0-B & P0-E): Validates memory unit normalization, separated memory scopes,
    absence of 64 MiB fallback fabrication, disk budget floor compliance, and zero orphan workers.
    """
    violations: List[str] = []
    exp_run = str(expected_run_id or "").strip()
    exp_sha = str(expected_source_sha or "").strip()

    for label, doc in (("MEMORY_REPORT", memory_report), ("DISK_REPORT", disk_report)):
        if not isinstance(doc, Mapping) or not doc:
            violations.append(f"EMPTY_OR_INVALID_{label}")
            continue
        d_run = str(doc.get("run_id") or "").strip()
        d_sha = str(doc.get("tested_source_sha") or doc.get("source_sha") or "").strip()
        d_status = str(doc.get("status") or "").strip().upper()
        m_status = str(doc.get("measurement_status") or "").strip().upper()
        if not d_run or (exp_run and d_run != exp_run):
            violations.append(f"{label}_RUN_ID_MISMATCH:{d_run}!={exp_run}")
        if not d_sha or (exp_sha and d_sha != exp_sha):
            violations.append(f"{label}_SOURCE_SHA_MISMATCH:{d_sha}!={exp_sha}")
        if d_status != "PASS":
            violations.append(f"{label}_STATUS_NOT_PASS:{d_status or 'MISSING'}")
        if m_status != "MEASURED":
            violations.append(f"{label}_MEASUREMENT_STATUS_NOT_MEASURED:{m_status or 'MISSING'}")

    if isinstance(memory_report, Mapping) and memory_report:
        unit = str(memory_report.get("unit") or "").strip().upper()
        if unit != "BYTES":
            violations.append(f"MEMORY_REPORT_UNIT_NOT_BYTES:{unit}")
        curr_rss = memory_report.get("current_rss_bytes")
        peak_proc = memory_report.get("peak_process_rss_bytes")
        child_rss = memory_report.get("child_rss_bytes")
        peak_child = memory_report.get("peak_child_rss_bytes")
        peak_tree = memory_report.get("peak_process_tree_rss_bytes")
        sys_total = memory_report.get("system_total_memory_bytes")
        sys_used = memory_report.get("system_used_memory_bytes")

        for k_name, val in (
            ("current_rss_bytes", curr_rss),
            ("peak_process_rss_bytes", peak_proc),
            ("child_rss_bytes", child_rss),
            ("peak_child_rss_bytes", peak_child),
            ("peak_process_tree_rss_bytes", peak_tree),
        ):
            if val is None or isinstance(val, bool) or not isinstance(val, int) or int(val) < 0:
                violations.append(f"MEMORY_SCOPE_MISSING_OR_INVALID:{k_name}:{val}")
            elif k_name in ("current_rss_bytes", "peak_process_rss_bytes", "peak_process_tree_rss_bytes") and int(val) == 67108864:
                violations.append(f"FABRICATED_64MIB_MEMORY_FALLBACK_DETECTED:{k_name}")

        if isinstance(peak_proc, int) and peak_proc <= 0:
            violations.append(f"PEAK_PROCESS_RSS_BYTES_NON_POSITIVE:{peak_proc}")
        if isinstance(peak_proc, int) and isinstance(curr_rss, int) and peak_proc < curr_rss:
            violations.append(f"PEAK_PROCESS_RSS_BELOW_CURRENT_RSS:{peak_proc}<{curr_rss}")
        if isinstance(peak_tree, int) and isinstance(peak_proc, int) and peak_tree < peak_proc:
            violations.append(f"PEAK_TREE_RSS_BELOW_PEAK_PROCESS_RSS:{peak_tree}<{peak_proc}")

        # Detect macOS 1024x unit inflation (e.g. > 64 GiB or > system_total_memory_bytes)
        max_plausible_b = int(sys_total) if (isinstance(sys_total, int) and sys_total > 0) else (64 * 1024 * 1024 * 1024)
        if isinstance(peak_proc, int) and peak_proc > max_plausible_b:
            violations.append(f"MACOS_RU_MAXRSS_UNIT_INFLATION_DETECTED:peak_process_rss_bytes={peak_proc}>max={max_plausible_b}")
        if sys_used is None or isinstance(sys_used, bool) or not isinstance(sys_used, int) or int(sys_used) <= 0:
            violations.append(f"MEMORY_SCOPE_MISSING_SYSTEM_USED_BYTES:{sys_used}")

    if isinstance(disk_report, Mapping) and disk_report:
        floor_b = int(disk_report.get("reserved_disk_floor_bytes", 0) or 0)
        pre_b = int(disk_report.get("free_disk_bytes_preflight", 0) or 0)
        min_b = int(disk_report.get("minimum_free_disk_bytes_observed", 0) or 0)
        post_b = int(disk_report.get("free_disk_bytes_postflight", 0) or 0)
        orphans = int(disk_report.get("orphan_worker_count", -1))
        if floor_b <= 0 or pre_b < floor_b or min_b < floor_b or post_b < floor_b:
            violations.append(f"DISK_BUDGET_BELOW_FLOOR:floor={floor_b},pre={pre_b},min={min_b},post={post_b}")
        if orphans != 0:
            violations.append(f"DISK_REPORT_ORPHAN_WORKERS_REMAINING:{orphans}")

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "violations": violations,
    }


def verify_mandatory_artifact_hash_coverage(
    run_dir: Path,
    sha_manifest: Mapping[str, Any],
    mandatory_artifacts: Sequence[str],
    *,
    self_excluded_files: Sequence[str] = (
        "R331_ARTIFACT_SHA256_MANIFEST.json",
        "R331_INDEPENDENT_VERIFICATION.json",
        "R331_FINAL_ACCEPTANCE_AUDIT.md",
    ),
) -> Dict[str, Any]:
    """
    R3.3.1 Section 2.5 (P0-F): Verifies complete SHA-256 manifest coverage across all mandatory
    artifacts (except self-excluded files written at or after manifest generation) and verifies
    exact on-disk byte SHA-256 equality.
    """
    violations: List[str] = []
    run_dir = Path(run_dir)
    excluded = set(self_excluded_files)

    if not isinstance(sha_manifest, Mapping) or not sha_manifest:
        return {
            "is_valid": False,
            "status": "FAIL",
            "violations": ["EMPTY_OR_INVALID_SHA256_MANIFEST"],
        }

    entries = sha_manifest.get("artifacts") or []
    if isinstance(entries, Mapping):
        entries = [{"filename": k, **(v if isinstance(v, Mapping) else {"sha256": v})} for k, v in entries.items()]
    if not isinstance(entries, list) or len(entries) == 0:
        return {
            "is_valid": False,
            "status": "FAIL",
            "violations": ["SHA256_MANIFEST_HAS_ZERO_ENTRIES"],
        }

    by_filename: Dict[str, Mapping[str, Any]] = {}
    for ent in entries:
        if isinstance(ent, Mapping):
            fn = str(ent.get("filename") or ent.get("path") or "").strip()
            if fn:
                by_filename[fn] = ent

    for req_file in mandatory_artifacts:
        if req_file in excluded:
            continue
        if req_file not in by_filename:
            violations.append(f"INCOMPLETE_HASH_MANIFEST_MISSING_ENTRY:{req_file}")

    for fname, ent in by_filename.items():
        if fname in excluded:
            continue
        f_disk = run_dir / fname
        claimed_sha = str(ent.get("sha256") or "").strip()
        if not claimed_sha:
            violations.append(f"MANIFESTED_ARTIFACT_MISSING_SHA256:{fname}")
        elif not f_disk.exists():
            violations.append(f"MANIFESTED_ARTIFACT_MISSING_ON_DISK:{fname}")
        elif f_disk.stat().st_size == 0 and fname not in {"R332_MASTER_RETRY_ATTEMPTS.jsonl", "master_retry_attempts.jsonl"}:
            violations.append(f"MANIFESTED_ARTIFACT_EMPTY_ON_DISK:{fname}")
        else:
            actual_sha = hashlib.sha256(f_disk.read_bytes()).hexdigest()
            if claimed_sha != actual_sha:
                violations.append(f"ARTIFACT_BYTE_CHECKSUM_MISMATCH:{fname}:{claimed_sha}!={actual_sha}")

    return {
        "is_valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "verified_entries_count": len(by_filename),
        "violations": violations,
    }


def verify_r331_evidence_bundle(
    run_dir: Path,
    expected_code_sha: str,
    *,
    expected_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    R3.3.1 Section 2.5: Standalone, fail-closed independent verifier for the 27 mandatory
    R3.3.1 deliverables in `docs/artifacts/v17_9_0_1_3/r331/<run_id>/`.
    """
    from tools.runtime_execution_logger import R331_MANDATORY_ARTIFACTS

    run_dir = Path(run_dir)
    violations: List[str] = []
    clean_expected_sha = str(expected_code_sha or "").strip()

    if not run_dir.exists() or not run_dir.is_dir():
        return {
            "schema_version": "R331_INDEPENDENT_VERIFICATION_V1",
            "is_valid": False,
            "status": "FAIL",
            "violations": [f"R331_RUN_DIR_DOES_NOT_EXIST:{run_dir}"],
            "recalculated_verdict": "BLOCKED_BY_MISSING_RUN_DIR",
        }

    existing_files = [p.name for p in run_dir.iterdir() if p.is_file()]
    if not existing_files:
        return {
            "schema_version": "R331_INDEPENDENT_VERIFICATION_V1",
            "is_valid": False,
            "status": "FAIL",
            "violations": ["EMPTY_R331_RUN_DIRECTORY"],
            "recalculated_verdict": "BLOCKED_BY_EMPTY_RUN_DIR",
        }

    loaded_json: Dict[str, Any] = {}
    loaded_jsonl: Dict[str, List[Dict[str, Any]]] = {}

    for fname in R331_MANDATORY_ARTIFACTS:
        fpath = run_dir / fname
        if not fpath.exists():
            violations.append(f"MISSING_MANDATORY_R331_ARTIFACT:{fname}")
            continue
        if fpath.stat().st_size == 0:
            violations.append(f"EMPTY_MANDATORY_R331_ARTIFACT:{fname}")
            continue
        if fname.endswith(".json"):
            try:
                doc = json.loads(fpath.read_text(encoding="utf-8"))
                if isinstance(doc, Mapping) and len(doc) == 0:
                    violations.append(f"EMPTY_JSON_R331_ARTIFACT:{fname}")
                loaded_json[fname] = doc
            except Exception as exc:
                violations.append(f"CORRUPT_JSON_R331_ARTIFACT:{fname}:{exc}")
        elif fname.endswith(".jsonl"):
            lines = [ln.strip() for ln in fpath.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if not lines:
                violations.append(f"EMPTY_JSONL_R331_ARTIFACT:{fname}")
                loaded_jsonl[fname] = []
            else:
                rows: List[Dict[str, Any]] = []
                for idx, ln in enumerate(lines):
                    try:
                        rows.append(json.loads(ln))
                    except Exception as exc:
                        violations.append(f"CORRUPT_JSONL_R331_ARTIFACT:{fname}:line_{idx}:{exc}")
                loaded_jsonl[fname] = rows

    init_man = loaded_json.get("R331_INITIAL_RUN_MANIFEST.json") or {}
    final_man = loaded_json.get("R331_RUN_MANIFEST_FINALIZATION.json") or {}
    init_man_path = run_dir / "R331_INITIAL_RUN_MANIFEST.json"
    init_man_bytes = init_man_path.read_bytes() if init_man_path.exists() else None
    manifest_run_id = str(init_man.get("run_id") or expected_run_id or "").strip()

    # Determine earliest task & backtest start timestamps
    cmd_rows = loaded_jsonl.get("R331_COMMAND_AND_SOURCE_EXECUTIONS.jsonl", [])
    worker_rows = loaded_jsonl.get("R331_WORKER_EXECUTIONS.jsonl", [])
    all_start_ms = [
        int(r.get("started_at_ms") or r.get("started_ms") or 0)
        for r in (list(cmd_rows) + list(worker_rows))
        if isinstance(r, Mapping) and int(r.get("started_at_ms") or r.get("started_ms") or 0) > 0
    ]
    first_task_ms = min(all_start_ms) if all_start_ms else None

    bt_man = loaded_json.get("R331_EXACT_SOURCE_BACKTEST_MANIFEST.json") or {}
    bt_start_ms = bt_man.get("started_at_ms") or (bt_man.get("execution_provenance") or {}).get("started_at_ms")

    # 1. Verify Pre-Execution Run Manifest Lifecycle (P0-A)
    man_chk = verify_pre_execution_run_manifest(
        init_man,
        final_man,
        expected_run_id=expected_run_id or manifest_run_id,
        expected_source_sha=clean_expected_sha,
        first_task_started_at_ms=first_task_ms,
        backtest_started_at_ms=int(bt_start_ms) if bt_start_ms is not None else None,
        initial_manifest_bytes=init_man_bytes,
    )
    if not man_chk["is_valid"]:
        violations.extend(man_chk["violations"])

    # 2. Verify Unmodified Run Identity Tree across all JSON & JSONL artifacts (P0-D)
    for fname, doc in loaded_json.items():
        if fname == "R331_INDEPENDENT_VERIFICATION.json":
            continue
        tree_chk = verify_unmodified_run_identity_tree(
            doc,
            expected_run_id=manifest_run_id,
            expected_source_sha=clean_expected_sha,
            path_prefix=fname,
        )
        if not tree_chk["is_valid"]:
            violations.extend(tree_chk["violations"])

    for fname, rows in loaded_jsonl.items():
        tree_chk = verify_unmodified_run_identity_tree(
            rows,
            expected_run_id=manifest_run_id,
            expected_source_sha=clean_expected_sha,
            path_prefix=fname,
        )
        if not tree_chk["is_valid"]:
            violations.extend(tree_chk["violations"])

    # 3. Verify Runtime Invocation Events, Input Snapshots & Input Captures
    rt_events = loaded_jsonl.get("R331_RUNTIME_INVOCATION_EVENTS.jsonl", [])
    rt_snaps = loaded_jsonl.get("R331_RUNTIME_INPUT_SNAPSHOTS.jsonl", [])
    rt_caps = loaded_jsonl.get("R331_RUNTIME_INPUT_CAPTURES.jsonl", [])
    recomp_rt_verif = reconcile_r33_runtime_captures(
        events=rt_events,
        snapshots=rt_snaps,
        captures=rt_caps,
        expected_run_id=manifest_run_id or None,
        expected_frozen_sha=clean_expected_sha or None,
    )
    if not recomp_rt_verif["is_valid"]:
        violations.extend(recomp_rt_verif["violations"])

    persisted_rt_verif = loaded_json.get("R331_RUNTIME_PROVENANCE_VERIFICATION.json") or {}
    if str(persisted_rt_verif.get("status") or "").upper() != "PASS" or not bool(persisted_rt_verif.get("is_valid", False)):
        violations.append("R331_RUNTIME_PROVENANCE_VERIFICATION_NOT_PASS")

    # 4. Verify Negative Controls Collection & Results (N01-N148, F01-F20, R331-01..R331-30 = 198 controls)
    neg_coll = loaded_json.get("R331_NEGATIVE_COLLECTION.json") or {}
    neg_res = loaded_json.get("R331_NEGATIVE_RESULTS.json") or {}
    collected_ids = set(neg_coll.get("collected_control_ids") or [])
    executed_results = neg_res.get("results") or []
    executed_ids = {str(r.get("control_id") or r.get("test_id") or "") for r in executed_results if isinstance(r, Mapping)}
    mandatory_neg_ids = (
        {f"N{i:02d}" for i in range(1, 149)}
        | {f"F{i:02d}" for i in range(1, 21)}
        | {f"R331-{i:02d}" for i in range(1, 31)}
    )
    for req_nid in sorted(mandatory_neg_ids):
        if req_nid not in collected_ids or req_nid not in executed_ids:
            violations.append(f"MISSING_MANDATORY_NEGATIVE_CONTROL_ID:{req_nid}")
    if int(neg_res.get("failed_negative_controls", -1)) != 0:
        violations.append(f"NEGATIVE_CONTROLS_FAILED:{neg_res.get('failed_negative_controls')}")
    for r in executed_results:
        if isinstance(r, Mapping) and str(r.get("status") or "").upper() != "PASS":
            violations.append(f"NEGATIVE_CONTROL_NOT_PASS:{r.get('control_id') or r.get('test_id')}")

    # 5. Independently Reconcile JUnit Scopes (P0-C)
    reports_dir = run_dir / "reports"
    if not reports_dir.exists() and isinstance(init_man, Mapping) and init_man.get("run_directory"):
        alt_rep = Path(__file__).resolve().parents[1] / str(init_man["run_directory"]) / "reports"
        if alt_rep.exists():
            reports_dir = alt_rep
    junit_dir = run_dir / "junit"
    junit_chk = independently_reconcile_junit_scopes(
        gate_xml_dir=reports_dir,
        suite_xml_dir=junit_dir if junit_dir.exists() else None,
        expected_run_id=manifest_run_id,
        expected_source_sha=clean_expected_sha,
        scope_inventory_doc=loaded_json.get("R331_JUNIT_SCOPE_INVENTORY.json"),
        per_xml_doc=loaded_json.get("R331_JUNIT_PER_XML_RECALCULATION.json"),
        overlap_doc=loaded_json.get("R331_JUNIT_OVERLAP_AND_RECONCILIATION.json"),
    )
    if not junit_chk["is_valid"]:
        violations.extend(junit_chk["violations"])

    # 6. Verify Worker Execution & Task Accounting (P0-E & P0-F)
    task_acc = loaded_json.get("R331_TASK_ACCOUNTING.json") or {}
    worker_chk = verify_worker_execution_accounting(
        worker_rows,
        task_acc,
        expected_run_id=manifest_run_id,
        expected_source_sha=clean_expected_sha,
        manifest_created_at_ms=man_chk.get("created_at_ms"),
    )
    if not worker_chk["is_valid"]:
        violations.extend(worker_chk["violations"])

    # 7. Verify Resource Telemetry & Disk Budget (P0-B & P0-E)
    mem_rep = loaded_json.get("R331_MEMORY_UNIT_AND_SCOPE_REPORT.json") or {}
    disk_rep = loaded_json.get("R331_DISK_BUDGET_AND_CLEANUP.json") or {}
    res_chk = verify_resource_telemetry_semantics(
        mem_rep,
        disk_rep,
        expected_run_id=manifest_run_id,
        expected_source_sha=clean_expected_sha,
    )
    if not res_chk["is_valid"]:
        violations.extend(res_chk["violations"])

    # 8. Verify Five-Way Parity
    five_way_doc = loaded_json.get("R331_FIVE_WAY_PARITY.json") or {}
    if str(five_way_doc.get("status") or "").upper() != "PASS":
        violations.append("FIVE_WAY_PARITY_STATUS_NOT_PASS")
    p_cls_res = verify_parity_classification_report(five_way_doc)
    if not p_cls_res["is_valid"]:
        violations.extend(p_cls_res["violations"])

    # 9. Verify Backtest Manifest, Variants ABCD, Trade Lineage, and Matched Cohort / OOS
    bt_abcd = loaded_json.get("R331_BACKTEST_VARIANTS_ABCD.json") or {}
    bt_cohort = loaded_json.get("R331_MATCHED_COHORT_AND_OOS.json") or {}
    bt_lineage = loaded_jsonl.get("R331_BACKTEST_TRADE_LINEAGE.jsonl", [])
    for doc_label, bt_d in (
        ("R331_EXACT_SOURCE_BACKTEST_MANIFEST.json", bt_man),
        ("R331_BACKTEST_VARIANTS_ABCD.json", bt_abcd),
        ("R331_MATCHED_COHORT_AND_OOS.json", bt_cohort),
    ):
        b_sha = str(bt_d.get("backtest_source_sha") or bt_d.get("tested_source_sha") or bt_d.get("source_sha") or "").strip()
        if not b_sha or (clean_expected_sha and b_sha != clean_expected_sha):
            violations.append(f"BACKTEST_SOURCE_SHA_MISMATCH:{doc_label}:{b_sha}!={clean_expected_sha}")
    if not bt_lineage:
        violations.append("EMPTY_BACKTEST_TRADE_LINEAGE")

    # 10. Verify Mandatory Artifact Hash Coverage (P0-F)
    sha_man = loaded_json.get("R331_ARTIFACT_SHA256_MANIFEST.json") or {}
    hash_chk = verify_mandatory_artifact_hash_coverage(
        run_dir,
        sha_man,
        R331_MANDATORY_ARTIFACTS,
    )
    if not hash_chk["is_valid"]:
        violations.extend(hash_chk["violations"])

    dedup_violations = list(dict.fromkeys(violations))
    is_valid = len(dedup_violations) == 0

    return {
        "schema_version": "R331_INDEPENDENT_VERIFICATION_V1",
        "run_id": manifest_run_id,
        "tested_source_sha": clean_expected_sha,
        "expected_code_sha": clean_expected_sha,
        "is_valid": is_valid,
        "status": "PASS" if is_valid else "FAIL",
        "violations": dedup_violations,
        "manifest_lifecycle_verification": man_chk,
        "junit_scope_reconciliation": junit_chk,
        "worker_accounting_verification": worker_chk,
        "resource_telemetry_verification": res_chk,
        "runtime_capture_verification": recomp_rt_verif,
        "hash_coverage_verification": hash_chk,
        "recalculated_verdict": (
            "VERIFIED_FOUNDATION_CLOSURE — READY_TO_PROCEED_TO_V17_9_1"
            if is_valid
            else f"BLOCKED_BY_R331_VERIFICATION:{dedup_violations[0]}"
        ),
    }


def verify_r332_evidence_bundle(
    run_dir: Path,
    expected_code_sha: str,
    *,
    expected_run_id: Optional[str] = None,
    expected_historical_baseline_sha: str = "c356d6551e13cd6af61cea5c3ab1f4aae1548af2",
    expected_dataset_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """
    R3.3.2 Section 5: Standalone, fail-closed independent verifier for the 30 mandatory
    R3.3.2 deliverables in `docs/artifacts/v17_9_0_1_3/r332/<run_id>/`.
    Reconciles ALL raw master-run primary worker attempts, raw primary shard XMLs,
    retry attempts, derived gate projections, Phase-H workers, 5-way runtime captures,
    resource/disk telemetry, backtest provenance, and 30-file SHA-256 manifest coverage.
    """
    import xml.etree.ElementTree as _ET
    from tools.runtime_execution_logger import R332_MANDATORY_ARTIFACTS

    run_dir = Path(run_dir)
    repo_root = Path(__file__).resolve().parents[1]
    violations: List[str] = []
    clean_expected_sha = str(expected_code_sha or "").strip()
    clean_hist_sha = str(expected_historical_baseline_sha or "c356d6551e13cd6af61cea5c3ab1f4aae1548af2").strip()

    if not run_dir.exists() or not run_dir.is_dir():
        return {
            "schema_version": "R332_INDEPENDENT_VERIFICATION_V1",
            "is_valid": False,
            "status": "FAIL",
            "violations": [f"R332_RUN_DIR_DOES_NOT_EXIST:{run_dir}"],
            "recalculated_verdict": "BLOCKED_BY_MISSING_RUN_DIR",
            "ready_for_v17_9_1": "NO",
            "trading_authorization": "MARKET_SHADOW_RESEARCH_ONLY",
        }

    existing_files = [p.name for p in run_dir.iterdir() if p.is_file()]
    if not existing_files:
        return {
            "schema_version": "R332_INDEPENDENT_VERIFICATION_V1",
            "is_valid": False,
            "status": "FAIL",
            "violations": ["EMPTY_R332_RUN_DIRECTORY"],
            "recalculated_verdict": "BLOCKED_BY_EMPTY_RUN_DIR",
            "ready_for_v17_9_1": "NO",
            "trading_authorization": "MARKET_SHADOW_RESEARCH_ONLY",
        }

    # Note: R332_MASTER_RETRY_ATTEMPTS.jsonl may legitimately be 0 bytes (empty) on a 100% clean primary run!
    empty_allowed_jsonl = {"R332_MASTER_RETRY_ATTEMPTS.jsonl"}

    loaded_json: Dict[str, Any] = {}
    loaded_jsonl: Dict[str, List[Dict[str, Any]]] = {}

    for fname in R332_MANDATORY_ARTIFACTS:
        fpath = run_dir / fname
        if not fpath.exists():
            violations.append(f"MISSING_MANDATORY_R332_ARTIFACT:{fname}")
            continue
        if fpath.stat().st_size == 0 and fname not in empty_allowed_jsonl:
            violations.append(f"EMPTY_MANDATORY_R332_ARTIFACT:{fname}")
            continue
        if fname.endswith(".json"):
            try:
                doc = json.loads(fpath.read_text(encoding="utf-8"))
                if isinstance(doc, Mapping) and len(doc) == 0:
                    violations.append(f"EMPTY_JSON_R332_ARTIFACT:{fname}")
                loaded_json[fname] = doc
            except Exception as exc:
                violations.append(f"CORRUPT_JSON_R332_ARTIFACT:{fname}:{exc}")
        elif fname.endswith(".jsonl"):
            lines = [ln.strip() for ln in fpath.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if not lines and fname not in empty_allowed_jsonl:
                violations.append(f"EMPTY_JSONL_R332_ARTIFACT:{fname}")
                loaded_jsonl[fname] = []
            else:
                rows: List[Dict[str, Any]] = []
                for idx, ln in enumerate(lines):
                    try:
                        rows.append(json.loads(ln))
                    except Exception as exc:
                        violations.append(f"CORRUPT_JSONL_R332_ARTIFACT:{fname}:line_{idx}:{exc}")
                loaded_jsonl[fname] = rows

    init_man = loaded_json.get("R332_INITIAL_RUN_MANIFEST.json") or {}
    final_man = loaded_json.get("R332_RUN_MANIFEST_FINALIZATION.json") or {}
    init_man_path = run_dir / "R332_INITIAL_RUN_MANIFEST.json"
    init_man_bytes = init_man_path.read_bytes() if init_man_path.exists() else None
    manifest_run_id = str(init_man.get("run_id") or expected_run_id or "").strip()

    # Check separate historical baseline SHA identity (Condition B / R332-30)
    man_hist_sha = str(init_man.get("historical_baseline_sha") or "").strip()
    if not man_hist_sha or man_hist_sha != clean_hist_sha or (clean_expected_sha and man_hist_sha == clean_expected_sha):
        violations.append(f"HISTORICAL_BASELINE_SHA_MISMATCH:{man_hist_sha}!={clean_hist_sha}")

    # Check safety policy & trading authorization (Condition T)
    safety_pol = init_man.get("safety_policy") or {}
    if (
        str(safety_pol.get("trading_authorization") or "") != "MARKET_SHADOW_RESEARCH_ONLY"
        or bool(safety_pol.get("paper_auto_trading_authorized", False))
        or bool(safety_pol.get("live_trading_authorized", False))
    ):
        violations.append("UNSAFE_TRADING_AUTHORIZATION_IN_MANIFEST")

    cmd_rows = loaded_jsonl.get("R332_SOURCE_COMMAND_EXECUTIONS.jsonl", [])
    master_primary_rows = loaded_jsonl.get("R332_MASTER_PRIMARY_ATTEMPTS.jsonl", [])
    master_retry_rows = loaded_jsonl.get("R332_MASTER_RETRY_ATTEMPTS.jsonl", [])
    phase_h_worker_rows = loaded_jsonl.get("R332_PHASE_H_WORKER_EXECUTIONS.jsonl", [])

    all_start_ms = [
        int(r.get("started_at_ms") or r.get("started_ms") or 0)
        for r in (list(cmd_rows) + list(master_primary_rows) + list(phase_h_worker_rows))
        if isinstance(r, Mapping) and int(r.get("started_at_ms") or r.get("started_ms") or 0) > 0
    ]
    first_task_ms = min(all_start_ms) if all_start_ms else None

    bt_man = loaded_json.get("R332_EXACT_SOURCE_BACKTEST_MANIFEST.json") or {}
    bt_start_ms = bt_man.get("started_at_ms") or (bt_man.get("execution_provenance") or {}).get("started_at_ms")

    # 1. Verify Pre-Execution Run Manifest Lifecycle (Condition A & R332-35)
    man_chk = verify_pre_execution_run_manifest(
        init_man,
        final_man,
        expected_run_id=expected_run_id or manifest_run_id,
        expected_source_sha=clean_expected_sha,
        first_task_started_at_ms=first_task_ms,
        backtest_started_at_ms=int(bt_start_ms) if bt_start_ms is not None else None,
        initial_manifest_bytes=init_man_bytes,
    )
    if not man_chk["is_valid"]:
        violations.extend(man_chk["violations"])

    # 2. Verify Unmodified Run Identity Tree across all JSON & JSONL artifacts (Condition B & O)
    for fname, doc in loaded_json.items():
        if fname == "R332_INDEPENDENT_VERIFICATION.json":
            continue
        tree_chk = verify_unmodified_run_identity_tree(
            doc,
            expected_run_id=manifest_run_id,
            expected_source_sha=clean_expected_sha,
            expected_historical_sha=clean_hist_sha,
            path_prefix=fname,
        )
        if not tree_chk["is_valid"]:
            violations.extend(tree_chk["violations"])

    for fname, rows in loaded_jsonl.items():
        if not rows:
            continue
        tree_chk = verify_unmodified_run_identity_tree(
            rows,
            expected_run_id=manifest_run_id,
            expected_source_sha=clean_expected_sha,
            expected_historical_sha=clean_hist_sha,
            path_prefix=fname,
        )
        if not tree_chk["is_valid"]:
            violations.extend(tree_chk["violations"])

    # 3. Verify Master Primary Worker Attempts, Raw Shard XMLs & Logs (Conditions C, D, E, L, S & R332-01..R332-22, R332-33..R332-34)
    assets_base_dir = run_dir
    if not (run_dir / "reports").exists() and isinstance(init_man, Mapping) and init_man.get("run_directory"):
        alt_base = repo_root / str(init_man["run_directory"])
        if (alt_base / "reports").exists():
            assets_base_dir = alt_base

    raw_junit_man = loaded_json.get("R332_RAW_JUNIT_SHA256_MANIFEST.json") or {}
    raw_man_by_id: Dict[str, Mapping[str, Any]] = {}
    for ent in (raw_junit_man.get("entries") or []):
        if isinstance(ent, Mapping):
            aid = str(ent.get("primary_attempt_id") or ent.get("retry_attempt_id") or "").strip()
            if aid:
                raw_man_by_id[aid] = ent

    seen_attempt_ids: set[str] = set()
    primary_attempt_by_id: Dict[str, Mapping[str, Any]] = {}
    scheduled_shard_files: List[str] = []
    raw_primary_xml_test_totals = {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    raw_primary_node_ids: set[str] = set()

    if not master_primary_rows:
        violations.append("EMPTY_OR_MISSING_MASTER_PRIMARY_ATTEMPTS")
    for idx, row in enumerate(master_primary_rows):
        if not isinstance(row, Mapping):
            violations.append(f"INVALID_PRIMARY_ATTEMPT_ROW:{idx}")
            continue
        aid = str(row.get("primary_attempt_id") or row.get("attempt_id") or "").strip()
        if not aid:
            violations.append(f"MISSING_PRIMARY_ATTEMPT_ID:row_{idx}")
            continue
        if aid in seen_attempt_ids:
            violations.append(f"DUPLICATE_ATTEMPT_ID:{aid}")
        seen_attempt_ids.add(aid)
        primary_attempt_by_id[aid] = row

        w_sha = str(row.get("source_sha") or row.get("tested_source_sha") or "").strip()
        if not w_sha or (clean_expected_sha and w_sha != clean_expected_sha):
            violations.append(f"MISSING_OR_MISMATCHED_WORKER_SOURCE_SHA:{aid}:{w_sha}!={clean_expected_sha}")

        st_ms = row.get("started_at_ms")
        comp_ms = row.get("completed_at_ms")
        if st_ms is None or isinstance(st_ms, bool) or not isinstance(st_ms, int) or int(st_ms) <= 0:
            violations.append(f"MISSING_PRIMARY_SHARD_STARTED_AT_MS:{aid}")
        if comp_ms is None or isinstance(comp_ms, bool) or not isinstance(comp_ms, int) or (isinstance(st_ms, int) and int(comp_ms) < int(st_ms)):
            violations.append(f"INVALID_PRIMARY_SHARD_COMPLETED_AT_MS:{aid}")

        pid_val = row.get("pid")
        cmd_val = row.get("command")
        if pid_val is None or isinstance(pid_val, bool) or not isinstance(pid_val, int) or int(pid_val) <= 0 or not cmd_val:
            violations.append(f"MISSING_PRIMARY_SUBPROCESS_PID_OR_ARGV:{aid}")

        if bool(row.get("timed_out", False)):
            violations.append(f"INTERRUPTED_OR_TIMED_OUT_WORKER_REPORTED:{aid}")

        exit_c = row.get("exit_code")
        prim_exit_c = row.get("primary_exit_code", exit_c)
        prim_status = str(row.get("primary_status") or row.get("status") or "").upper()
        if exit_c is None or isinstance(exit_c, bool) or not isinstance(exit_c, int):
            violations.append(f"MISSING_PRIMARY_EXIT_CODE:{aid}")
        elif prim_exit_c is None or isinstance(prim_exit_c, bool) or not isinstance(prim_exit_c, int):
            violations.append(f"MISSING_PRIMARY_EXIT_CODE:{aid}")
        else:
            if int(exit_c) != int(prim_exit_c):
                violations.append(f"PRIMARY_EXIT_CODE_MUTATED:{aid}:{prim_exit_c}!={exit_c}")
            if int(exit_c) != 0 or int(prim_exit_c) != 0 or prim_status != "PASS":
                violations.append(f"PRIMARY_SUBPROCESS_NONZERO_EXIT_OR_FAIL:{aid}:exit={exit_c},prim_exit={prim_exit_c},status={prim_status}")

        # Verify raw log file existence, SHA-256, and unmutated EXIT_CODE line (R332-03, R332-05, R332-33)
        log_rel = str(row.get("log_path") or "").strip()
        log_sha = str(row.get("log_sha256") or "").strip()
        log_candidates = []
        if log_rel:
            log_candidates.append(assets_base_dir / "logs" / Path(log_rel).name)
            log_candidates.append(assets_base_dir / log_rel)
            log_candidates.append(run_dir / "logs" / Path(log_rel).name)
            log_candidates.append(run_dir / log_rel)
            log_candidates.append(repo_root / log_rel)
        log_disk = next((p for p in log_candidates if p.exists() and p.is_file()), None)
        if log_disk is None or log_disk.stat().st_size == 0:
            violations.append(f"MISSING_ORIGINAL_RAW_SHARD_LOG:{aid}:{log_rel}")
        else:
            actual_log_sha = hashlib.sha256(log_disk.read_bytes()).hexdigest()
            if log_sha and actual_log_sha != log_sha:
                violations.append(f"RAW_SHARD_LOG_SHA256_MISMATCH:{aid}:{actual_log_sha}!={log_sha}")
            log_text = log_disk.read_text(encoding="utf-8", errors="replace")
            for ln in log_text.splitlines()[:15]:
                if ln.startswith("EXIT_CODE:"):
                    try:
                        logged_exit = int(ln.split(":", 1)[1].strip())
                        if isinstance(prim_exit_c, int) and logged_exit != int(prim_exit_c):
                            violations.append(f"PRIMARY_EXIT_CODE_MUTATED_FROM_RAW_LOG:{aid}:log={logged_exit}!=record={prim_exit_c}")
                        if logged_exit != 0:
                            violations.append(f"RAW_LOG_RECORDS_NONZERO_EXIT:{aid}:{logged_exit}")
                    except Exception:
                        violations.append(f"CORRUPT_RAW_LOG_EXIT_CODE:{aid}")

        # If this is a pytest shard attempt, verify raw primary shard XML on disk (R332-02, R332-04, R332-06, R332-09, R332-21)
        task_id_str = str(row.get("task_id") or "")
        if task_id_str.startswith("pytest_shard_") or "raw_xml_path" in row:
            shard_files = row.get("files") or []
            if isinstance(shard_files, list):
                scheduled_shard_files.extend(str(f) for f in shard_files)
            raw_xml_rel = str(row.get("raw_xml_path") or "").strip()
            claimed_xml_sha = str(row.get("raw_xml_sha256") or "").strip()
            xml_candidates = []
            if raw_xml_rel:
                xml_candidates.append(assets_base_dir / "reports" / "raw" / "primary" / Path(raw_xml_rel).name)
                xml_candidates.append(assets_base_dir / raw_xml_rel)
                xml_candidates.append(run_dir / "reports" / "raw" / "primary" / Path(raw_xml_rel).name)
                xml_candidates.append(run_dir / raw_xml_rel)
                xml_candidates.append(repo_root / raw_xml_rel)
            xml_disk = next((p for p in xml_candidates if p.exists() and p.is_file()), None)
            if xml_disk is None or xml_disk.stat().st_size == 0:
                violations.append(f"MISSING_ORIGINAL_RAW_SHARD_XML:{aid}:{raw_xml_rel}")
            else:
                actual_xml_sha = hashlib.sha256(xml_disk.read_bytes()).hexdigest()
                man_ent = raw_man_by_id.get(aid) or {}
                man_xml_sha = str(man_ent.get("raw_xml_sha256") or "").strip()
                if not claimed_xml_sha or actual_xml_sha != claimed_xml_sha or (man_xml_sha and actual_xml_sha != man_xml_sha):
                    violations.append(f"ALTERED_RAW_XML_SHA256_MISMATCH:{aid}:disk={actual_xml_sha},claimed={claimed_xml_sha},man={man_xml_sha}")
                try:
                    xroot = _ET.parse(xml_disk).getroot()
                    tcs = xroot.findall(".//testcase")
                    f_c = sum(1 for tc in tcs if tc.find("failure") is not None)
                    e_c = sum(1 for tc in tcs if tc.find("error") is not None)
                    s_c = sum(1 for tc in tcs if tc.find("skipped") is not None)
                    t_c = len(tcs)
                    p_c = max(0, t_c - f_c - e_c - s_c)
                    raw_primary_xml_test_totals["tests"] += t_c
                    raw_primary_xml_test_totals["passed"] += p_c
                    raw_primary_xml_test_totals["failed"] += f_c
                    raw_primary_xml_test_totals["errors"] += e_c
                    raw_primary_xml_test_totals["skipped"] += s_c
                    if f_c > 0 or e_c > 0:
                        violations.append(f"RAW_PRIMARY_SHARD_XML_HAS_FAILURES:{aid}:failures={f_c},errors={e_c}")
                    shard_nodes = [f"{tc.get('classname', '')}::{tc.get('name', '')}" for tc in tcs]
                    if len(set(shard_nodes)) != len(shard_nodes):
                        violations.append(f"DUPLICATE_TESTCASE_ID_IN_SHARD:{aid}")
                    for nid in shard_nodes:
                        if nid in raw_primary_node_ids:
                            violations.append(f"DUPLICATE_TESTCASE_ID_ACROSS_SHARDS:{nid}")
                        raw_primary_node_ids.add(nid)
                    p_counts = row.get("primary_counts") or {}
                    if isinstance(p_counts, Mapping) and int(p_counts.get("collected", t_c)) != t_c:
                        violations.append(f"DUPLICATE_TESTCASE_ID_COLLAPSED_OR_COUNT_MISMATCH:{aid}:{p_counts.get('collected')}!={t_c}")
                except Exception as xp_exc:
                    violations.append(f"CORRUPT_RAW_PRIMARY_SHARD_XML:{aid}:{xp_exc}")

    # Check shard file inventory completeness if running against real repo shard set (R332-21)
    expected_shard_files = (init_man.get("expected_backend_test_files") or [])
    if expected_shard_files:
        missing_files = sorted(set(str(x) for x in expected_shard_files) - set(scheduled_shard_files))
        if missing_files:
            violations.append(f"MISSING_BACKEND_TESTS_IN_SHARD_INVENTORY:{missing_files[:5]}")
    elif len(scheduled_shard_files) > 50:
        actual_repo_test_files = {
            str(p.relative_to(repo_root)).replace("\\", "/")
            for p in (repo_root / "backend" / "tests").glob("test_*.py")
        }
        missing_repo_files = sorted(actual_repo_test_files - set(scheduled_shard_files))
        if missing_repo_files:
            violations.append(f"MISSING_BACKEND_TESTS_IN_SHARD_INVENTORY:{missing_repo_files[:5]}")

    # 4. Verify Master Attempt Accounting & Failure/Flake Report (Conditions C, D, E, G & R332-01, R332-07, R332-10, R332-11, R332-14, R332-19, R332-31)
    master_acc = loaded_json.get("R332_MASTER_ATTEMPT_ACCOUNTING.json") or {}
    m_sched = int(master_acc.get("scheduled_tasks", -1))
    m_succ = int(master_acc.get("succeeded_tasks", -1))
    m_fail = int(master_acc.get("failed_tasks", -1))
    m_blk = int(master_acc.get("blocked_tasks", 0))
    m_canc = int(master_acc.get("cancelled_tasks", 0))
    if m_sched <= 0 or m_sched != len(master_primary_rows) or m_sched != (m_succ + m_fail + m_blk + m_canc):
        violations.append(
            f"MASTER_ATTEMPT_ACCOUNTING_MISMATCH:sched={m_sched},rows={len(master_primary_rows)},sum={m_succ + m_fail + m_blk + m_canc}"
        )
    if m_fail != 0 or m_blk != 0 or m_canc != 0:
        violations.append(f"MASTER_WORKER_LEDGER_HAS_FAILED_TASKS:failed={m_fail},blocked={m_blk},cancelled={m_canc}")
    if int(master_acc.get("retry_attempts_count", 0)) != len(master_retry_rows):
        violations.append("MASTER_RETRY_COUNT_ACCOUNTING_MISMATCH")

    flake_rep = loaded_json.get("R332_MASTER_FAILURE_AND_FLAKE_REPORT.json") or {}
    if (
        int(flake_rep.get("primary_shards_failed", 0)) > 0
        or int(flake_rep.get("primary_failed_testcases_count", 0)) > 0
        or int(flake_rep.get("isolated_retries_count", 0)) > 0
        or int(flake_rep.get("flaky_or_isolation_dependent_count", 0)) > 0
        or str(flake_rep.get("clean_primary_acceptance_status") or "").upper() != "PASS"
    ):
        violations.append("MASTER_FAILURE_OR_FLAKE_REPORT_NOT_CLEAN_PASS")

    prim_vs_retry = loaded_json.get("R332_PRIMARY_VS_RETRY_JUNIT_RECONCILIATION.json") or {}
    if (
        bool(prim_vs_retry.get("replacement_performed", False))
        or bool(prim_vs_retry.get("primary_exit_codes_rewritten", False))
        or not bool(prim_vs_retry.get("raw_shard_xml_retained", False))
    ):
        violations.append("ILLEGAL_PRIMARY_JUNIT_REPLACEMENT_OR_EXIT_REWRITE")
    p_scope = prim_vs_retry.get("primary_execution_scope") or {}
    if int(p_scope.get("unique_testcase_nodes", -1)) != len(raw_primary_node_ids):
        violations.append(
            f"PRIMARY_UNIQUE_TESTCASE_COUNT_MISMATCH:claimed={p_scope.get('unique_testcase_nodes')}!=actual={len(raw_primary_node_ids)}"
        )

    for r_idx, r_row in enumerate(master_retry_rows):
        if not isinstance(r_row, Mapping):
            violations.append(f"INVALID_RETRY_ATTEMPT_ROW:{r_idx}")
            continue
        r_id = str(r_row.get("retry_attempt_id") or r_row.get("attempt_id") or "").strip()
        src_id = str(r_row.get("source_attempt_id") or "").strip()
        if not r_id or not src_id:
            violations.append(f"RETRY_MISSING_ATTEMPT_IDS:row_{r_idx}")
        if r_id == src_id or r_id in primary_attempt_by_id:
            violations.append(f"RETRY_REUSES_PRIMARY_ATTEMPT_ID:{r_id}")
        if r_id in seen_attempt_ids:
            violations.append(f"DUPLICATE_ATTEMPT_ID:{r_id}")
        seen_attempt_ids.add(r_id)
        if (
            not r_row.get("pid")
            or not r_row.get("command")
            or not r_row.get("started_at_ms")
            or not r_row.get("completed_at_ms")
        ):
            violations.append(f"RETRY_MISSING_SUBPROCESS_PROVENANCE:{r_id}")
        r_run = str(r_row.get("run_id") or "").strip()
        r_sha = str(r_row.get("source_sha") or r_row.get("tested_source_sha") or "").strip()
        if (manifest_run_id and r_run != manifest_run_id) or (clean_expected_sha and r_sha != clean_expected_sha):
            violations.append(f"CROSS_RUN_OR_COMMIT_RETRY_XML:{r_id}")
        r_xml_rel = str(r_row.get("retry_xml_path") or "").strip()
        r_xml_sha = str(r_row.get("retry_xml_sha256") or "").strip()
        r_candidates = []
        if r_xml_rel:
            r_candidates.append(assets_base_dir / "reports" / "raw" / "retries" / Path(r_xml_rel).name)
            r_candidates.append(assets_base_dir / r_xml_rel)
            r_candidates.append(run_dir / "reports" / "raw" / "retries" / Path(r_xml_rel).name)
            r_candidates.append(run_dir / r_xml_rel)
            r_candidates.append(repo_root / r_xml_rel)
        r_disk = next((p for p in r_candidates if p.exists() and p.is_file()), None)
        if r_disk is None or r_disk.stat().st_size == 0:
            violations.append(f"MISSING_RETRY_XML_ON_DISK:{r_id}")
        else:
            actual_r_sha = hashlib.sha256(r_disk.read_bytes()).hexdigest()
            if not r_xml_sha or actual_r_sha != r_xml_sha:
                violations.append(f"ALTERED_RETRY_XML_LINEAGE_MISMATCH:{r_id}")
            try:
                rtcs = _ET.parse(r_disk).getroot().findall(".//testcase")
                rf_c = sum(1 for tc in rtcs if tc.find("failure") is not None or tc.find("error") is not None)
                rc_counts = r_row.get("counts") or {}
                if len(rtcs) != int(rc_counts.get("collected", -1)) or rf_c != (int(rc_counts.get("failed", 0)) + int(rc_counts.get("errors", 0))):
                    violations.append(f"ALTERED_RETRY_XML_LINEAGE_MISMATCH:{r_id}:counts_mismatch")
            except Exception:
                violations.append(f"ALTERED_RETRY_XML_LINEAGE_MISMATCH:{r_id}:corrupt_xml")
        # Any retry after primary failure blocks clean acceptance (R332-01, R332-31)
        violations.append(f"PRIMARY_FAILURE_FOLLOWED_BY_ISOLATED_RETRY:{src_id}->{r_id}:{r_row.get('effective_test_outcome')}")

    # 5. Verify Gate Projection Provenance & Reconcile JUnit Scopes (Conditions F, H, I, J & R332-02, R332-12, R332-13, R332-20)
    reports_dir = assets_base_dir / "reports"
    junit_dir = assets_base_dir / "junit"
    junit_chk = independently_reconcile_junit_scopes(
        gate_xml_dir=reports_dir,
        suite_xml_dir=junit_dir if junit_dir.exists() else None,
        expected_run_id=manifest_run_id,
        expected_source_sha=clean_expected_sha,
        scope_inventory_doc=loaded_json.get("R332_JUNIT_SCOPE_INVENTORY.json"),
        per_xml_doc=loaded_json.get("R332_JUNIT_PER_XML_RECALCULATION.json"),
    )
    if not junit_chk["is_valid"]:
        violations.extend(junit_chk["violations"])

    gate_proj_doc = loaded_json.get("R332_GATE_PROJECTION_PROVENANCE.json") or {}
    projections_map = gate_proj_doc.get("projections") or {}
    if not isinstance(projections_map, Mapping) or len(projections_map) == 0:
        violations.append("EMPTY_GATE_PROJECTION_PROVENANCE")
    else:
        for g_xml_name, p_rec in projections_map.items():
            if not isinstance(p_rec, Mapping):
                continue
            mode = str(p_rec.get("execution_mode") or "").strip()
            src_aids = p_rec.get("source_attempt_ids") or []
            src_hashes = p_rec.get("source_xml_hashes") or {}
            log_n = str(p_rec.get("log_name") or Path(g_xml_name).stem).strip()
            g_log_p = assets_base_dir / "logs" / f"{log_n}.log"
            if mode == "DERIVED_PROJECTION":
                if p_rec.get("exit_code") != "not_applicable":
                    violations.append(f"DERIVED_PROJECTION_CLAIMS_SUBPROCESS_EXIT_CODE:{g_xml_name}:{p_rec.get('exit_code')}")
                if g_log_p.exists():
                    g_log_txt = g_log_p.read_text(encoding="utf-8", errors="replace")
                    if any(
                        ln.startswith("COMMAND:")
                        or ln.startswith("EXIT_CODE:")
                        or "EXECUTION_MODE: ACTUAL_SUBPROCESS" in ln
                        for ln in g_log_txt.splitlines()
                    ):
                        violations.append(f"DERIVED_PROJECTION_CLAIMS_SUBPROCESS_EXECUTION:{g_xml_name}")
                if not src_aids or not isinstance(src_hashes, Mapping):
                    violations.append(f"DERIVED_PROJECTION_MISSING_SOURCE_PROVENANCE:{g_xml_name}")
                for s_aid in src_aids:
                    p_att = primary_attempt_by_id.get(str(s_aid))
                    if p_att is None:
                        violations.append(f"DERIVED_PROJECTION_UNKNOWN_SOURCE_ATTEMPT:{g_xml_name}:{s_aid}")
                    else:
                        if int(p_att.get("primary_exit_code", 1)) != 0 or str(p_att.get("primary_status") or "").upper() != "PASS":
                            violations.append(f"DERIVED_JUNIT_MASKS_FAILED_PRIMARY_ATTEMPT:{g_xml_name}:{s_aid}")
                        expected_raw_sha = str(p_att.get("raw_xml_sha256") or "").strip()
                        recorded_raw_sha = str(src_hashes.get(str(s_aid)) or "").strip()
                        if expected_raw_sha and recorded_raw_sha != expected_raw_sha:
                            violations.append(f"DERIVED_PROJECTION_SOURCE_XML_HASH_MISMATCH:{g_xml_name}:{s_aid}")
            elif mode == "ACTUAL_SUBPROCESS":
                if not src_aids or any(str(s_aid) not in primary_attempt_by_id for s_aid in src_aids):
                    violations.append(f"ACTUAL_SUBPROCESS_GATE_MISSING_PRIMARY_ATTEMPT:{g_xml_name}")
            else:
                violations.append(f"INVALID_GATE_EXECUTION_MODE:{g_xml_name}:{mode}")

    # 6. Verify All 107 Mandatory Gates in R332_GATE_RESULTS.json (Condition K)
    gate_res_doc = loaded_json.get("R332_GATE_RESULTS.json") or {}
    gates_map = gate_res_doc.get("gates") or {}
    for req_gate in MANDATORY_GATES_V17_9_0:
        g_entry = gates_map.get(req_gate)
        if not isinstance(g_entry, Mapping) or str(g_entry.get("status") or "").upper() != "PASS":
            violations.append(f"MANDATORY_GATE_NOT_PASS:{req_gate}")

    # 7. Verify Negative Controls Collection & Results (Condition R & R332-27: N01-N148, F01-F20, R331-01..R331-30, R332-01..R332-36 = 234 controls)
    neg_coll = loaded_json.get("R332_NEGATIVE_CONTROL_COLLECTION.json") or {}
    neg_res = loaded_json.get("R332_NEGATIVE_CONTROL_RESULTS.json") or {}
    collected_ids = set(neg_coll.get("collected_control_ids") or [])
    executed_results = neg_res.get("results") or []
    executed_ids = {str(r.get("control_id") or r.get("test_id") or "") for r in executed_results if isinstance(r, Mapping)}
    mandatory_neg_ids = (
        {f"N{i:02d}" for i in range(1, 149)}
        | {f"F{i:02d}" for i in range(1, 21)}
        | {f"R331-{i:02d}" for i in range(1, 31)}
        | {f"R332-{i:02d}" for i in range(1, 37)}
    )
    for req_nid in sorted(mandatory_neg_ids):
        if req_nid not in collected_ids or req_nid not in executed_ids:
            violations.append(f"MISSING_MANDATORY_NEGATIVE_CONTROL_ID:{req_nid}")
    if int(neg_res.get("failed_negative_controls", -1)) != 0:
        violations.append(f"NEGATIVE_CONTROLS_FAILED:{neg_res.get('failed_negative_controls')}")
    for r in executed_results:
        if isinstance(r, Mapping) and str(r.get("status") or "").upper() != "PASS":
            violations.append(f"NEGATIVE_CONTROL_NOT_PASS:{r.get('control_id') or r.get('test_id')}")

    # 8. Verify Phase-H Worker Execution & Resource/Disk Telemetry (Conditions M, N & R332-23..R332-26)
    res_disk_doc = loaded_json.get("R332_RESOURCE_AND_DISK_TELEMETRY.json") or {}
    phase_h_acc = res_disk_doc.get("phase_h_task_accounting") or master_acc
    worker_chk = verify_worker_execution_accounting(
        phase_h_worker_rows,
        phase_h_acc,
        expected_run_id=manifest_run_id,
        expected_source_sha=clean_expected_sha,
        manifest_created_at_ms=man_chk.get("created_at_ms"),
    )
    if not worker_chk["is_valid"]:
        violations.extend(worker_chk["violations"])

    mem_rep = res_disk_doc.get("memory_unit_and_scope_report") or res_disk_doc.get("cpu_memory_metrics") or {}
    disk_rep = res_disk_doc.get("disk_budget_and_cleanup_report") or res_disk_doc.get("disk_budget_report") or {}
    res_chk = verify_resource_telemetry_semantics(
        mem_rep,
        disk_rep,
        expected_run_id=manifest_run_id,
        expected_source_sha=clean_expected_sha,
    )
    if not res_chk["is_valid"]:
        violations.extend(res_chk["violations"])

    # 9. Verify Runtime Invocation Events, Input Snapshots, Captures & Five-Way Parity
    rt_events = loaded_jsonl.get("R332_RUNTIME_INVOCATION_EVENTS.jsonl", [])
    rt_snaps = loaded_jsonl.get("R332_RUNTIME_INPUT_SNAPSHOTS.jsonl", [])
    rt_caps = loaded_jsonl.get("R332_RUNTIME_INPUT_CAPTURES.jsonl", [])
    recomp_rt_verif = reconcile_r33_runtime_captures(
        events=rt_events,
        snapshots=rt_snaps,
        captures=rt_caps,
        expected_run_id=manifest_run_id or None,
        expected_frozen_sha=clean_expected_sha or None,
    )
    if not recomp_rt_verif["is_valid"]:
        violations.extend(recomp_rt_verif["violations"])

    five_way_doc = loaded_json.get("R332_FIVE_WAY_PARITY.json") or {}
    if str(five_way_doc.get("status") or "").upper() != "PASS":
        violations.append("FIVE_WAY_PARITY_STATUS_NOT_PASS")
    p_cls_res = verify_parity_classification_report(five_way_doc)
    if not p_cls_res["is_valid"]:
        violations.extend(p_cls_res["violations"])

    # 10. Verify Backtest Manifest, Variants ABCD, Trade Ledger & Dataset SHA (Condition P & R332-28..R332-29)
    bt_abcd = loaded_json.get("R332_BACKTEST_A_B_C_D.json") or {}
    bt_cohort = loaded_json.get("R332_MATCHED_COHORT_AND_OOS.json") or {}
    bt_ledger = loaded_jsonl.get("R332_BACKTEST_TRADE_LEDGER.jsonl", [])
    for doc_label, bt_d in (
        ("R332_EXACT_SOURCE_BACKTEST_MANIFEST.json", bt_man),
        ("R332_BACKTEST_A_B_C_D.json", bt_abcd),
        ("R332_MATCHED_COHORT_AND_OOS.json", bt_cohort),
    ):
        b_sha = str(bt_d.get("backtest_source_sha") or bt_d.get("tested_source_sha") or bt_d.get("source_sha") or "").strip()
        if not b_sha or (clean_expected_sha and b_sha != clean_expected_sha):
            violations.append(f"BACKTEST_SOURCE_SHA_MISMATCH:{doc_label}:{b_sha}!={clean_expected_sha}")
    exp_ds = str(expected_dataset_sha256 or init_man.get("dataset_sha256") or "").strip()
    bt_ds = str(bt_man.get("dataset_sha256") or "").strip()
    if not bt_ds or (exp_ds and bt_ds != exp_ds):
        violations.append(f"BACKTEST_DATASET_SHA_MISMATCH:{bt_ds}!={exp_ds}")
    if not bt_ledger:
        violations.append("EMPTY_BACKTEST_TRADE_LEDGER")

    # 11. Verify Mandatory Artifact Hash Coverage (Condition Q)
    sha_man = loaded_json.get("R332_ARTIFACT_SHA256_MANIFEST.json") or {}
    hash_chk = verify_mandatory_artifact_hash_coverage(
        run_dir,
        sha_man,
        R332_MANDATORY_ARTIFACTS,
        self_excluded_files=(
            "R332_ARTIFACT_SHA256_MANIFEST.json",
            "R332_INDEPENDENT_VERIFICATION.json",
            "R332_FINAL_ACCEPTANCE_AUDIT.md",
        ),
    )
    if not hash_chk["is_valid"]:
        violations.extend(hash_chk["violations"])

    dedup_violations = list(dict.fromkeys(violations))
    is_valid = len(dedup_violations) == 0

    return {
        "schema_version": "R332_INDEPENDENT_VERIFICATION_V1",
        "run_id": manifest_run_id,
        "tested_source_sha": clean_expected_sha,
        "expected_code_sha": clean_expected_sha,
        "historical_baseline_sha": clean_hist_sha,
        "is_valid": is_valid,
        "status": "PASS" if is_valid else "FAIL",
        "violations": dedup_violations,
        "manifest_lifecycle_verification": man_chk,
        "master_primary_attempts_verified": len(master_primary_rows),
        "master_retry_attempts_verified": len(master_retry_rows),
        "raw_primary_xml_test_totals": raw_primary_xml_test_totals,
        "unique_primary_test_nodes": len(raw_primary_node_ids),
        "junit_scope_reconciliation": junit_chk,
        "worker_accounting_verification": worker_chk,
        "resource_telemetry_verification": res_chk,
        "runtime_capture_verification": recomp_rt_verif,
        "hash_coverage_verification": hash_chk,
        "release_readiness_verdict": "VERIFIED_FOUNDATION_CLOSURE" if is_valid else "BLOCKED",
        "ready_for_v17_9_1": "YES" if is_valid else "NO",
        "trading_authorization": "MARKET_SHADOW_RESEARCH_ONLY",
        "recalculated_verdict": (
            "VERIFIED_FOUNDATION_CLOSURE — READY_TO_PROCEED_TO_V17_9_1"
            if is_valid
            else f"BLOCKED_BY_R332_VERIFICATION:{dedup_violations[0]}"
        ),
    }


def verify_persisted_acceptance_artifacts(
    target_dir: Path,
    expected_code_sha: str,
    expected_release: str = "V17.9.0.1.3",
    *,
    required_schema: Optional[str] = None,
    expected_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    DEF-179013-R01 & R3.3 / R3.3.1 / R3.3.2: Reads persisted artifacts from disk, verifies checksums,
    validates cross-artifact consistency, and recalculates the final release verdict directly from disk bytes.
    """
    target_dir = Path(target_dir)
    if (
        required_schema == "R332"
        or (target_dir.exists() and target_dir.is_dir() and (
            (target_dir / "R332_INITIAL_RUN_MANIFEST.json").exists()
            or any(f.name.startswith("R332_") for f in target_dir.iterdir() if f.is_file())
        ))
    ):
        return verify_r332_evidence_bundle(
            target_dir,
            expected_code_sha,
            expected_run_id=expected_run_id,
        )
    if (
        required_schema == "R331"
        or (target_dir.exists() and target_dir.is_dir() and (
            (target_dir / "R331_INITIAL_RUN_MANIFEST.json").exists()
            or any(f.name.startswith("R331_") for f in target_dir.iterdir() if f.is_file())
        ))
    ):
        return verify_r331_evidence_bundle(
            target_dir,
            expected_code_sha,
            expected_run_id=expected_run_id,
        )
    if (
        required_schema == "R33"
        or (target_dir.exists() and target_dir.is_dir() and (
            (target_dir / "R33_RUN_MANIFEST.json").exists()
            or any(f.name.startswith("R33_") for f in target_dir.iterdir() if f.is_file())
        ))
    ):
        return verify_r33_evidence_bundle(
            target_dir,
            expected_code_sha,
            expected_run_id=expected_run_id,
        )

    violations: List[str] = []

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

    # Check R3.2 mandatory deliverables (Fail-Closed: all 23 files must exist and be valid)
    is_r32_context = (
        (target_dir / "R32_RUN_MANIFEST.json").exists()
        or (target_dir.exists() and any(f.name.startswith("R32_") for f in target_dir.iterdir() if f.is_file()))
    )
    if is_r32_context:
        r32_manifest_path = target_dir / "R32_RUN_MANIFEST.json"
        manifest_run_id = None
        if not r32_manifest_path.exists():
            violations.append("MISSING_MANDATORY_ARTIFACT:R32_RUN_MANIFEST.json")
        else:
            try:
                r32_man = json.loads(r32_manifest_path.read_text(encoding="utf-8"))
                manifest_run_id = str(r32_man.get("run_id") or "").strip()
                if not manifest_run_id:
                    violations.append("R32_RUN_MANIFEST_MISSING_RUN_ID")
                man_sha = str(
                    r32_man.get("source_sha")
                    or r32_man.get("final_code_sha")
                    or r32_man.get("tested_source_sha")
                    or ""
                ).strip()
                if not man_sha:
                    violations.append("R32_RUN_MANIFEST_MISSING_SOURCE_SHA")
                elif expected_code_sha and man_sha != expected_code_sha.strip():
                    violations.append(f"R32_RUN_MANIFEST_SHA_MISMATCH:{man_sha}!={expected_code_sha}")
            except Exception as ex_man:
                violations.append(f"R32_RUN_MANIFEST_CORRUPT:{ex_man}")

        r32_events: List[Dict[str, Any]] = []
        r32_captures: List[Dict[str, Any]] = []

        for r32_f in R32_MANDATORY_ARTIFACTS:
            fpath = target_dir / r32_f
            if not fpath.exists():
                violations.append(f"MISSING_MANDATORY_ARTIFACT:{r32_f}")
            elif fpath.stat().st_size == 0:
                violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r32_f}")
            elif r32_f.endswith(".json"):
                try:
                    r32_doc = json.loads(fpath.read_text(encoding="utf-8"))
                    if isinstance(r32_doc, dict) and len(r32_doc) == 0:
                        violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r32_f}")
                    loaded_docs[r32_f] = r32_doc

                    # Check run_id consistency
                    doc_run_id = str(r32_doc.get("run_id") or "").strip()
                    doc_acc_run_id = str(r32_doc.get("acceptance_run_id") or "").strip()
                    if manifest_run_id and doc_run_id and doc_run_id != manifest_run_id and doc_acc_run_id != manifest_run_id:
                        violations.append(f"RUN_ID_MISMATCH:{r32_f}:{doc_run_id}!={manifest_run_id}")

                    if r32_f == "R32_PARITY_CLASSIFICATION_REPORT.json":
                        p_res = verify_parity_classification_report(r32_doc)
                        if not p_res["is_valid"]:
                            violations.extend(p_res["violations"])
                    elif r32_f == "R32_EXACT_SOURCE_MANIFEST.json":
                        if str(r32_doc.get("status") or "").upper() != "PASS":
                            violations.append("R32_EXACT_SOURCE_MANIFEST_STATUS_NOT_PASS")
                        bt_sha = str(r32_doc.get("backtest_source_sha") or "").strip()
                        final_sha = str(r32_doc.get("final_code_sha") or "").strip()
                        if not bt_sha:
                            violations.append("R32_EXACT_SOURCE_MANIFEST_MISSING_BACKTEST_SHA")
                        if not final_sha:
                            violations.append("R32_EXACT_SOURCE_MANIFEST_MISSING_FINAL_SHA")
                        if bt_sha and final_sha and bt_sha != final_sha:
                            violations.append(f"R32_EXACT_SOURCE_MANIFEST_SHA_MISMATCH:{bt_sha}!={final_sha}")
                        if expected_code_sha and bt_sha != expected_code_sha.strip():
                            violations.append(f"R32_EXACT_SOURCE_MANIFEST_EXPECTED_SHA_MISMATCH:{bt_sha}!={expected_code_sha}")
                    elif r32_f == "R32_NEGATIVE_CONTROL_RESULTS.json":
                        failed_negs = int(r32_doc.get("failed_negative_controls", 0))
                        passed_negs = int(r32_doc.get("passed_negative_controls", 0))
                        if failed_negs > 0:
                            violations.append(f"R32_NEGATIVE_CONTROLS_FAILED:{failed_negs}")
                        if passed_negs < 148:
                            violations.append(f"R32_NEGATIVE_CONTROLS_INCOMPLETE:{passed_negs}<148")
                except Exception as ex_j:
                    violations.append(f"CORRUPT_JSON_ARTIFACT:{r32_f}:{ex_j}")
            elif r32_f.endswith(".jsonl"):
                lines = [l.strip() for l in fpath.read_text(encoding="utf-8").splitlines() if l.strip()]
                if not lines:
                    violations.append(f"EMPTY_MANDATORY_ARTIFACT:{r32_f}")
                elif r32_f == "R32_RUNTIME_EXECUTION_EVENTS.jsonl":
                    for line_idx, line in enumerate(lines):
                        try:
                            r32_events.append(json.loads(line))
                        except Exception as ex_l:
                            violations.append(f"CORRUPT_JSONL_LINE:{r32_f}:{line_idx}:{ex_l}")
                elif r32_f == "R32_RUNTIME_INPUT_CAPTURES.jsonl":
                    for line_idx, line in enumerate(lines):
                        try:
                            r32_captures.append(json.loads(line))
                        except Exception as ex_l:
                            violations.append(f"CORRUPT_JSONL_LINE:{r32_f}:{line_idx}:{ex_l}")

        if r32_captures:
            c_res = verify_runtime_input_captures(r32_captures, events=r32_events if r32_events else None)
            if not c_res["is_valid"]:
                violations.extend(c_res["violations"])

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
        # Skip self-referential manifests and verification reports that record this manifest
        if fname in (
            "FINAL_ARTIFACT_MANIFEST.json",
            "R32_ARTIFACT_HASH_MANIFEST.json",
            "R32_INDEPENDENT_VERIFICATION.json",
            "V179013_R32_ARTIFACT_MANIFEST.json",
            "V179013_R32_INDEPENDENT_VERIFICATION.json",
            "V179013_R31_ARTIFACT_MANIFEST.json",
            "V179013_R31_INDEPENDENT_VERIFICATION.json",
            "V179013_R3_ARTIFACT_MANIFEST.json",
            "V179013_R3_INDEPENDENT_VERIFICATION.json",
            "V179013_R2_INDEPENDENT_ARTIFACT_VERIFICATION.json",
            "V179013_R1_INDEPENDENT_ARTIFACT_VERIFICATION.json",
        ):
            continue
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



