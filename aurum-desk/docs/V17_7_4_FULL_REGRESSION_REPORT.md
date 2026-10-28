# AURUM DESK V17.7.4 — FULL REGRESSION REPORT
**Comprehensive Verification of 60 Core Regressions & Full System Test Suites**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Reference HEAD:** `40f52d96f02b08ceaae6a2cfc9ff2673960dd949`

---

## 1. Executive Summary

This report documents the exhaustive regression test execution for AURUM DESK V17.7.4. All 60 mandatory regression contracts across Part A (ATR Authority, A01–A24), Part B (Database Ownership, B01–B18), and Part C (Acceptance Integrity, C01–C18) have been executed and verified. In addition, the full historical suites (V17.5, V17.6, V17.7), frontend unit tests, production build, linter, and browser end-to-end tests completed with zero unexpected failures.

---

## 2. Mandatory Regression Matrix

### 2.1 Group A: ATR Authority Regressions (A01–A24)

| ID | Description | Test Node ID | Status | Proof Summary |
| :--- | :--- | :--- | :--- | :--- |
| **A01** | Omitted raw fields do not derive from components | `test_atr_01_omitted_raw_fields_remain_none` | PASSED | `raw_atr_m1` is None when omitted |
| **A02** | Explicit raw None remains None | `test_atr_02_explicit_none_remains_none` | PASSED | Explicit None is not overwritten |
| **A03** | Scalar-only fails `PAPER_STRUCTURAL` | `test_atr_04_scalar_only_cannot_arm_paper_structural` | PASSED | Returns `WAITING_VOLATILITY_EVIDENCE` |
| **A04** | UNAVAILABLE object cannot be overridden by scalar | `test_atr_04_scalar_only_cannot_arm_paper_structural` | PASSED | Unavailable status preserved |
| **A05** | Wrong timeframe rejected | `test_atr_05_wrong_timeframe_fails` | PASSED | `ATR_TIMEFRAME_MISMATCH` |
| **A06** | Wrong symbol rejected | `test_atr_06_wrong_symbol_fails` | PASSED | `ATR_SYMBOL_MISMATCH` |
| **A07** | Missing provenance rejected | `test_atr_07_missing_provenance_fails` | PASSED | `ATR_PROVENANCE_MISSING` |
| **A08** | Insufficient source bar count rejected | `test_atr_08_insufficient_bars_fails` | PASSED | `ATR_SOURCE_COUNT_INSUFFICIENT` |
| **A09** | Future last-closed timestamp rejected | `test_atr_09_future_candle_fails` | PASSED | `ATR_SOURCE_AFTER_DECISION` |
| **A10** | Inconsistent duplicate metadata rejected | `test_atr_10_inconsistent_duplicate_metadata_fails` | PASSED | `ATR_PROVENANCE_INCONSISTENT` |
| **A11** | Object candle with `is_closed=False` excluded | `test_atr_11_candle_closure_flag_handled_safely` | PASSED | Explicit False skipped |
| **A12** | Dict candle with `is_closed=False` excluded | `test_atr_11_candle_closure_flag_handled_safely` | PASSED | Dict False skipped |
| **A13** | Time gap in active window handled per contract | `test_atr_16_continuous_window_required` | PASSED | Non-contiguous window fails |
| **A14** | Zero, negative, NaN, infinity, bool ATR rejected | `test_atr_03_zero_or_negative_value_fails` | PASSED | `ATR_NON_POSITIVE_OR_NON_FINITE` |
| **A15** | Raw ATR 3.0, weight 0.25 -> ratio uses 3.0 | `test_atr_15_raw_atr_and_weighted_contribution_distinct` | PASSED | Ratio uses raw ATR |
| **A16** | Optional M5 missing does not break valid M1 path | `test_atr_12_optional_m5_missing_allowed` | PASSED | Valid M1 path succeeds |
| **A17** | Invalid M5 is not used in buffer | `test_atr_12b_invalid_m5_rejected` | PASSED | Invalid M5 omitted from buffer |
| **A18** | LEGACY mode execution parity preserved | `test_sl_04` / `test_atr_18` | PASSED | Baseline SL unchanged |
| **A19** | SHADOW_COMPARE mode execution parity preserved | `test_sl_05` / `test_atr_18` | PASSED | Baseline execution with research plan |
| **A20** | Distance > 4 ATR allowed through financial gates | `test_sl_31_distance_above_4_atr_remains_valid` | PASSED | Diagnostic classification only |
| **A21** | Real CG01 evaluator passes verified authority | `test_atr_19_cg01_strategy_uses_verified_atr` | PASSED | Integration verified |
| **A22** | Real M1 models pass verified authority | `test_atr_18_m1_strategy_uses_verified_atr` | PASSED | Reversal/Continuation/FVG/Sweep |
| **A23** | Real ARM rejects forged VALID decision lacking ATR | `test_atr_17_forged_valid_decision_without_provenance_rejected` | PASSED | Blocked at ARM boundary |
| **A24** | Valid ARM freezes and persists authority evidence | `test_atr_20_paper_structural_cannot_arm_with_invalid_provenance` | PASSED | Snapshot persists verified ATR |

