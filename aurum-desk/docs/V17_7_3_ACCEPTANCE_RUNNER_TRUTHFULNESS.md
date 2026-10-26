# AURUM DESK V17.7.3 — ACCEPTANCE RUNNER TRUTHFULNESS
**Authoritative Verdict Engine, 28-Gate Fail-Closed Manifest & Tested Source SHA Integrity**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Status:** `VERIFIED_FROM_UNIT_TEST`

---

## 1. Defect Analysis & Design Rationale

### 1.1 Previous Vulnerabilities (Defects V173-P1-04 & V173-P1-05)
In V17.7.2:
1. **Ad-hoc Verdict Logic**: Acceptance verdict logic was embedded directly in `tools/run_v17_7_acceptance.py`. If a mandatory gate was accidentally omitted from the execution list, it simply disappeared from the results dictionary, allowing `all_gates_passed` to evaluate to `True`.
2. **Superficial Unit Testing (CE-19)**: The test `test_ce_19` did not exercise the acceptance runner's code; it constructed a dummy local dictionary and computed a verdict with its own inline rules.
3. **Weak Commit SHA Verification (CE-20)**: The test `test_ce_20` merely asserted that a Git SHA was a 40-character hexadecimal string, rather than proving that `results["source_sha"]` matched the exact commit of the tested source tree.

---

## 2. Architecture of Authoritative Verdict Engine

In `tools/acceptance_verdict.py`, the verdict logic is extracted into a standalone, pure function:

```python
ALLOWED_GATE_STATUSES = {
    "PASS",
    "FAIL",
    "BLOCKED",
    "NOT_EXECUTED",
    "SKIPPED_WITH_REASON",
}

def determine_release_verdict(
    actual_gate_results: Dict[str, Any],
    mandatory_gate_manifest: Optional[List[str]] = None,
) -> str:
```

### 2.1 The Mandatory 28-Gate Manifest (`MANDATORY_GATES_V17_7_3`)
Release acceptance requires all 28 gates to be explicitly present and evaluate to `PASS`:
1. `G01_BRANCH_INTEGRITY`
2. `G02_TEST_DB_BOOTSTRAP`
3. `G03_UNSAFE_PATH_REJECTION`
4. `G04_DESTRUCTIVE_FIXTURE_ISOLATION`
5. `G05_RAW_ATR_PROVENANCE`
6. `G06_REQUIRED_M1_ATR_FAIL_CLOSED`
7. `G07_ATR_BUFFER_MATH_CORRECTNESS`
8. `G08_STRUCTURAL_ANCHOR_CAUSAL`
9. `G09_DISTANT_SL_FINANCIAL_INTEGRITY`
10. `G10_CG01_EVALUATOR_INTEGRATION`
11. `G11_M1_EVALUATOR_INTEGRATION`
12. `G12_ORCHESTRATOR_REAL_ARM`
13. `G13_REAL_ARM_CONCURRENCY`
14. `G14_FILL_TIME_FINANCIAL_INVARIANTS`
15. `G15_PAPER_EXIT_AND_PERSISTENCE`
16. `G16_LIVE_ORDER_SAFETY_BOUNDARY`
17. `G17_EXISTING_SL_DSL_CE_REGRESSION`
18. `G18_V17_5_REGRESSION`
19. `G19_V17_6_REGRESSION`
20. `G20_FULL_BACKEND_PYTEST`
21. `G21_FRONTEND_VITEST`
22. `G22_FRONTEND_BUILD`
23. `G23_FRONTEND_LINT`
24. `G24_PLAYWRIGHT_BROWSER_E2E`
25. `G25_DETERMINISTIC_REPLAY`
26. `G26_ACCEPTANCE_RUNNER_NEGATIVE_TESTS`
27. `G27_ARTIFACT_SOURCE_SHA_INTEGRITY`
28. `G28_DOCS_DEFECT_REGISTER_CONSISTENCY`

### 2.2 Strict Fail-Closed Verification Rules
The verdict function unconditionally returns `"BLOCKED"` if:
- Any mandatory gate is absent from `actual_gate_results`.
- Any mandatory gate has a status other than `"PASS"`.
- Any gate records an exit code other than `0`.
- Any gate records `counts["failed"] > 0` or `counts["collection_errors"] > 0`.
- Gate status is not in `ALLOWED_GATE_STATUSES`.

---

## 3. Negative-Control & SHA Integrity Proof (`test_ce_19` & `test_ce_20`)

In `backend/tests/test_v17_7_adaptive_stop_loss.py`:

### Test `test_ce_19_true_acceptance_runner_negative_controls`:
Directly calls `determine_release_verdict()` with controlled failure mutations:
1. **Missing Gate**: Removing `G06_REQUIRED_M1_ATR_FAIL_CLOSED` causes verdict to become `BLOCKED`.
2. **Skipped Gate**: Setting `status: "SKIPPED_WITH_REASON"` causes verdict to become `BLOCKED`.
3. **Failed Gate**: Setting `status: "FAIL"` causes verdict to become `BLOCKED`.
4. **Exit Code Contradiction**: Setting `status: "PASS"` with `exit_code: 1` causes verdict to become `BLOCKED`.
5. **Collection Error**: Setting `collection_errors: 1` causes verdict to become `BLOCKED`.
6. **Full Clean Pass**: All 28 mandatory gates with clean status return `ACCEPTED_FOR_PAPER_OPT_IN`.

### Test `test_ce_20_acceptance_source_sha_and_artifact_integrity`:
1. Executes `git rev-parse HEAD` to capture the true repository commit SHA.
2. Checks that any persisted acceptance artifact's `source_sha` matches `actual_head_sha` byte-for-byte.
3. Proves that tested code and recorded acceptance evidence cannot diverge.

### Execution Evidence:
```bash
./backend/venv/bin/python -m pytest backend/tests/test_v17_7_adaptive_stop_loss.py -k "test_ce_19 or test_ce_20" -v
============================= test session starts ==============================
collected 100 items / 98 deselected / 2 selected

backend/tests/test_v17_7_adaptive_stop_loss.py::test_ce_19_true_acceptance_runner_negative_controls PASSED [ 50%]
backend/tests/test_v17_7_adaptive_stop_loss.py::test_ce_20_acceptance_source_sha_and_artifact_integrity PASSED [100%]

======================= 2 passed, 98 deselected in 0.36s =======================
```
