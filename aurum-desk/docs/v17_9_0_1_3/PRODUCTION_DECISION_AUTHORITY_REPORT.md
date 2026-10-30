# V17.9.0.1.2 — Production Decision Authority Report (`DEF-179012-01` & `DEF-179012-C01`)

- **Final Code SHA (`FINAL_CODE_SHA`):** `21cc6876cd9e2a846adf96d22a4d2061b8bf4e98`
- **Evaluator Version:** `17.9.0.1.1`

## 1. Monotonic Domain Reconciliation (`_execute_production_domain_spec`)
- In `backend/services/shared_strategy_evaluator.py::_execute_production_domain_spec`, if `base_snap.decision_status != DecisionStatus.ELIGIBLE.value` or `base_snap.setup_validity != 'VALID'`, the legacy domain helper (`ScalpM1ModelService.evaluate_m1_*_candidate`) can **never** promote the snapshot to `DecisionStatus.ELIGIBLE`.
- Even when `base_snap.decision_status == DecisionStatus.ELIGIBLE.value`, any legacy domain evaluation is validated through `validate_canonical_executability` and `enforce_canonical_decision_authority_on_candidate` before returning.

## 2. Orchestrator Candidate Append, Routing, Consumed Lifecycle & Arming Guards (`ScalpWatcherOrchestrator`)
- Every Production model (`SMC_M1_REVERSAL`, `SMC_M1_CONTINUATION`, `SMC_M1_FVG_CONTINUATION`, `SMC_M1_SWEEP_RECLAIM`, `CG01`) requires `validate_canonical_executability(snap, context=shared_cycle_context)` at candidate append, routing, and `OrderManager.arm_candidate_order` boundaries.
- `consumed_model_decisions` records ONLY decisions that were actually consumed by a downstream candidate with matching `decision_id` and `decision_fingerprint` (`DEF-179012-C01`).
