"""
tools/acceptance_verdict.py
AURUM DESK V17.7.3 — Authoritative Release Verdict Resolution
Extracts verdict evaluation into a pure, fail-closed, testable module (V17.7.3 P1-03).
"""

from typing import Dict, List, Any, Optional

ALLOWED_GATE_STATUSES = {
    "PASS",
    "FAIL",
    "BLOCKED",
    "NOT_EXECUTED",
    "SKIPPED_WITH_REASON",
}

# The mandatory 28 gates for V17.7.3 release acceptance (Section 14)
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


def determine_release_verdict(
    actual_gate_results: Dict[str, Any],
    mandatory_gate_manifest: Optional[List[str]] = None,
) -> str:
    """
    Evaluates gate results against mandatory manifest with fail-closed semantics (V17.7.3 P1-03).
    Returns:
    - 'ACCEPTED_FOR_PAPER_OPT_IN': ALL mandatory gates are present and have status == 'PASS'
      with exit_code == 0 (where applicable) and no collection errors.
    - 'BLOCKED': Any mandatory gate is missing, failed, skipped, blocked, malformed, or exit code contradicts PASS.
    """
    manifest = mandatory_gate_manifest if mandatory_gate_manifest is not None else MANDATORY_GATES_V17_7_3

    if not actual_gate_results or not isinstance(actual_gate_results, dict):
        return "BLOCKED"

    # Check for missing mandatory gates
    for gate_id in manifest:
        if gate_id not in actual_gate_results:
            return "BLOCKED"

        gate_data = actual_gate_results[gate_id]
        if not isinstance(gate_data, dict):
            return "BLOCKED"

        status = gate_data.get("status")
        if status not in ALLOWED_GATE_STATUSES:
            return "BLOCKED"

        if status != "PASS":
            return "BLOCKED"

        # Check exit code consistency if recorded
        exit_code = gate_data.get("exit_code")
        if exit_code is not None and exit_code != 0:
            return "BLOCKED"

        # Check failed tests count if recorded
        counts = gate_data.get("counts")
        if isinstance(counts, dict):
            if counts.get("failed", 0) > 0:
                return "BLOCKED"
            if counts.get("collection_errors", 0) > 0:
                return "BLOCKED"

    return "ACCEPTED_FOR_PAPER_OPT_IN"
