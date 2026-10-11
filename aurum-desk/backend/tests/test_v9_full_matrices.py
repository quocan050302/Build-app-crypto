"""
Pytest integration for V9 Full System Test & Verification Matrices.
Executes all 42 scenarios across Matrices E, F, T, J, and B against isolated test harnesses.
"""
import pytest
from lab.v9_full_runner import V9ScenarioRunner
from lab.v9_test_harness import V9TestHarness

runner = V9ScenarioRunner(seed=42)
all_methods = runner.get_suite_methods("all")

@pytest.mark.parametrize("m", all_methods, ids=[m.__name__ for m in all_methods])
def test_v9_matrix_scenario(m):
    harness = V9TestHarness(seed=42)
    try:
        res = m(harness)
        if res.scenario_id == "T32":
            assert res.status in ("PASS", "BLOCKED")
        else:
            assert res.status == "PASS", f"Scenario {res.scenario_id} failed: {res.error}"
    finally:
        harness.teardown()