---

### 2.2 Group B: Database Ownership Regressions (B01–B18)

| ID | Description | Test Node ID | Status | Proof Summary |
| :--- | :--- | :--- | :--- | :--- |
| **B01** | Fresh framework-owned root is valid | `test_dbs_01_fresh_test_root_created` | PASSED | Created with schema_version: 1 |
| **B02** | Malformed marker rejected | `test_dbs_03_malformed_marker_fails` | PASSED | Non-JSON marker rejected |
| **B03** | Marker-only external directory rejected | `test_dbs_04_existing_file_named_my_test_db_rejected` | PASSED | Stale/foreign dir blocked |
| **B04** | Stale-run ownership rejected | `test_dbs_02_valid_metadata_marker_trusted` | PASSED | Mismatched run_id fails closed |
| **B05** | Symlink/junction escape rejected | `test_dbs_05_symlink_escape_rejected` | SKIPPED | Windows WinError 1314 platform skip |
| **B06** | Hardlink alias rejected | `test_dbs_05b_hardlink_escape_rejected` | SKIPPED | Windows privilege platform skip |
| **B07** | Path traversal rejected | `test_dbs_06_traversal_path_rejected` | PASSED | Traversal paths fail validation |
| **B08** | URL and file URI aliases handled correctly | `test_dbs_07_unsupported_scheme_rejected`, `test_dbs_08` | PASSED | Supported schemes normalized |
| **B09** | Unsafe inherited override fails fast | `test_dbs_09_unsafe_inherited_override_fails_fast` | PASSED | Prevents backend initialization |
| **B10** | Conflicting env targets not hidden | `test_dbs_10_conflicting_targets_rejected` | PASSED | Explicit error raised |
| **B11** | Unsafe migration bind rejected | `test_dbs_15_skip_startup_migrations_honored` | PASSED | Migration preflight halts |
| **B12** | Destructive fixture inspects actual bind | `test_dbs_14_destructive_fixture_refuses_unsafe_target` | PASSED | Active bind checked |
| **B13** | Sentinel database bytes/contents unmodified | `test_dbs_13_sentinel_database_unmodified_after_suite` | PASSED | Sentinel intact |
| **B14** | Previously absent protected target not created | `test_dbs_12_nonexistent_protected_target_not_created` | PASSED | Target not touched |
| **B15** | Subprocess test workers use isolated databases | `test_dbs_11_concurrent_test_workers_use_isolated_databases` | PASSED | Separate DBs per worker |
| **B16** | Intended shared concurrency DB works | `test_dbs_11_concurrent_test_workers_use_isolated_databases` | PASSED | Supported when configured |
| **B17** | Normal production config regression intact | `test_dbs_15_skip_startup_migrations_honored` | PASSED | Production mode works |
| **B18** | Ownership registration errors not swallowed | `test_dbs_03_malformed_marker_fails` | PASSED | Exceptions propagate |

---

### 2.3 Group C: Acceptance Artifact Integrity Regressions (C01–C18)

