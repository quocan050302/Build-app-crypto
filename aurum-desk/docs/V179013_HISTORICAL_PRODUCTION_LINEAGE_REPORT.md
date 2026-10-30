# V17.9.0.1.3 Historical Production Decision Lineage & Consumed Authority Report

- **Final Code SHA:** `21cc6876cd9e2a846adf96d22a4d2061b8bf4e98`
- **Historical Commit:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Authoritative Historical Callables Executed:**
  * `services.market_shadow_service.MarketShadowService._evaluate_single_model_shadow`
  * `services.market_shadow_service.MarketShadowService.evaluate_shadow_cycle`
  * `services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_cycle`
  * `services.scalp_m1_model_service.ScalpM1ModelService` candidate evaluators
  * `services.cg01_engine.evaluate_cg01_candidate`

## Lifecycle State Transitions Across Historical Real Market Windows
- `EVALUATED_NOT_CONSUMED`: Model candidate was evaluated but not selected by router or not consumed downstream.
- `CANDIDATE_CONSUMED`: Candidate was consumed by shadow execution guard with valid plan.
- `ORDER_INTENT_CREATED`: Candidate was armed by `OrderManager.arm_candidate_order`.
- `ORDER_BLOCKED`: Candidate was blocked by execution risk guard.
- `REJECTED`: Candidate rejected by entry conditions or market regime.

- **Historical Consumed Decisions Count:** `139`
- **Evaluated Not Consumed Decisions Count:** `11`
