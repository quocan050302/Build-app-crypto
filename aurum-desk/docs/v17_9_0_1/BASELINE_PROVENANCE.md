# V17.9.0.1.1 — Independent Baseline Provenance

## 1. Baseline Source Provenance
- **Pre-Refactor Git Commit SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` (V17.8.0.4 — last verified commit prior to V17.9.0 shared evaluator extraction).
- **Git Commit Object Verified:** `True`
- **Recovery Mechanism:** Extracted the direct pre-V17.9.0 evaluation pipeline (`ScalpM1ModelService`, `cg01_engine.evaluate_cg01_candidate`, `unicorn_evidence_service.detect_unicorn_evidence`, `stop_placement_service.resolve_stop_anchor`, `stop_placement_service.plan_structural_stop`, `domain_calculator.calculate_risk_reward`) into `backend/services/v17_9_0_1_baseline_oracle.py` (`IndependentPreRefactorBaselineOracle`).
- **Non-Self-Referential Guarantee:** `backend/services/v17_9_0_1_baseline_oracle.py` is AST-verified in regression tests to contain zero imports or invocations of `SharedStrategyEvaluator` or `shared_strategy_evaluator`.

## 2. Historical Source File SHA-256 Digests at Commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`

| Historical File | Size (Bytes) | SHA-256 |
|---|---:|---|

## 3. Frozen Artifact Integrity (LF Byte-Exact)
- `BASELINE_DECISIONS.jsonl` SHA-256: `fdfc051ea34fd744685bb7f2a77a8e978de44f7959d259936ac292ec88e50905`
- `BASELINE_DECISIONS.jsonl` Size (Bytes): `78770`
- Total Frozen Decisions: `55` across all 11 mandatory categories and 5 production models.