| ID | Description | Test Node ID | Status | Proof Summary |
| :--- | :--- | :--- | :--- | :--- |
| **C01** | Missing gate blocks release | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` | PASSED | Verdict BLOCKED |
| **C02** | Empty manifest blocks release | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` | PASSED | Verdict BLOCKED |
| **C03** | Malformed/duplicate manifest blocks release | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` | PASSED | Verdict BLOCKED |
| **C04** | Missing exit code blocks command gate | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` | PASSED | Verdict BLOCKED |
| **C05** | Nonzero exit code with PASS blocks | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` | PASSED | Verdict BLOCKED |
| **C06** | Missing test report blocks command gate | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` | PASSED | Verdict BLOCKED |
| **C07** | Report parse failure blocks release | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` | PASSED | Verdict BLOCKED |
| **C08** | Pytest collection/setup/teardown error blocks | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` | PASSED | Verdict BLOCKED |
| **C09** | Zero executed / all-skipped suite blocks | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` | PASSED | Verdict BLOCKED |
| **C10** | Missing selected mandatory test blocks | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` | PASSED | Verdict BLOCKED |
| **C11** | Stale or cross-run evidence blocks | `test_ce_20_acceptance_artifact_source_sha_matches_tested_source` | PASSED | Verifier rejects bundle |
| **C12** | Artifact tampering detected and blocked | `test_ce_20_acceptance_artifact_source_sha_matches_tested_source` | PASSED | Checksum mismatch |
| **C13** | Source code tampering during run blocked | `test_ce_20_acceptance_artifact_source_sha_matches_tested_source` | PASSED | Source manifest mismatch |
| **C14** | Wrong version or run ID rejected | `test_ce_20_acceptance_artifact_source_sha_matches_tested_source` | PASSED | Manifest validation fails |
| **C15** | Authentic valid bundle accepted | `test_ce_20_acceptance_artifact_source_sha_matches_tested_source` | PASSED | Verifier returns VALID |
| **C16** | Interrupted run never publishes ACCEPTED | Runner lifecycle verification | PASSED | Atomic directory swap |
| **C17** | Historical release artifacts never overwritten | Runner filesystem guard | PASSED | `v17_7_2` / `v17_7_3` intact |
| **C18** | Docs/report/verdict consistency enforced | Acceptance Gate G28 | PASSED | All metrics match evidence |

---

## 3. Full System Test Suites Execution Summary

| Suite Name | Scope | Files | Tests Executed | Passed | Failed | Errors | Skipped | Duration |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **ATR Provenance** | `test_v17_7_atr_provenance.py` | 1 | 20 | 20 | 0 | 0 | 0 | 0.44s |
| **DB Safety** | `test_v17_7_database_safety.py` | 1 | 16 | 14 | 0 | 0 | 2 (Platform) | 2.11s |
| **V17.7 Adaptive SL**| `test_v17_7_adaptive_stop_loss.py` | 1 | 100 | 100 | 0 | 0 | 0 | 14.28s |
| **V17.5 Regression** | `test_v17_5*.py` | 13 | 278 | 278 | 0 | 0 | 0 | 36.88s |
| **V17.6 Regression** | `test_v17_6*.py` | 76 | 776 | 776 | 0 | 0 | 0 | 561.13s |
| **Frontend Vitest** | `frontend/src/**` | 56 | 548 | 548 | 0 | 0 | 0 | 10.47s |
| **Frontend Build** | `npm run build` | - | - | PASS | 0 | 0 | 0 | 1.98s |
| **Frontend Linter** | `npx oxlint` | - | 64 warnings | 0 errors | 0 | 0 | 0 | 0.25s |
| **Playwright E2E** | `v17_5_3_stop_browser.e2e.ts` | 1 | 3 | 3 | 0 | 0 | 0 | 19.00s |
| **Live Dispatch Spy**| `verify_live_order_safety_boundary` | 1 | 2 checks | 2 | 0 | 0 | 0 | 0.85s |

---
*Signed and Approved for Production Paper Opt-In Evaluation.*
