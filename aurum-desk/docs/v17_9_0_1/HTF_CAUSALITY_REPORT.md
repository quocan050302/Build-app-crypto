# V17.9.0.1.2 — HTF Causality & Point-in-Time Data Correctness Report

- **Final Code SHA (`FINAL_CODE_SHA`):** `6416dcef31719cd8e72a8eef5c9d7a68dd2e2852`
- **Evaluator Version:** `17.9.0.1.1`

## 1. Point-in-Time Closed-Candle Validation (`validate_htf_candle_series` & `aggregate_closed_htf_candles`)
- Every `1H` (`3,600,000 ms`) and `4H` (`14,400,000 ms`) candle is verified for:
  - `is_closed == True` (`UNCLOSED_1H_CANDLE_REJECTED` / `UNCLOSED_4H_CANDLE_REJECTED` on partial bars)
  - `close_time <= decision_timestamp_ms` (`LOOKAHEAD_1H_CANDLE_REJECTED` / `LOOKAHEAD_4H_CANDLE_REJECTED` on future bars)
  - Exact UTC timeframe boundary alignment (`open_time % tf_ms == 0`, `close_time - open_time == tf_ms`)
  - Strictly increasing timestamps with zero duplicates and valid OHLC geometry (`high >= max(open, close)`, `low <= min(open, close)`)
- Verified by negative controls `N28` (unclosed HTF candle rejected) and `N29` (future HTF candle lookahead rejected).
