# V17.9.0.1.1 — Authoritative Acceptance Gate Matrix (`G01`–`G107`)

- **Final Code SHA (`FINAL_CODE_SHA`):** `0aa80ed0921662650807a42ec98292cfec83c874`
- **Acceptance Run Source SHA:** `0aa80ed0921662650807a42ec98292cfec83c874`
- **Gate Provenance Integrity:** `PASS` (`stale_gates=0`, `missing_gates=0`)
- **Top-Level Normalized Verdict:** `PASS`
- **Mandatory Gates Passed:** `107/107`

## 1. Repaired V17.9.0.1.1 Gate Mappings (`G89`–`G105`)

| Gate ID | Previous V17.9.0 Selector | Repaired V17.9.0.1.1 Selector | Min Passed | Actual Passed | Tested Source SHA | Normalized Status | Requirement |
|---|---|---|---:|---:|---|---|---|
| `G89_SHARED_DECISION_CONTEXT_CONTRACT` | `test_01 or test_02 or test_03` | `test_01 or test_02 or test_03 or test_04 or test_05 or test_v17_9_0_1_htf` | 5 | 6 | `0aa80ed09216` | `PASS` | MarketDecisionContext immutability, closed-bar causality, H4/H1 UTC boundary validation & quote checks |
| `G90_SHARED_DECISION_SNAPSHOT_CONTRACT` | `test_04 or test_05` | `test_06 or test_07 or test_08 or test_v17_9_0_1_canonical_decision_result` | 3 | 4 | `0aa80ed09216` | `PASS` | StrategyDecisionSnapshot / DecisionResult 20-field schema, evaluate_decision entrypoint & deterministic hashes |
| `G91_PRODUCTION_M1_REVERSAL_PARITY` | `test_06 or test_07` | `test_09 or test_14 or test_v17_9_0_1_reversal_parity` | 2 | 3 | `0aa80ed09216` | `PASS` | SMC_M1_REVERSAL independent pre-refactor baseline vs SharedStrategyEvaluator parity |
| `G92_PRODUCTION_M1_CONTINUATION_PARITY` | `test_08 or test_09` | `test_10 or test_14 or test_v17_9_0_1_continuation_parity` | 2 | 3 | `0aa80ed09216` | `PASS` | SMC_M1_CONTINUATION independent pre-refactor baseline vs SharedStrategyEvaluator parity |
| `G93_PRODUCTION_M1_FVG_CONTINUATION_PARITY` | `test_10 or test_11` | `test_11 or test_14 or test_v17_9_0_1_fvg_continuation_parity` | 2 | 3 | `0aa80ed09216` | `PASS` | SMC_M1_FVG_CONTINUATION independent pre-refactor baseline vs SharedStrategyEvaluator parity |
| `G94_PRODUCTION_M1_SWEEP_RECLAIM_PARITY` | `test_12 or test_13` | `test_12 or test_14 or test_v17_9_0_1_sweep_reclaim_parity` | 2 | 3 | `0aa80ed09216` | `PASS` | SMC_M1_SWEEP_RECLAIM independent pre-refactor baseline vs SharedStrategyEvaluator parity |
| `G95_PRODUCTION_CG01_PARITY` | `test_14` | `test_13 or test_14 or test_v17_9_0_1_cg01_parity` | 2 | 3 | `0aa80ed09216` | `PASS` | CG01_SWEEP_MSS_FVG independent pre-refactor baseline vs SharedStrategyEvaluator parity |
| `G96_PRODUCTION_SHADOW_DECISION_PARITY` | `test_15` | `test_15 or test_17 or test_v17_9_0_1_runtime_spy_production_and_shadow` | 2 | 3 | `0aa80ed09216` | `PASS` | Live runtime spy & decision parity between ScalpWatcherOrchestrator.evaluate_cycle and MarketShadowService.evaluate_shadow_cycle |
| `G97_PRODUCTION_BACKTEST_DECISION_PARITY` | `test_16 or test_17` | `test_16 or test_17 or test_18 or test_v17_9_0_1_runtime_spy_backtest` | 3 | 4 | `0aa80ed09216` | `PASS` | Live runtime spy & decision parity between Production, MARKET_SHADOW, and Chronological Backtest |
| `G98_SYNTHETIC_INDEX_SIGNAL_REMOVAL` | `test_18 or test_19` | `test_21 or test_22 or test_29` | 3 | 3 | `0aa80ed09216` | `PASS` | AST & runtime verification of zero modulo/index-driven synthetic signal triggers in backtest |
| `G99_UNICORN_CAUSAL_CHAIN_PARITY` | `test_20 or test_21 or test_22 or test_23` | `test_19 or test_20 or test_23 or test_24 or test_25 or test_v17_9_0_1_non_self_referential_oracle` | 5 | 6 | `0aa80ed09216` | `PASS` | Anti-fake parity trap detection, non-self-referential baseline oracle & Unicorn causal chain validation |
| `G100_STRUCTURAL_SL_TP_RR_PARITY` | `test_24 or test_25 or test_26 or test_27 or test_28 or test_29 or test_30` | `test_26 or test_27 or test_28 or test_v17_9_0_1_negative_acceptance` | 3 | 5 | `0aa80ed09216` | `PASS` | Structural SL anchor, R:R, quantity, rejection reason parity & 15 negative acceptance scenarios |
| `G101_BOUNDED_ASYNC_TEST_RUNNER_INTEGRITY` | `test_31 or test_32 or test_33` | `test_39 or test_v17_9_0_1_async_orchestrator` | 1 | 2 | `0aa80ed09216` | `PASS` | Bounded async multi-worker test orchestrator concurrency limit, failure accounting & zero silent skip |
| `G102_DISK_BUDGET_GUARD_AND_TEMP_CLEANUP` | `test_34 or test_35 or test_36` | `test_33 or test_34 or test_35 or test_v17_9_0_1_disk_guard` | 3 | 3 | `0aa80ed09216` | `PASS` | Disk budget guard preflight, fail-closed threshold, worker temp cleanup & log compression |
| `G103_CHECKPOINT_RESUME_AND_INVALIDATION` | `test_37 or test_38` | `test_36 or test_37 or test_38` | 3 | 3 | `0aa80ed09216` | `PASS` | Atomic execution state checkpoint resumption, SHA mismatch & corrupt JUnit XML invalidation |
| `G104_REAL_HISTORICAL_BACKTEST_SHARED_EVALUATOR` | `test_39` | `test_30 or test_31 or test_v17_9_0_1_chronological_backtest_invokes_shared_evaluator` | 2 | 3 | `0aa80ed09216` | `PASS` | Real chronological historical backtest invoking SharedStrategyEvaluator across all 5 production models |
| `G105_MATCHED_VS_FULL_STRATEGY_COHORT_INTEGRITY` | `test_40` | `test_32 or test_40 or test_v17_9_0_1_phase_b_55_scenario_parity` | 2 | 3 | `0aa80ed09216` | `PASS` | Matched SL-only cohort vs Complete Strategy cohort denominators, 55-scenario baseline parity & runtime safety |

