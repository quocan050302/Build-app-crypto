# V17.9.0.1.3 — Acceptance Semantic Verification Report (`DEF-179012-C06`)

- **Final Code SHA (`FINAL_CODE_SHA`):** `2202a72a3a0cadc18bbbf5559bb94cb0ed3aafcd`
- **Expected Release Version:** `V17.9.0.1.3`
- **Semantic Verification Status:** `PASS` (`is_valid=True`)
- **Errors Count:** `0`
- **Release Version Mismatches:** `0`
- **SHA-256 Mismatches:** `0`
- **CRLF Files Detected:** `0`

## 1. Semantic Invariants Verified Across Generated Bundle
1. **Release Metadata Uniformity (`N50`):** Every JSON manifest/report carries `release == 'V17.9.0.1.3'`.
2. **95/95 Negative Controls (`N01`–`N95`):** `NEGATIVE_ACCEPTANCE_RESULTS.json` verifies `95/95` passed controls.
3. **Isolated Historical Authority (`N41`–`N42`, `N51`–`N54`):** `BASELINE_MANIFEST.json` and `HISTORICAL_EXECUTED_CALLABLE_MANIFEST.json` verify `copied_current_oracle_entry == False`, `current_oracle_source_injected == False`, and `real_historical_scenarios_count == 150`.
4. **Comparison Source Accounting & Real Forward Candles (`N38`–`N40`, `N55`–`N60`):** `ACTUAL_RUNTIME_PARITY_REPORT.json` and `REAL_FORWARD_CANDLE_PROVENANCE.json` verify `three_way_current_runtime == 210`, `four_way_current_plus_baseline == 210`, `five_way_verified == 210`, `actual_consumed_trade_lineage == 18`, and `fabricated_forward_candles_count == 0`.
5. **Non-Null Input/Config Hashes & Provenance (`N43`–`N46`, `N61`–`N70`):** All 150 historical records carry valid 64-hex `input_snapshot_hash`, `market_input_hash`, and `configuration_hash`, and `EXACT_COMMIT_EXECUTION_MANIFEST.json` + `LEDGER_CACHE_VALIDATION_REPORT.json` verify exact commit and 11-invariant cache provenance.
