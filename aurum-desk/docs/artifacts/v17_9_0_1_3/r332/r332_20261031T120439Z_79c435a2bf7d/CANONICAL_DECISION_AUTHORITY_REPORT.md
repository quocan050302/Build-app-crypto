# V17.9.0.1.2 — Canonical Decision Authority Report

- **Final Code SHA (`FINAL_CODE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Evaluator Version:** `17.9.0.1.1`
- **Cross-Runtime Parity Status:** `PASS` (`divergences=0`)

## 1. Sole Pre-Execution Strategy Decision Authority
`SharedStrategyEvaluator.evaluate_decision(...)` in `backend/services/shared_strategy_evaluator.py` is the single authoritative pre-execution strategy evaluator across all 5 production models (`SMC_M1_REVERSAL`, `SMC_M1_CONTINUATION`, `SMC_M1_FVG_CONTINUATION`, `SMC_M1_SWEEP_RECLAIM`, `CG01`).

## 2. Fail-Closed Authority Invariants Enforced (`N01`–`N50`)
1. **Strict Positive Eligibility (`N01`–`N16`):** Only `DecisionStatus.ELIGIBLE` with `validate_canonical_executability` can produce an executable candidate or simulated trade.
2. **Consumed Decision Lifecycle Reconciliation (`N31`–`N33`):** `ScalpWatcherOrchestrator.reconcile_consumed_decisions_from_snapshots` ensures only downstream-consumed candidates are recorded in `consumed_model_decisions`.
3. **Chronological Backtest Trade Lineage (`N34`–`N37`):** `reconcile_backtest_trade_decision_lineage` ensures 1-to-1 linkage between all 18 simulated trades and `ELIGIBLE` canonical decisions.
4. **Comparison Accounting & Original Historical Baseline (`N38`–`N42`):** All 210 comparisons execute all 5 sources, and isolated historical execution uses only modules extracted from `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`.
5. **Input/Config Hash Completeness & Semantic Acceptance (`N43`–`N50`):** Non-null 64-hex `input_snapshot_hash`, `market_input_hash`, and `configuration_hash` are enforced on every comparison record.