## 2. Complete Mandatory Gate Inventory (`G01`–`G107`)

| Gate ID | Tested Source SHA | Raw Status | Normalized Status | Exit Code | Passed Tests | JUnit / Evidence Path |
|---|---|---|---|---:|---:|---|
| `G01_BRANCH_INTEGRITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `N/A` | `verified` |
| `G02_TEST_DB_BOOTSTRAP` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g02_test_db_bootstrap.xml` |
| `G03_UNSAFE_PATH_REJECTION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `9` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g03_unsafe_path_rejection.xml` |
| `G04_DESTRUCTIVE_FIXTURE_ISOLATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `5` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g04_destructive_fixture_isolation.xml` |
| `G05_RAW_ATR_PROVENANCE` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `8` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g05_raw_atr_provenance.xml` |
| `G06_REQUIRED_M1_ATR_FAIL_CLOSED` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `7` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g06_required_m1_atr_fail_closed.xml` |
| `G07_ATR_BUFFER_MATH_CORRECTNESS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g07_atr_buffer_math_correctness.xml` |
| `G08_STRUCTURAL_ANCHOR_CAUSAL` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `4` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g08_structural_anchor_causal.xml` |
| `G09_DISTANT_SL_FINANCIAL_INTEGRITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `4` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g09_distant_sl_financial_integrity.xml` |
| `G10_CG01_EVALUATOR_INTEGRATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g10_cg01_evaluator_integration.xml` |
| `G11_M1_EVALUATOR_INTEGRATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `7` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g11_m1_evaluator_integration.xml` |
| `G12_ORCHESTRATOR_REAL_ARM` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g12_orchestrator_real_arm.xml` |
| `G13_REAL_ARM_CONCURRENCY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g13_real_arm_concurrency.xml` |
| `G14_FILL_TIME_FINANCIAL_INVARIANTS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g14_fill_time_financial_invariants.xml` |
| `G15_PAPER_EXIT_AND_PERSISTENCE` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `4` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g15_paper_exit_and_persistence.xml` |
| `G16_LIVE_ORDER_SAFETY_BOUNDARY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g16.xml` |
| `G17_EXISTING_SL_DSL_CE_REGRESSION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `102` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g17_existing_sl_dsl_ce_regression.xml` |
| `G18_V17_5_REGRESSION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `278` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g18_v17_5_regression.xml` |
| `G19_V17_6_REGRESSION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `776` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g19_v17_6_regression.xml` |
| `G20_FULL_BACKEND_PYTEST` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `4303` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g20_full_backend_pytest.xml` |
| `G21_FRONTEND_VITEST` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `554` | `verified` |
| `G22_FRONTEND_BUILD` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `N/A` | `verified` |
| `G23_FRONTEND_LINT` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `N/A` | `verified` |
| `G24_PLAYWRIGHT_BROWSER_E2E` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `verified` |
| `G25_DETERMINISTIC_REPLAY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g25_deterministic_replay.xml` |
| `G26_ACCEPTANCE_RUNNER_NEGATIVE_TESTS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `9` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g26_acceptance_runner_negative_tests.xml` |
| `G27_ARTIFACT_SOURCE_SHA_INTEGRITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `verified` |
| `G28_DOCS_DEFECT_REGISTER_CONSISTENCY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `N/A` | `verified` |
| `G29_UNICORN_BREAKER_DETECTION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g29_unicorn_breaker_detection.xml` |
| `G30_UNICORN_FVG_OVERLAP` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g30_unicorn_fvg_overlap.xml` |
| `G31_CROSS_TIMEFRAME_CAUSALITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g31_cross_timeframe_causality.xml` |
| `G32_STRUCTURAL_INVALIDATION_AUTHORITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g32_structural_invalidation_authority.xml` |
| `G33_MODEL_SPECIFIC_SL_SELECTION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g33_model_specific_sl_selection.xml` |
| `G34_ATR_SPREAD_NOISE_PROTECTION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g34_atr_spread_noise_protection.xml` |
| `G35_LIQUIDITY_TP_INDEPENDENCE` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g35_liquidity_tp_independence.xml` |
| `G36_ENTRY_QUOTE_PROXIMITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g36_entry_quote_proximity.xml` |
| `G37_RISK_BUDGET_AND_NET_RR` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g37_risk_budget_and_net_rr.xml` |
| `G38_COMPLETE_ARM_FILL_EXIT` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `7` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g38_complete_arm_fill_exit.xml` |
| `G39_HISTORICAL_REPLAY_DETERMINISM` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g39_historical_replay_determinism.xml` |
| `G40_FEATURE_FLAG_AND_LEGACY_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `4` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g40_feature_flag_and_legacy_parity.xml` |
| `G41_NEGATIVE_CONTROLS_AND_ANTI_FABRICATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g41_negative_controls_and_anti_fabrication.xml` |
| `G42_EXACT_SOURCE_AND_ARTIFACT_INTEGRITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `N/A` | `verified` |
| `G43_UNICORN_RUNTIME_INTERFACE_CONTRACT` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g43_unicorn_runtime_interface_contract.xml` |
| `G44_FEATURE_FLAG_PERSISTENCE_AND_RELOAD` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g44_feature_flag_persistence_and_reload.xml` |
| `G45_FILL_TIME_PROTECTIVE_DISTANCE_GUARD` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g45_fill_time_protective_distance_guard.xml` |
| `G46_FILL_TIME_STOP_BREACH_QUOTE_GUARD` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g46_fill_time_stop_breach_quote_guard.xml` |
| `G47_CAUSAL_UNICORN_EPISODE_BINDING` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g47_causal_unicorn_episode_binding.xml` |
| `G48_ANTI_LOOKAHEAD_AND_UNCLOSED_CANDLE_REJECTION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g48_anti_lookahead_and_unclosed_candle_rejection.xml` |
| `G49_END_TO_END_RUNTIME_INTEGRATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g49_end_to_end_runtime_integration.xml` |
| `G50_FRONTEND_MOUNTED_SLTP_INTELLIGENCE_VIEW_E2E` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `5` | `verified` |
| `G51_FORENSIC_AUDIT_TRUTH_RECONCILIATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g51_forensic_audit_truth_reconciliation.xml` |
| `G52_HONEST_REPLAY_COMPARISON_DISCLOSURE` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g52_honest_replay_comparison_disclosure.xml` |
| `G53_OBSERVE_ONLY_STARTUP_ISOLATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g53_observe_only_startup_isolation.xml` |
| `G54_ZERO_BACKGROUND_ORDER_MUTATIONS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g54_zero_background_order_mutations.xml` |
| `G55_ZERO_LIVE_EXCHANGE_DISPATCH` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g55_zero_live_exchange_dispatch.xml` |
| `G56_OBSERVATION_DATABASE_IMMUTABILITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g56_observation_database_immutability.xml` |
| `G57_OBSERVATION_STARTUP_SHUTDOWN` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g57_observation_startup_shutdown.xml` |
| `G58_RELEASE_ARTIFACT_TRUTH_INTEGRITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `8` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g58_release_artifact_truth.xml` |
| `G59_MARKET_SHADOW_PROFILE_CONTRACT` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g59_market_shadow_profile_contract.xml` |
| `G60_MARKET_SHADOW_EXECUTION_FORBIDDEN` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g60_market_shadow_execution_forbidden.xml` |
| `G61_PUBLIC_MARKET_FEED_CONNECTED_OR_EXPLICIT_BLOCKER` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g61_public_market_feed_connected_or_explicit_blocker.xml` |
| `G62_CANONICAL_QUOTE_VALIDATION_AND_ORDERING` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g62_canonical_quote_validation_and_ordering.xml` |
| `G63_SHADOW_CANDLE_FINALITY_AND_CACHE` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g63_shadow_candle_finality_and_cache.xml` |
| `G64_SHADOW_DATABASE_ISOLATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g64_shadow_database_isolation.xml` |
| `G65_CONTINUOUS_UNICORN_SHADOW_EVALUATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g65_continuous_unicorn_shadow_evaluation.xml` |
| `G66_STRUCTURAL_SL_TP_SHADOW_DECISIONS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g66_structural_sl_tp_shadow_decisions.xml` |
| `G67_HISTORICAL_DATA_QUALITY_GATE` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g67_historical_data_quality_gate.xml` |
| `G68_REAL_HISTORICAL_BACKTEST_EXECUTION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g68_real_historical_backtest_execution.xml` |
| `G69_MATCHED_ABCD_COMPARISON_COMPLETENESS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g69_matched_abcd_comparison_completeness.xml` |
| `G70_OUT_OF_SAMPLE_AND_STRESS_VALIDATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g70_out_of_sample_and_stress_validation.xml` |
| `G71_SHADOW_API_READ_ONLY_CONTRACT` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g71_shadow_api_read_only_contract.xml` |
| `G72_FRONTEND_SHADOW_OBSERVATION_E2E` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `11` | `verified` |
| `G73_OPERATIONAL_DB_ZERO_MUTATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g73_operational_db_zero_mutation.xml` |
| `G74_MARKET_SHADOW_STARTUP_SHUTDOWN` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g74_market_shadow_startup_shutdown.xml` |
| `G75_EXACT_COMMIT_AND_ARTIFACT_TRUTH_VERIFICATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `8` | `verified` |
| `G76_REMOTE_ACCEPTANCE_LOG_COMPLETENESS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g76_remote_acceptance_log_completeness.xml` |
| `G77_GIT_REPRODUCIBLE_SOURCE_MANIFEST` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g77_git_reproducible_source_manifest.xml` |
| `G78_SOURCE_MANIFEST_NEGATIVE_CONTROLS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g78_source_manifest_negative_controls.xml` |
| `G79_PRODUCTION_BACKTEST_SHADOW_DECISION_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g79_production_backtest_shadow_decision_parity.xml` |
| `G80_UNICORN_CAUSAL_CHAIN_STRICT_VALIDATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g80_unicorn_causal_chain_strict_validation.xml` |
| `G81_ATR_M1_M5_PROVENANCE_INTEGRITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g81_atr_m1_m5_provenance_integrity.xml` |
| `G82_MATCHED_COHORT_STOP_OUT_ANALYSIS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g82_matched_cohort_stop_out_analysis.xml` |
| `G83_OOS_ZERO_TRADE_INSUFFICIENT_SAMPLE_HANDLING` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g83_oos_zero_trade_insufficient_sample_handling.xml` |
| `G84_TRADE_LEVEL_PNL_RECONCILIATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g84_trade_level_pnl_reconciliation.xml` |
| `G85_MARKET_SHADOW_ROUTING_REGRESSION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g85_market_shadow_routing_regression.xml` |
| `G86_BACKTEST_ANTI_BIAS_NEGATIVE_CONTROLS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `1` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g86_backtest_anti_bias_negative_controls.xml` |
| `G87_JUNIT_DOCUMENTATION_COUNT_RECONCILIATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `N/A` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/junit_reconciliation.json` |
| `G88_EXACT_COMMIT_REMOTE_EVIDENCE_VERIFICATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `N/A` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/logs/g88_exact_commit_evidence.log` |
| `G89_SHARED_DECISION_CONTEXT_CONTRACT` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `6` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g89_shared_decision_context_contract.xml` |
| `G90_SHARED_DECISION_SNAPSHOT_CONTRACT` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `4` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g90_shared_decision_snapshot_contract.xml` |
| `G91_PRODUCTION_M1_REVERSAL_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g91_production_m1_reversal_parity.xml` |
| `G92_PRODUCTION_M1_CONTINUATION_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g92_production_m1_continuation_parity.xml` |
| `G93_PRODUCTION_M1_FVG_CONTINUATION_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g93_production_m1_fvg_continuation_parity.xml` |
| `G94_PRODUCTION_M1_SWEEP_RECLAIM_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g94_production_m1_sweep_reclaim_parity.xml` |
| `G95_PRODUCTION_CG01_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g95_production_cg01_parity.xml` |
| `G96_PRODUCTION_SHADOW_DECISION_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g96_production_shadow_decision_parity.xml` |
| `G97_PRODUCTION_BACKTEST_DECISION_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `4` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g97_production_backtest_decision_parity.xml` |
| `G98_SYNTHETIC_INDEX_SIGNAL_REMOVAL` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g98_synthetic_index_signal_removal.xml` |
| `G99_UNICORN_CAUSAL_CHAIN_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `6` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g99_unicorn_causal_chain_parity.xml` |
| `G100_STRUCTURAL_SL_TP_RR_PARITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `5` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g100_structural_sl_tp_rr_parity.xml` |
| `G101_BOUNDED_ASYNC_TEST_RUNNER_INTEGRITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `2` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g101_bounded_async_test_runner_integrity.xml` |
| `G102_DISK_BUDGET_GUARD_AND_TEMP_CLEANUP` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g102_disk_budget_guard_and_temp_cleanup.xml` |
| `G103_CHECKPOINT_RESUME_AND_INVALIDATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g103_checkpoint_resume_and_invalidation.xml` |
| `G104_REAL_HISTORICAL_BACKTEST_SHARED_EVALUATOR` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g104_real_historical_backtest_shared_evaluator.xml` |
| `G105_MATCHED_VS_FULL_STRATEGY_COHORT_INTEGRITY` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `3` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/reports/g105_matched_vs_full_strategy_cohort_integrity.xml` |
| `G106_EXACT_COMMIT_SOURCE_MANIFEST_VERIFICATION` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `N/A` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/logs/g88_exact_commit_evidence.log` |
| `G107_REMOTE_ACCEPTANCE_BUNDLE_COMPLETENESS` | `0aa80ed09216` | `PASS` | `PASS` | `0` | `N/A` | `docs/artifacts/v17_9_0/run_v17_9_0_1793292391842_33371/logs/g88_exact_commit_evidence.log` |
