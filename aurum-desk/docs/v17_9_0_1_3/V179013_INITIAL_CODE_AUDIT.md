# V179013 INITIAL CODE AUDIT: HISTORICAL PRODUCTION DECISION PARITY HARDENING

**Repository:** `quocan050302/Build-app-crypto`  
**Application:** `aurum-desk/`  
**Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`  
**Master Base SHA:** `d4a70e0242ea952d12e805d76aa260a9f5d1306d`  
**Imported Feature SHA:** `39f67ac0c07c01ccb5eeaa84ff9a6244272b42d5`  
**Pre-Refactor Historical Reference:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` (V17.8.0.4)  
**Audit Date:** `2026-10-30`  
**Author:** Principal Trading Systems Architect & Independent QA Auditor  

---

## 1. Existing Behavior Reconstructed

The V17.9.0.1.2 foundation established:
- Pure canonical evaluation contract via `evaluate_canonical_production_decision` and `SharedStrategyEvaluator.evaluate_decision`.
- Isolated detached git worktree execution of historical commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` without current-tree oracle injection (`N51`–`N54`).
- Real post-decision 1m forward candles slicing from `historical_dataset_XAUUSDT.json.gz` eliminating synthetic forward candle fallbacks (`N55`–`N60`).
- Exact commit SHA and tree hash provenance verification (`N61`–`N64`).
- 11-invariant cache provenance validation for backtest trade ledgers (`N65`–`N68`).
- 70 negative acceptance controls (`N01`–`N70`).

However, five critical weaknesses and integrity risks remain in the decision-parity harness:
1. **Historical Subprocess Decision Authority**: In `_build_historical_subprocess_harness_code`, the historical neutral decision record is mapped from `raw_shadow = shadow_svc._evaluate_single_model_shadow(...)` instead of directly extracting the downstream decisions genuinely consumed by the historical Production orchestrator (`ScalpWatcherOrchestrator.evaluate_cycle`).
2. **Current-Tree Oracle Fallback in Parity Replay**: In `evaluate_historical_multi_runtime_parity()`, line 619 used `base_snap = iso_hist_decisions.get(scen_key) or IndependentPreRefactorBaselineOracle.evaluate_baseline_dict(...)`. This fallback can mask subprocess execution failures or missing records by evaluating current-tree code.
3. **Overwriting Raw Historical Input Hashes**: In `generate_v17_9_0_1_artifacts.py` lines 638–644, `base_snap["input_snapshot_hash"]` was overwritten with `shared_snap.get("input_snapshot_hash")` to force `validate_comparison_record_hashes` to pass. Raw source hashes must be preserved, and canonical semantic equivalence must be computed via a dedicated normalizer.
4. **Missing Explicit Distinct Denominators in Five-Way Accounting**: The five-way parity reporting must independently collect all 5 runtimes (Historical Production, SharedStrategyEvaluator, Current Production, MARKET_SHADOW, Backtest) and maintain separate denominators for characterization scenarios, real historical scenarios, comparable decisions, consumed decisions, and simulated trades.
5. **Historical Quote Provenance & Trade Reconciliation**: Reconstructed bid/ask quotes must be explicitly labeled (`RECONSTRUCTED_HISTORICAL_QUOTE` / `ASSUMED_SPREAD_MODEL`), and parity-window simulated trades must be reconciled with chronological backtest trades by unique event keys.

---

## 2. Exact Affected Functions and Callers

| File | Functions / Classes | Role / Vulnerability |
| :--- | :--- | :--- |
| `backend/services/v17_9_0_1_baseline_oracle.py` | `_build_historical_subprocess_harness_code()`, `execute_isolated_historical_baseline_scenarios()` | Builds harness script for isolated historical subprocess. Needs to trace and emit authoritative production consumed decisions and lifecycle states (`CANDIDATE_CONSUMED`, `ORDER_INTENT_CREATED`, `ORDER_BLOCKED`, `REJECTED`, `EVALUATED_NOT_CONSUMED`, `NOT_EXECUTED`, `NOT_COMPARABLE`). |
| `tools/generate_v17_9_0_1_artifacts.py` | `evaluate_historical_multi_runtime_parity()`, `generate_v17_9_0_1_artifacts()` | Parity evaluation and artifact generator. Contains current-tree oracle fallback (`or evaluate_baseline_dict`) and overwrites historical input hash. |
| `tools/acceptance_verdict.py` | `validate_comparison_record_hashes()`, `validate_semantic_acceptance_bundle()` | Hash validator and semantic bundle auditor. Must enforce raw hash preservation, canonical semantic hash matching, and failure on missing historical baseline. |
| `backend/services/v17_8_backtest_runner.py` | `extract_and_validate_real_forward_candles()`, `execute_chronological_backtest_window_for_parity()` | Forward candle validator and window simulator. Quote provenance must be explicitly stamped. |
| `backend/tests/test_v17_9_0_1_2_strict_eligibility_and_parity.py` | `run_all_v17_9_0_1_2_negative_acceptance_checks()` | Negative controls runner. Must be extended from 70 to 95 negative controls (`N71`–`N95`). |

---

## 3. What is Already Correct

- The canonical decision contract in `backend/services/canonical_production_decision_contract.py` correctly defines `evaluate_canonical_production_decision()`.
- Isolated detached worktree checkout of `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` functions properly and verifies commits without current-tree module leaks.
- Real M1 forward candles are causally sliced without synthetic `last_c +/- 0.30` bars.
- Bounded async pytest runner in `tools/run_v17_8_acceptance.py` safely runs 10 parallel shards with DB isolation.
- Operational database `backend/aurum_desk.db` is protected and verified unchanged.

---

## 4. Remaining Defect Matrix (V17.9.0.1.3 P0 Scope)

| Defect ID | Description | Root Cause | Repair Plan | Target Controls |
| :--- | :--- | :--- | :--- | :--- |
| **`DEF-179013-01`** | Historical Production Decision Authority & Consumed Lineage | Subprocess harness mapped `neutral_rec` from `raw_shadow` rather than extracting downstream consumed decisions from `ScalpWatcherOrchestrator.evaluate_cycle`. | Reconstruct historical cycle consumption end-to-end; trace `candidates`, `evaluate_smc_opportunity_candidates`, `model_diagnostics`, and `OpportunityEvaluation` records; emit full consumed lineage and lifecycle states. | `N71`, `N72`, `N73`, `N80`, `N82` |
| **`DEF-179013-02`** | Current-Tree Oracle Fallback in Parity Replay | `or IndependentPreRefactorBaselineOracle.evaluate_baseline_dict` masked missing subprocess results. | Remove fallback completely; report `HISTORICAL_BASELINE_MISSING` and fail closed on missing or corrupt historical baseline. | `N74`, `N81` |
| **`DEF-179013-03`** | Overwriting Raw Historical Input Hashes | `base_snap["input_snapshot_hash"]` was overwritten with `shared_snap`'s hash. | Preserve raw original hashes (`raw_input_hash`, `raw_config_hash`) for all 5 runtimes; compute `canonical_semantic_input_hash` and `canonical_semantic_config_hash` via versioned semantic normalizer; validate both. | `N75`, `N76`, `N77` |
| **`DEF-179013-04`** | Independent Five-Way Parity & Accounting Denominators | Five runtimes were not all cleanly separated and distinct denominators were conflated. | Independently execute and collect all 5 sources; report distinct denominators for scenarios, decisions, consumed decisions, and simulated trades; enforce zero synthetic matches. | `N78`, `N79` |
| **`DEF-179013-05`** | Historical Quote Provenance & Trade Reconciliation | Reconstructed quotes were not explicitly labeled; trade IDs lacked strict event-key mapping. | Stamp `quote_provenance: RECONSTRUCTED_HISTORICAL_QUOTE / ASSUMED_SPREAD_MODEL`; verify chronological trade linkage by unique event keys. | `N83`, `N84`, `N85`, `N86`, `N87` |
| **`DEF-179013-06`** | Extended Negative Controls Suite (`N01`–`N95`) | Negative controls stopped at N70; lacked tests for current-tree fallback, hash overwrite, worker crashes, and missing consumed lineage. | Implement `N71`–`N95` in `test_v17_9_0_1_3_hardening_and_parity.py` and register in acceptance suite. | `N71`–`N95` |

---

## 5. Smallest Safe Repair Scope

1. **`backend/services/v17_9_0_1_baseline_oracle.py`**:
   - Update `_build_historical_subprocess_harness_code` to extract historical orchestrator decisions, observe `orch_res` and `OpportunityEvaluation` in `mem_db`, emit authoritative production consumed decisions with full lifecycle (`CANDIDATE_CONSUMED`, `ORDER_INTENT_CREATED`, `ORDER_BLOCKED`, `REJECTED`, `EVALUATED_NOT_CONSUMED`, `NOT_EXECUTED`), downstream consumer names, and lineage provenance.
2. **`tools/generate_v17_9_0_1_artifacts.py`** (and V17.9.0.1.3 artifact generation):
   - Remove current-tree oracle fallback; handle missing historical baseline fail-closed.
   - Preserve all raw source hashes (`raw_input_hash`, `raw_configuration_hash`).
   - Implement canonical semantic normalization (`compute_canonical_semantic_input_hash`, `compute_canonical_semantic_config_hash`).
   - Stamp historical quote provenance (`RECONSTRUCTED_HISTORICAL_QUOTE`).
   - Generate all 14 required V17.9.0.1.3 artifacts in `docs/v17_9_0_1_3/` and `docs/`.
3. **`tools/acceptance_verdict.py`**:
   - Update `validate_comparison_record_hashes()` to check raw hash preservation and canonical semantic equivalence.
   - Extend `validate_semantic_acceptance_bundle()` to verify V17.9.0.1.3 artifacts and `N01`–`N95`.
4. **`backend/tests/test_v17_9_0_1_3_hardening_and_parity.py`**:
   - Implement `N71`–`N95` negative controls.
5. **`tools/run_v17_8_acceptance.py`**:
   - Update G100 to require 95 negative controls (`N01`–`N95`).

---

## 6. Existing Tests That Must Remain Unchanged

- All 436 backend test suites across `backend/tests/`.
- All 36 frontend Vitest test files.
- All Playwright browser E2E test files.
- Gates `G01` through `G107`.
- Negative controls `N01` through `N70` remain intact without modification of their behavior.

---

## 7. Expected Regression Risks & Mitigations

| Risk | Mitigation |
| :--- | :--- |
| Subprocess memory exhaustion when running 150 historical orchestrator scenarios | Subprocess reuses in-memory SQLite database across timestamps and clears per-cycle caches. |
| Incompatible hash formats between historical commit and current commit | Retain raw hashes as `raw_input_hash` and compute `canonical_semantic_input_hash` on normalized field tuples. |
| Cross-file DB locking in parallel shards | Maintain `--workers 10` with test ordering isolation and separate in-memory SQLite engines per thread/process. |
| Disk space consumption | Maintain active disk guard (`12 GB` warn, `8 GB` abort), stream logs, and clean up isolated worker directories upon completion. |

