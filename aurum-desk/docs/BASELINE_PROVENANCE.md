# V17.9.0.1 — Independent Baseline Provenance

## 1. Baseline Source Provenance
- **Pre-Refactor Git Commit SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` (V17.8.0.4 — last verified commit prior to V17.9.0 shared evaluator extraction).
- **Recovery Mechanism:** Extracted the direct pre-V17.9.0 evaluation pipeline (`ScalpM1ModelService`, `cg01_engine.evaluate_cg01_candidate`, `unicorn_evidence_service.detect_unicorn_evidence`, `stop_placement_service.resolve_stop_anchor`, `stop_placement_service.plan_structural_stop`, `domain_calculator.calculate_risk_reward`) into `backend/services/v17_9_0_1_baseline_oracle.py` (`IndependentPreRefactorBaselineOracle`).
- **Non-Self-Referential Guarantee:** `backend/services/v17_9_0_1_baseline_oracle.py` is AST-verified in regression tests to contain zero imports or invocations of `SharedStrategyEvaluator` or `shared_strategy_evaluator`.

## 2. Frozen Artifact Integrity
- `BASELINE_DECISIONS.jsonl` SHA-256: `92edbd743b1568c16cb01351d46d78db5e581569a8d2752c3ecb22863dc0b608`
- Total Frozen Decisions: `55` across all 11 mandatory categories and 5 production models.
