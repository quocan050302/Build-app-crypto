# V17.9.0.1.3 Defect Closure Matrix

- **Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`
- **Final Code SHA:** `2202a72a3a0cadc18bbbf5559bb94cb0ed3aafcd`
- **Historical Pre-Refactor Baseline:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`

| Defect ID | Severity | Root Cause | Actual Code Repair | Verification Tests & Negative Controls | Final Status |
|---|---|---|---|---|---|
| `DEF-179013-01` | P0 | Historical subprocess mapped decisions from `raw_shadow` rather than authentic orchestrator downstream consumption. | Reconstructed historical `ScalpWatcherOrchestrator.evaluate_cycle` execution; recorded `OpportunityFunnelRecord` stages; emitted full consumed lifecycle (`ORDER_INTENT_CREATED`, `CANDIDATE_CONSUMED`, `ORDER_BLOCKED`, `EVALUATED_NOT_CONSUMED`, `REJECTED`, `NOT_EXECUTED`). | `N71`, `N72`, `N73`, `N80`, `N82` | `CLOSED_VERIFIED` |
| `DEF-179013-02` | P0 | Parity replay contained fallback `or IndependentPreRefactorBaselineOracle.evaluate_baseline_dict` which could mask missing historical subprocess results. | Removed current-tree oracle fallback; records `HISTORICAL_BASELINE_MISSING` and fails closed on missing baseline. | `N74`, `N81` | `CLOSED_VERIFIED` |
| `DEF-179013-03` | P0 | Parity comparison overwrote `base_snap['input_snapshot_hash']` with `shared_snap`'s hash. | Preserved all raw source hashes (`raw_input_hash`, `raw_configuration_hash`) without overwriting; computed and verified `canonical_semantic_input_hash` and `canonical_semantic_config_hash`. | `N75`, `N76`, `N77` | `CLOSED_VERIFIED` |
| `DEF-179013-04` | P0 | Five runtimes were not all cleanly separated and distinct denominators were conflated. | Independently collected all 5 sources (`historical_baseline_oracle`, `shared_strategy_evaluator`, `production_runtime`, `market_shadow_runtime`, `chronological_backtest_runtime`); reported distinct denominators for scenarios, decisions, consumed decisions, and simulated trades. | `N78`, `N79` | `CLOSED_VERIFIED` |
| `DEF-179013-05` | P0 | Reconstructed bid/ask quotes were not explicitly disclosed; trade linkage lacked strict event keys. | Stamped `source: RECONSTRUCTED_HISTORICAL_QUOTE` and verified chronological trade linkage by unique event keys. | `N83`, `N84`, `N85`, `N86`, `N87` | `CLOSED_VERIFIED` |
| `DEF-179013-06` | P0 | Extended negative controls suite needed coverage for new historical parity, hash integrity, worker crash, and async runner traps. | Implemented and verified all 25 new negative controls `N71`–`N95` (total 95/95 passing). | `N71`–`N95` | `CLOSED_VERIFIED` |
