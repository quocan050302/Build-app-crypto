# AURUM DESK V17.8.0.4 — ATR M1/M5 PROVENANCE INTEGRITY REPAIR

## 1. Executive Summary
- **Final Code SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Verification Gate:** `G81_ATR_M1_M5_PROVENANCE_INTEGRITY` (`PASS`)
- **Synthetic `atr_m1 * 2.1` Multiplier:** **COMPLETELY REMOVED** from `v17_8_backtest_runner.py` and `market_shadow_service.py`.

## 2. Provenance Contract
- **M1 ATR (Required):** Computed strictly from closed M1 candles (`close_time <= decision_ms`). Missing or non-positive M1 ATR fails closed with `REJECTED_MISSING_ATR_M1` / `ATR_MISSING`.
- **M5 ATR (Optional):** Computed strictly from closed M5 candles (`close_time <= decision_ms`).
  - When `>= 15` closed M5 bars exist: `atr_decision_status = "ACTUAL_ATR_M5"`, `atr_m5_available = True`.
  - When `< 15` closed M5 bars exist: `atr_decision_status = "OPTIONAL_M5_OMITTED"`, `atr_m5_available = False`, `atr_m5_value = None`, and `dyn_buf` uses `max(min_buf, k1 * atr_m1, k_spread * spread)`.
- **Historical Replay Breakdown:** `ACTUAL_ATR_M5 = 3839`, `OPTIONAL_M5_OMITTED = 0`.
