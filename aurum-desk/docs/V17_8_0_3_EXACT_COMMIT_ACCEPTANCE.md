# AURUM DESK V17.8.0.3 — EXACT-COMMIT ACCEPTANCE & ARTIFACT TRUTH

**Release:** `V17.8.0.3`  
**Runner:** `tools/run_v17_8_acceptance.py`  
**Verifier:** `tools/artifact_verifier.py`  
**Verdict Engine:** `tools/acceptance_verdict.py`

---

## 1. 75-Gate Mandatory Acceptance Manifest (`MANDATORY_GATES_V17_8_0_3`)

- **Core Gates (`G01`–`G52`)**: All 52 V17.7.4 / V17.8.0 / V17.8.0.1 gates preserved without weakening.
- **Observation Safety Gates (`G53`–`G58`)**: All 6 V17.8.0.2 `OBSERVE_ONLY` gates preserved.
- **Market Shadow & Real Historical Backtest Gates (`G59`–`G75`)**:
  - `G59_MARKET_SHADOW_PROFILE_CONTRACT`
  - `G60_MARKET_SHADOW_EXECUTION_FORBIDDEN`
  - `G61_PUBLIC_MARKET_FEED_CONNECTED_OR_EXPLICIT_BLOCKER`
  - `G62_CANONICAL_QUOTE_VALIDATION_AND_ORDERING`
  - `G63_SHADOW_CANDLE_FINALITY_AND_CACHE`
  - `G64_SHADOW_DATABASE_ISOLATION`
  - `G65_CONTINUOUS_UNICORN_SHADOW_EVALUATION`
  - `G66_STRUCTURAL_SL_TP_SHADOW_DECISIONS`
  - `G67_HISTORICAL_DATA_QUALITY_GATE`
  - `G68_REAL_HISTORICAL_BACKTEST_EXECUTION`
  - `G69_MATCHED_ABCD_COMPARISON_COMPLETENESS`
  - `G70_OUT_OF_SAMPLE_AND_STRESS_VALIDATION`
  - `G71_SHADOW_API_READ_ONLY_CONTRACT`
  - `G72_FRONTEND_SHADOW_OBSERVATION_E2E`
  - `G73_OPERATIONAL_DB_ZERO_MUTATION`
  - `G74_MARKET_SHADOW_STARTUP_SHUTDOWN`
  - `G75_EXACT_COMMIT_AND_ARTIFACT_TRUTH_VERIFICATION`

---

## 2. Exact-Commit Discipline & Verified Evidence

- **`INITIAL_BASE_SHA`**: `f7f6de38b36cafefef7f3436c425ca57b1181f83`
- **`FINAL_CODE_SHA` (`tested_source_sha`)**: `3428ed3cef23c7cfad6a28abd78256682ceb3ece`
- **`RUN_ID`**: `run_v17_8_0_3_1793203227963_81705`
- **Source Composite SHA**: `5e56dfd6` (`888` files hashed)
- **Backtest Run ID**: `v17_8_0_3_bt_1793203152` (`source_sha = 3428ed3cef23c7cfad6a28abd78256682ceb3ece`)
- **Mandatory Gates Verified**: `75 / 75 PASS`
- **Leaf Artifacts Verified**: `153` leaf files (`errors = []`)
- **Backend Pytest (`G20`)**: `4182 passed, 8 skipped, 0 failed`
- **Frontend Vitest (`G21`)**: `554 passed (57 files), 0 failed`
- **Playwright Browser E2E (`G24` + `G50`)**: `8 passed (3 + 5), 0 failed`
- **Release Verdict**: `V17_8_0_3_ACCEPTED_FOR_SAFE_MARKET_SHADOW_AND_BACKTEST`

1. All executable code (`backend/`, `frontend/`, `tools/`) is committed first to establish `FINAL_CODE_SHA` (`3428ed3cef23c7cfad6a28abd78256682ceb3ece`).
2. `backend/services/v17_8_backtest_runner.py` and `tools/run_v17_8_acceptance.py` are executed against the clean `FINAL_CODE_SHA` tree so that `source_sha == tested_source_sha == FINAL_CODE_SHA` across `backtest/dataset_manifest.json` and `docs/artifacts/v17_8_0_3/run_v17_8_0_3_1793203227963_81705/acceptance_results.json`.
3. Only `backtest/` and `docs/` are committed in the final evidence commit (`FINAL_EVIDENCE_HEAD_SHA`), preserving `git diff <FINAL_CODE_SHA> <FINAL_EVIDENCE_HEAD_SHA> -- backend frontend tools == ""`.

