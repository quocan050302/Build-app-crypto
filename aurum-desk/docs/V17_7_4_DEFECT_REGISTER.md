# AURUM DESK V17.7.4 — DEFECT REGISTER
**Official Defect Tracking, Root Causes & Verification Proofs**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Reference HEAD:** `40f52d96f02b08ceaae6a2cfc9ff2673960dd949`

---

## 1. Defect Summary Table

| Defect ID | Severity | Component | Summary | Status | Verified Test Proof |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **DEF-1774-01** | P0 (Critical) | `stop_placement_types.py` | Weighted buffer components populated raw ATR in `StopBufferResult.__post_init__` | Closed / Verified | `test_atr_01_omitted_raw_fields_remain_none` |
| **DEF-1774-02** | P0 (Critical) | `stop_placement_service.py` | Missing authoritative ATR validator causing loose scalar overrides | Closed / Verified | `test_atr_04_scalar_only_cannot_arm_paper_structural` |
| **DEF-1774-03** | P1 (High) | `stop_placement_service.py` | Truthiness bug in `compute_stop_atr` including unclosed candles | Closed / Verified | `test_atr_11_candle_closure_flag_handled_safely` |
| **DEF-1774-04** | P1 (High) | `scalp_m1_model_service.py` | Unbound symbol and loose scalar ambiguity in M1 model evaluators | Closed / Verified | `test_atr_18_m1_strategy_uses_verified_atr` |
| **DEF-1774-05** | P0 (Critical) | `database.py` | Unverified marker file trusted without run-scoped provenance | Closed / Verified | `test_dbs_02_valid_metadata_marker_trusted` |
| **DEF-1774-06** | P0 (Critical) | `database.py` | Database isolation checked global `DB_PATH` instead of actual session/engine bind | Closed / Verified | `test_dbs_14_destructive_fixture_refuses_unsafe_target` |
| **DEF-1774-07** | P1 (High) | `conftest.py` | Premature startup migrations executed prior to test root isolation | Closed / Verified | `test_dbs_15_skip_startup_migrations_honored` |
| **DEF-1774-08** | P0 (Critical) | `acceptance_verdict.py` | Acceptance verdict allowed missing exit codes and permissive gate defaults | Closed / Verified | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` |
| **DEF-1774-09** | P0 (Critical) | `test_v17_7_adaptive_stop_loss.py` | Tautological self-verification in `test_ce_20` generating false confidence | Closed / Verified | `test_ce_20_acceptance_artifact_source_sha_matches_tested_source` |
| **DEF-1774-10** | P1 (High) | `run_v17_7_acceptance.py` | Acceptance runner overwrote historical release artifacts (`v17_7_2`, `v17_7_3`) | Closed / Verified | Run-scoped directory allocation under `docs/artifacts/v17_7_4/<run_id>/` |

---

## 2. Detailed Defect Dossiers

### DEF-1774-01: Weighted-to-Raw ATR Fallback in StopBufferResult
- **Component:** `backend/services/stop_placement_types.py::StopBufferResult`
- **Root Cause:** `__post_init__()` populated `raw_atr_m1` with `components.get("atr_m1")`. Because `components` stores weighted values (e.g. $0.25 \times 3.0 = 0.75$), downstream modules used scaled contributions as raw market volatility.
- **Resolution:** Removed fallback completely. Unset raw fields remain strictly `None`.
- **Test Node ID:** `test_v17_7_atr_provenance.py::test_atr_01_omitted_raw_fields_remain_none`

### DEF-1774-02: Missing Centralized Volatility Authority Validator
- **Component:** `backend/services/stop_placement_service.py`
- **Root Cause:** Planner, evaluators, and ARM boundary used fragmented validation rules, allowing unverified scalars to bypass volatility gates in `PAPER_STRUCTURAL` mode.
- **Resolution:** Implemented `validate_stop_atr_authority(...)` enforcing 12 validation rules with deterministic rejection reason codes.
- **Test Node ID:** `test_v17_7_atr_provenance.py::test_atr_04_scalar_only_cannot_arm_paper_structural`

### DEF-1774-03: Candle Closure Flag Loss Due to Truthiness Evaluation
- **Component:** `backend/services/stop_placement_service.py::compute_stop_atr`
- **Root Cause:** `getattr(bar, "is_closed", None) or ...` evaluated `False` as falsy, triggering fallbacks and improperly incorporating open candles.
- **Resolution:** Explicit check `if is_closed_val is False: continue` preserves unclosed candle exclusion.
- **Test Node ID:** `test_v17_7_atr_provenance.py::test_atr_11_candle_closure_flag_handled_safely`

### DEF-1774-04: Instrument Symbol and Timeframe Ambiguity in Model Calls
- **Component:** `backend/services/scalp_m1_model_service.py`
- **Root Cause:** Evaluator functions passed loose scalars without binding candidate instruments (`symbol="XAUUSDT"`), risking cross-instrument data leakage.
- **Resolution:** Eliminated scalar overrides in `PAPER_STRUCTURAL`; bound `symbol="XAUUSDT"` across Reversal, Continuation, FVG, and Sweep evaluators.
- **Test Node ID:** `test_v17_7_atr_provenance.py::test_atr_18_m1_strategy_uses_verified_atr`

### DEF-1774-05: Unverified Marker Ownership in Test Roots
- **Component:** `backend/database.py::get_or_create_test_root`
- **Root Cause:** Presence of `.aurum_test_owned` marker was trusted unconditionally without validating run identity or schema version.
- **Resolution:** Implemented Schema Version 1 JSON marker encapsulating `run_id`, `created_at_utc`, and `canonical_root`.
- **Test Node ID:** `test_v17_7_database_safety.py::test_dbs_02_valid_metadata_marker_trusted`

### DEF-1774-06: Database Guard Inspected Global Variable Rather Than Session Bind
- **Component:** `backend/database.py::assert_test_database_isolated`
- **Root Cause:** Guard checked global string `DB_PATH`, allowing local engine and session binds to bypass isolation checks.
- **Resolution:** Updated guard to accept `Engine`, `Session`, or URL, inspecting actual database connections before DDL or write operations.
- **Test Node ID:** `test_v17_7_database_safety.py::test_dbs_14_destructive_fixture_refuses_unsafe_target`

### DEF-1774-07: Module Import Time Migration Execution
- **Component:** `backend/database.py`
- **Root Cause:** Top-level `run_schema_migrations(engine)` ran when `database.py` was imported, before test isolation fixtures had initialized.
- **Resolution:** Added `AURUM_SKIP_STARTUP_MIGRATIONS` check, deferring migrations until isolation is verified.
- **Test Node ID:** `test_v17_7_database_safety.py::test_dbs_15_skip_startup_migrations_honored`

### DEF-1774-08: Permissive Acceptance Verdict Function
- **Component:** `tools/acceptance_verdict.py`
- **Root Cause:** `determine_release_verdict` permitted missing exit codes and used zero-defaults for missing counters.
- **Resolution:** Implemented `MANDATORY_GATES_V17_7_4` with strict fail-closed enforcement across all 28 gates.
- **Test Node ID:** `test_v17_7_adaptive_stop_loss.py::test_ce_19_acceptance_runner_refuses_pass_on_missing_gates`

### DEF-1774-09: Tautological Self-Verification in Acceptance SHA Test
- **Component:** `backend/tests/test_v17_7_adaptive_stop_loss.py::test_ce_20`
- **Root Cause:** Unit test constructed a mock dictionary containing the expected SHA and asserted equality against itself.
- **Resolution:** Replaced with an end-to-end verifier testing actual disk bundle structures, SHA-256 digests, and 8 distinct negative controls.
- **Test Node ID:** `test_v17_7_adaptive_stop_loss.py::test_ce_20_acceptance_artifact_source_sha_matches_tested_source`

### DEF-1774-10: Overwriting Historical Release Artifacts
- **Component:** `tools/run_v17_7_acceptance.py`
- **Root Cause:** Acceptance runs saved results directly to `v17_7_2_acceptance_results.json`, overwriting historical evidence.
- **Resolution:** Scoped all run outputs to `docs/artifacts/v17_7_4/<run_id>/`; historical artifacts are strictly immutable.
- **Test Node ID:** Acceptance runner lifecycle and filesystem immutability verification.

---
*Signed and Approved for Production Paper Opt-In Evaluation.*
