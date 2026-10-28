# AURUM DESK V17.8.0.3 — HISTORICAL MARKET DATA QUALITY REPORT

**Release:** `V17.8.0.3`  
**Module:** `backend/services/v17_8_backtest_runner.py`  
**Machine Artifacts:** `backtest/dataset_manifest.json`, `backtest/data_quality_report.json`

---

## 1. Verified Historical Dataset Provenance

- **Source File**: `backend/aurum_desk.db` (read-only SQLite URI `file:...?mode=ro` extraction of real historical `XAUUSDT` `candles` rows)
- **Dataset SHA-256**: `e2c972875e4517c88873c1f6f533db5844ca66f3c2254cd2e720cb028f3dcfa7`
- **Overall Quality Gate Status**: `PASS`
- **Fabricated / Synthetic Candles Count**: `0`

---

## 2. Per-Timeframe Coverage & Integrity Metrics

| Timeframe | Closed Bars | First Open (ms) | Last Close (ms) | Span (Days) | Duplicates | Invalid OHLC | Missing Intervals | Fabricated Bars |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `1M` | `16,645` | `1773960420000` | `1775256960000` | `15.01` | `0` | `0` | `4,968` | `0` |
| `5M` | `32,602` | `1758182700000` | `1775257200000` | `197.62` | `0` | `0` | `24,313` | `0` |
| `15M` | `15,509` | `1758183600000` | `1775257500000` | `197.61` | `0` | `0` | `3,462` | `0` |

---

## 3. Data Quality Gate Invariants Verified

1. **Strict Monotonic Timestamps**: `0` out-of-order or duplicate timestamps across `1M`, `5M`, and `15M`.
2. **Valid OHLCV Geometry**: `0` rows with `NaN`, `Inf`, `<= 0` prices, negative volume, or `high < max(open, close, low)` / `low > min(open, close, high)`.
3. **Honest Gap Disclosure**: Weekend market closures and historical feed gaps are explicitly counted (`4,968` M1 intervals, `24,313` M5 intervals, `3,462` M15 intervals) and never interpolated or filled with synthetic bars.
4. **Anti-Fabrication Negative Control**: `run_historical_data_quality_gate()` fails closed with `overall_status = "FAIL"` whenever any bar has `source` in `("SYNTHETIC", "FABRICATED", "RANDOM")` or `is_synthetic=True`, or when invalid OHLC geometry is injected.

