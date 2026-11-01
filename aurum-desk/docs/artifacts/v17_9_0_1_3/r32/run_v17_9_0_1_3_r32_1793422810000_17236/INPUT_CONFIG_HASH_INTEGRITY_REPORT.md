# V17.9.0.1.2 — Input & Configuration Hash Integrity Report (`DEF-179012-C05`)

- **Final Code SHA (`FINAL_CODE_SHA`):** `ab3e82f376c0f0324571bf4b0b7405771f31559d`
- **Evaluator Version:** `17.9.0.1.1`
- **Historical Records Verified:** `150`
- **Null / Missing `input_snapshot_hash` Count:** `0`
- **Null / Missing `market_input_hash` Count:** `0`
- **Null / Missing `configuration_hash` Count:** `0`
- **Hash Integrity Status:** `PASS`

## 1. Deterministic Full-Series Hashing Contract
- `MarketDecisionContext.compute_input_snapshot_hash()` and `compute_market_input_hash()` serialize the full closed candle series (`_canonical_candle_series` across `1m`, `5m`, `15m`, `1h`, `4h`), quote bid/ask/spread, HTF bias, H1 alignment, liquidity pools, and session windows into canonical sorted-key JSON before SHA-256 hashing.
- `compare_decision_parity()` and `MarketShadowService.compare_production_shadow_divergence()` fail closed (`INPUT_MISMATCH` / `NULL_OR_MISSING_INPUT_OR_CONFIG_HASH`) whenever any input or configuration hash is null, empty, or mismatched.
