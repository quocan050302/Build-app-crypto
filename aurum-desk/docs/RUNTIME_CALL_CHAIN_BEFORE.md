# V17.9.0.1.1 — Runtime Call-Chain Audit (Before V17.9.0.1.1 Repair)

## 1. Pre-Repair Defects Identified (`DEF-V179011-01` through `DEF-V179011-06`)
1. **`DEF-V179011-01` (Production Decision Authority Split):** `SharedStrategyEvaluator.evaluate_production_*_candidate` called `evaluate_decision(...)`, but then delegated to `ScalpM1ModelService.evaluate_m1_*_candidate` / `evaluate_cg01_candidate` without enforcing that the legacy helper could not override canonical `NO_TRADE` / `REJECTED` / direction / SL / TP.
2. **`DEF-V179011-02` (`MARKET_SHADOW` Authority & Input-Mismatch Classification):** `MarketShadowService._evaluate_single_model_shadow` rebuilt fields from intermediate dicts instead of the canonical `DecisionResult`, and `compare_production_shadow_divergence` did not separate `INPUT_MISMATCH` from `DECISION_DIVERGENCE`.
3. **`DEF-V179011-03` (Backtest Canonical Authority & Financial Table Mapping):** `v17_8_backtest_runner._extract_historical_opportunities_chronologically` omitted `liquidity_pools` in `bt_shared_ctx` and `tools/generate_v17_9_0_1_artifacts.py` read `total_net_pnl_usd` instead of `net_pnl`.
4. **`DEF-V179011-04` (Hardcoded Parity Counts):** `tools/generate_v17_9_0_1_artifacts.py` hardcoded `production_vs_shadow_divergences = 0`, `production_vs_backtest_divergences = 0`, and `status = 'PASS'`.
5. **`DEF-V179011-05` (Stale `tested_source_sha` Overwrite):** `tools/generate_v17_9_0_1_artifacts.py` overwrote `tested_source_sha` on prior gate results when re-running only `G89`–`G105`.
6. **`DEF-V179011-06` (CRLF vs LF Checksum Drift):** Writing text files with `.write_text()` on Windows converted `\n` to `\r\n` on disk while hashing in-memory `\n` strings.
