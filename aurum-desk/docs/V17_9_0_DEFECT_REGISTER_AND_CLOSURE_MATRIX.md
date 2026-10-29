# AURUM DESK V17.9.0 — Defect Register and Closure Matrix

- **Release Version**: `V17.9.0`
- **Branch**: `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Tested Executable Source SHA (`FINAL_CODE_SHA`)**: `76f725a232ab3bda5459b40efad95a08dfca7fd1`

---

## 1. V17.9.0 Defect Register & Closure Matrix

| Defect ID | Severity | Component / File | Root Cause Description | Source-Level Fix Applied | Verification Gates & Tests | Status |
|---|---|---|---|---|---|---|
| **DEF-17900-01** | **P0** | `backend/services/v17_8_backtest_runner.py` (`evaluate_production_parity_model_snapshot`) | `evaluate_production_parity_model_snapshot()` instantiated `MarketShadowService` and called `_evaluate_single_model_shadow()`, making parity tests compare `MarketShadowService` against a wrapper calling `MarketShadowService`. | Extracted `SharedStrategyEvaluator` (`backend/services/shared_strategy_evaluator.py`) and `characterize_production_baseline_decision()`. Updated `evaluate_production_parity_model_snapshot()` to use `SharedStrategyEvaluator` directly (`adapter_origin="HISTORICAL_BACKTEST_ADAPTER"`) and added anti-self-comparison guard (`SELF_COMPARISON_FORBIDDEN`). | `G89`–`G97`, `test_06`–`test_17`, `test_19` | **CLOSED & VERIFIED** |
| **DEF-17900-02** | **P0** | `backend/services/scalp_m1_model_service.py` (`evaluate_m1_fvg_continuation_candidate`) | Production `evaluate_m1_fvg_continuation_candidate()` checked only the single latest broken pivot for a matching FVG, whereas `MarketShadowService` iterated across earlier confirmed swing pivots in `closed_1m` when the latest broken pivot had no matching FVG. | Updated `evaluate_m1_fvg_continuation_candidate()` (`scalp_m1_model_service.py` lines 2269–2300) to fall back across earlier confirmed swing pivots in `closed_1m` that have valid causal displacement and a matching FVG. | `G93_PRODUCTION_M1_FVG_CONTINUATION_PARITY`, `test_10`, `test_11`, `test_30` | **CLOSED & VERIFIED** |
| **DEF-17900-03** | **P0** | `backend/services/v17_8_backtest_runner.py` (`_extract_historical_opportunities_chronologically`) | Historical backtest opportunity extractor used `i % 15 != 0`, `i % 15 == 0`, and `(i // 15) % 2 == 0` synthetic bar-index modulo rules to trigger evaluations and synthesize HTF zones / M1 liquidity clusters. | Removed all modulo index triggers from `_extract_historical_opportunities_chronologically()`, replacing them with causal price-action break/sweep detection and `SharedStrategyEvaluator` evaluation across all 5 production models. Added AST/regex source scanner `verify_no_synthetic_index_signals_in_source()`. | `G98_SYNTHETIC_INDEX_SIGNAL_REMOVAL`, `G104`, `test_18`, `test_19`, `test_39` | **CLOSED & VERIFIED** |
| **DEF-17900-04** | **P1** | `tools/run_v17_9_async_regression.py` & `tools/test_resource_guard.py` | Unbounded parallel test execution or uncleaned temporary SQLite databases/logs could exhaust disk space or leave unaccounted pytest nodes on interruption. | Created `TestResourceGuard` (`MIN_FREE_DISK_GB = 3.0`, `MAX_RUN_DISK_USAGE_GB = 1.5`, `MAX_SINGLE_LOG_MB = 25.0`, scoped `tmp/v17_9_0/<RUN_ID>/worker_<id>` cleanup, `.gz` log compression), `ExecutionStateManager` (`execution_state.json`), and `BoundedAsyncTestOrchestrator`. | `G101`, `G102`, `G103`, `test_31`–`test_38` | **CLOSED & VERIFIED** |

---

## 2. Historical Defects Preserved Closed (`V17.7` – `V17.8.0.4`)

All previously closed defects (`DEF-178-01` through `DEF-178-15`, `DEF-17802-01` through `DEF-17802-05`, `DEF-17803-01` through `DEF-17803-10`) remain closed and protected by gates `G01` through `G88`.

