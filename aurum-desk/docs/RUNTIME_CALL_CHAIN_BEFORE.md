# AURUM DESK V17.9.0.1 — RUNTIME CALL CHAIN AUDIT (BEFORE REPAIR)

See canonical artifact at `docs/v17_9_0_1/RUNTIME_CALL_CHAIN_BEFORE.md`.

- **Production (`ScalpWatcherOrchestrator.evaluate_cycle`, `backend/services/scalp_watcher_orchestrator.py:988-2445`):** Directly invoked `ScalpM1ModelService.evaluate_m1_*_candidate` and `evaluate_cg01_candidate` (`L1952-2140`), never calling `SharedStrategyEvaluator` in the live cycle loop.
- **MARKET_SHADOW (`MarketShadowService.evaluate_shadow_cycle` $\rightarrow$ `_evaluate_single_model_shadow`, `backend/services/market_shadow_service.py:1360-1737`):** Computed decisions via private `_detect_model_setup_evidence` (`L1433`) and only attached `shared_decision_snapshot` as passive post-hoc metadata (`L1708-1730`).
- **Historical Backtest (`_extract_historical_opportunities_chronologically`, `backend/services/v17_8_backtest_runner.py:780-1390`):** Used inline setup heuristics (`L1017-1231`) instead of invoking `SharedStrategyEvaluator`.

