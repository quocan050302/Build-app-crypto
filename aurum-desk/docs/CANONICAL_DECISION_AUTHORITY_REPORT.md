# V17.9.0.1.1 — Canonical Decision Authority Report

- **Final Code SHA (`FINAL_CODE_SHA`):** `397cf251584470a84f20d222702c5f6e038cf271`
- **Evaluator Version:** `17.9.0.1.1`
- **Cross-Runtime Parity Status:** `PASS` (`divergences=0`)

## 1. Sole Pre-Execution Strategy Decision Authority
`SharedStrategyEvaluator.evaluate_decision(...)` in `backend/services/shared_strategy_evaluator.py` is the single authoritative pre-execution strategy evaluator across all 5 production models (`SMC_M1_REVERSAL`, `SMC_M1_CONTINUATION`, `SMC_M1_FVG_CONTINUATION`, `SMC_M1_SWEEP_RECLAIM`, `CG01`).

## 2. Fail-Closed Authority Invariants Enforced (`Negative Tests A–G`)
1. **Negative Test A (`NO_TRADE` Override Prevention):** If `SharedStrategyEvaluator.evaluate_decision(...)` returns `NO_TRADE` or any non-executable status while a legacy helper returns an eligible candidate, `enforce_canonical_decision_authority_on_candidate` forces `is_eligible = False` and `strategy_decision_status = 'REJECT'`.
2. **Negative Test B (`REJECTED` Override Prevention):** If `SharedStrategyEvaluator.evaluate_decision(...)` returns `REJECTED` or `VALID_SETUP_REJECTED_BY_RISK`, legacy helper candidates are rejected with the canonical rejection reason codes.
3. **Negative Test C (Direction Mismatch Rejection):** If canonical `direction` disagrees with `expected_direction` or the legacy candidate's direction, the candidate is rejected with `CANONICAL_DIRECTION_MISMATCH_REJECTED`.
4. **Negative Test D (Missing Lineage Rejection):** Any candidate missing `decision_id` or `decision_fingerprint` is rejected fail-closed with `CANONICAL_DECISION_LINEAGE_MISSING`.
5. **Negative Test E (Invalid SL/TP Geometry Rejection):** Any candidate with `SL >= entry` or `TP <= entry` for `LONG` (or `SL <= entry` / `TP >= entry` for `SHORT`) is rejected fail-closed with `CANONICAL_SL_TP_GEOMETRY_REJECTED`.
6. **Negative Test F (Duplicate `decision_id` Replay Prevention):** `ScalpWatcherOrchestrator.register_seen_decision_id(decision_id)` blocks any duplicate `decision_id` from being armed twice (`DUPLICATE_CANONICAL_DECISION_ID_BLOCKED`).
7. **Negative Test G (Evaluator Exception Fail-Closed):** Any unhandled exception inside `SharedStrategyEvaluator.evaluate_decision(...)` is caught and converted into a fail-closed `DecisionStatus.REJECTED` result (`CANONICAL_EVALUATOR_EXCEPTION:<Type>`).
