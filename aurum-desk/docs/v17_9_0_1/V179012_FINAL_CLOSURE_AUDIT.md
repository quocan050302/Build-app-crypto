# V17.9.0.1.2 — Final Historical Parity, Consumed Decision Authority & Evidence Closure Audit

- **Branch:** `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Final Code SHA (`FINAL_CODE_SHA`):** `0aa80ed0921662650807a42ec98292cfec83c874`
- **Historical Pre-Refactor Reference Commit:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Evaluator Version:** `17.9.0.1.1`
- **Negative Acceptance Suite (`N01`–`N50`):** `50/50` (`status=PASS`)

## 1. Defect Closure Matrix (`DEF-179012-C01` through `DEF-179012-C06`)
| Defect ID | Priority | Root Cause | Repair Implemented | Negative Controls | Status |
|---|---|---|---|---|---|
| `DEF-179012-C01` | P0 | `ScalpWatcherOrchestrator.evaluate_cycle()` iterated over `shared_decision_snapshots` and recorded unconsumed snapshots in `consumed_model_decisions`. | Tracked `downstream_candidates_by_fp`, added lifecycle classification (`CANDIDATE_CONSUMED`, `ORDER_INTENT_CREATED`, `ORDER_BLOCKED`, `EVALUATED_NOT_CONSUMED`), and enforced `reconcile_consumed_decisions_from_snapshots()`. | `N31`, `N32`, `N33` | `CLOSED_VERIFIED` |
| `DEF-179012-C02` | P0 | Historical parity compared `evaluate_production_parity_model_snapshot()` adapter instead of the chronological Backtest simulation pipeline and trade ledger. | Added `execute_chronological_backtest_window_for_parity()` and `reconcile_backtest_trade_decision_lineage()`, verifying all 18 simulated trades link 1-to-1 to `ELIGIBLE` canonical decisions. | `N34`, `N35`, `N36`, `N37` | `CLOSED_VERIFIED` |
| `DEF-179012-C03` | P0 | 150 historical comparisons were counted in `five_way_compared_count` without executing all 5 sources on each historical record. | Executed all 5 sources (`historical_baseline_oracle`, `shared_strategy_evaluator`, `production_runtime`, `market_shadow_runtime`, `chronological_backtest_runtime`) across all 210 comparisons (`55` characterization + `5` live cycle + `150` historical) and enforced `validate_comparison_source_accounting()`. | `N38`, `N39`, `N40` | `CLOSED_VERIFIED` |
| `DEF-179012-C04` | P1 | `execute_isolated_historical_baseline_scenarios()` copied current-tree `v17_9_0_1_baseline_oracle.py` into `_oracle_runner/hist_oracle_entry.py` and evaluated only 55 synthetic scenarios. | Removed file copy (`copied_current_oracle_entry=False`), executed pure historical modules from `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`, and evaluated all 150 real historical market scenarios (`HISTORICAL_BASELINE_REAL_SCENARIOS.jsonl`). | `N41`, `N42` | `CLOSED_VERIFIED` |
| `DEF-179012-C05` | P1 | Historical comparison records could have null `input_snapshot_hash` / `configuration_hash` or pass parity when hashes were missing. | Added `_canonical_candle_series()`, `market_input_hash`, fail-closed `_resolve_parity_hash()`, and `validate_comparison_record_hashes()`. | `N43`, `N44`, `N45`, `N46` | `CLOSED_VERIFIED` |
| `DEF-179012-C06` | P0 | Acceptance validator needed semantic cross-artifact checks and uniform `V17.9.0.1.2` release metadata enforcement. | Added `validate_semantic_acceptance_bundle()` (`N47`–`N50`) and standardized all artifacts to `release = 'V17.9.0.1.2'`. | `N47`, `N48`, `N49`, `N50` | `CLOSED_VERIFIED` |
