# V17.9.0.1.2 — Consumed Decision Authority & Lifecycle Report (`DEF-179012-C01`)

- **Final Code SHA (`FINAL_CODE_SHA`):** `a499f06951d50779a143828a10fb5ca48944d87c`
- **Evaluator Version:** `17.9.0.1.1`
- **Historical Cycles Verified:** `30` (`failed=0`)
- **Downstream Consumed Decisions (`is_consumed=True`):** `139`
- **Evaluated But Not Consumed Decisions (`EVALUATED_NOT_CONSUMED`):** `11`
- **Unconsumed Snapshots Reported as Consumed:** `0`
- **Reconciliation Status:** `PASS`

## 1. Downstream Consumption Lifecycle Stages
- `ORDER_INTENT_CREATED`: Canonical `ELIGIBLE` decision consumed by a downstream candidate and armed/created as an order intent.
- `ORDER_BLOCKED`: Canonical `ELIGIBLE` decision consumed by a downstream candidate and blocked by an execution/policy guard (e.g., `WATCH_ONLY`).
- `CANDIDATE_CONSUMED`: Canonical `ELIGIBLE` decision consumed by a downstream candidate in dry-run/evaluation mode.
- `EVALUATED_NOT_CONSUMED`: Canonical decision evaluated by `SharedStrategyEvaluator` (`NO_TRADE`, `VALID_SETUP_REJECTED_BY_RISK`, etc.) that produced no downstream actionable candidate and is strictly excluded from `consumed_model_decisions`.
