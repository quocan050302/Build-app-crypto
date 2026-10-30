# AURUM DESK V17.8.0.3 — REGRESSION & NEGATIVE-CONTROL MATRIX

**Release:** `V17.8.0.3`  
**Test Suite:** `backend/tests/test_v17_8_0_3_market_shadow.py` (`11/11` passed), `frontend/src/__tests__/v17_8_0_1_sltp_intelligence.test.tsx` (`6/6` passed), `frontend/tests/e2e/v17_8_0_1_sltp_intelligence.e2e.ts` (`5/5` passed)

---

## 1. All 30 Mandatory Negative Controls (Phase I.1)

| # | Negative Control | Verification Test | Status |
|---|---|---|---|
| 1 | `MARKET_SHADOW` rejects `ScalpWatcherOrchestrator._arm_paper_candidate` | `test_02_market_shadow_execution_boundaries_forbidden` | `PASS` |
| 2 | `MARKET_SHADOW` rejects `ExecutionCoordinator.evaluate_orders_sync` | `test_02_market_shadow_execution_boundaries_forbidden` | `PASS` |
| 3 | `MARKET_SHADOW` rejects `ExecutionConsumer.evaluate_quote_sync` | `test_02_market_shadow_execution_boundaries_forbidden` | `PASS` |
| 4 | `MARKET_SHADOW` rejects `TradeLifecycleService.execute_fill` | `test_02_market_shadow_execution_boundaries_forbidden` | `PASS` |
| 5 | `MARKET_SHADOW` rejects `TradeLifecycleService.execute_close` | `test_02_market_shadow_execution_boundaries_forbidden` | `PASS` |
| 6 | `MARKET_SHADOW` rejects `TradeLifecycleService.process_exit_tick` | `test_02_market_shadow_execution_boundaries_forbidden` | `PASS` |
| 7 | `MARKET_SHADOW` rejects `crud.create_paper_order` | `test_02_market_shadow_execution_boundaries_forbidden` | `PASS` |
| 8 | `MARKET_SHADOW` rejects `crud.update_paper_order_state` | `test_02_market_shadow_execution_boundaries_forbidden` | `PASS` |
| 9 | `MARKET_SHADOW` rejects live exchange order dispatch | `test_02_market_shadow_execution_boundaries_forbidden` | `PASS` |
| 10 | `MARKET_SHADOW` rejects `POST /api/v1/orders/paper` (`403`) | `test_09_shadow_api_endpoints_and_http_mutation_rejection` | `PASS` |
| 11 | `MARKET_SHADOW` rejects `PUT /api/v1/policy/stop-placement` (`403`) | `test_09_shadow_api_endpoints_and_http_mutation_rejection` | `PASS` |
| 12 | `MARKET_SHADOW` rejects operational trading DB writes | `test_02` & `test_10_end_to_end_ws_to_shadow_db_to_api` | `PASS` |
| 13 | Shadow DB path equal to operational DB path fails closed | `test_06_shadow_research_storage_isolation_and_schema` | `PASS` |
| 14 | Shadow DB symlink/hardlink to operational DB fails closed | `test_06_shadow_research_storage_isolation_and_schema` | `PASS` |
| 15 | Unknown `AURUM_BE_RUNTIME_PROFILE` fails closed | `test_01_market_shadow_runtime_profile_capabilities` | `PASS` |
| 16 | Quote with `bid > ask` is rejected | `test_04_canonical_quote_validation_and_ordering_negative_controls` | `PASS` |
| 17 | Quote with missing `bid` or `ask` is rejected (no Last substitution) | `test_04_canonical_quote_validation_and_ordering_negative_controls` | `PASS` |
| 18 | Quote with `NaN`/`Inf`/`<= 0` is rejected | `test_04_canonical_quote_validation_and_ordering_negative_controls` | `PASS` |
| 19 | Stale quote is rejected for active opportunity eligibility | `test_07_continuous_unicorn_shadow_evaluation_and_anti_lookahead` | `PASS` |
| 20 | Out-of-order quote does not overwrite newer quote | `test_04_canonical_quote_validation_and_ordering_negative_controls` | `PASS` |
| 21 | Duplicate quote does not trigger duplicate evaluation | `test_04_canonical_quote_validation_and_ordering_negative_controls` | `PASS` |
| 22 | Unclosed M5/M15 candle is rejected for HTF Unicorn confirmation | `test_07_continuous_unicorn_shadow_evaluation_and_anti_lookahead` | `PASS` |
| 23 | Future candle (`close_time > decision_cutoff_ms`) is rejected | `test_07_continuous_unicorn_shadow_evaluation_and_anti_lookahead` | `PASS` |
| 24 | Breaker without overlapping FVG is rejected as Unicorn | `test_07_continuous_unicorn_shadow_evaluation_and_anti_lookahead` | `PASS` |
| 25 | Consumed FVG is rejected as Unicorn | `test_07_continuous_unicorn_shadow_evaluation_and_anti_lookahead` | `PASS` |
| 26 | Invalidated breaker distal boundary is rejected | `test_07_continuous_unicorn_shadow_evaluation_and_anti_lookahead` | `PASS` |
| 27 | Stop already breached by executable Bid/Ask is rejected | `test_07_continuous_unicorn_shadow_evaluation_and_anti_lookahead` | `PASS` |
| 28 | Missing M1 ATR fails closed (`SHADOW_WAITING_M1_ATR`) | `test_07_continuous_unicorn_shadow_evaluation_and_anti_lookahead` | `PASS` |
| 29 | Synthetic/fabricated historical candle fails data quality gate | `test_11_historical_data_quality_and_real_backtest_artifacts` | `PASS` |
| 30 | Intrabar SL+TP collision without tick ordering uses conservative rule | `test_08` & `test_11_historical_data_quality_and_real_backtest_artifacts` | `PASS` |

