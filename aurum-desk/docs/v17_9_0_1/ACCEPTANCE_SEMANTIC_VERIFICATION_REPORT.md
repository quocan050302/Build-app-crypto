# V17.9.0.1.2 — Acceptance Semantic Verification Report (`DEF-179012-C06`)

- **Final Code SHA (`FINAL_CODE_SHA`):** `0aa80ed0921662650807a42ec98292cfec83c874`
- **Expected Release Version:** `V17.9.0.1.2`
- **Semantic Verification Status:** `PASS` (`is_valid=True`)
- **Errors Count:** `0`
- **Release Version Mismatches:** `0`
- **SHA-256 Mismatches:** `0`
- **CRLF Files Detected:** `0`

## 1. Semantic Invariants Verified Across Generated Bundle
1. **Release Metadata Uniformity (`N50`):** Every JSON manifest/report carries `release == 'V17.9.0.1.2'`.
2. **50/50 Negative Controls (`N01`–`N50`):** `NEGATIVE_ACCEPTANCE_RESULTS.json` verifies `50/50` passed controls.
3. **Isolated Historical Authority (`N41`–`N42`):** `BASELINE_MANIFEST.json` verifies `copied_current_oracle_entry == False` and `real_historical_scenarios_count == 150`.
4. **Comparison Source Accounting (`N38`–`N40`):** `ACTUAL_RUNTIME_PARITY_REPORT.json` verifies `three_way_current_runtime == 210`, `four_way_current_plus_baseline == 210`, `five_way_verified == 210`, `actual_consumed_trade_lineage == 18`.
5. **Non-Null Input/Config Hashes (`N43`–`N46`):** All 150 historical records carry valid 64-hex `input_snapshot_hash`, `market_input_hash`, and `configuration_hash`.
