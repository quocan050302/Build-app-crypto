# V17.9.0.1.3 — Real Forward Candle Causality & Parity Simulation Report (`DEF-179012-F02`)

- **Final Code SHA (`FINAL_CODE_SHA`):** `21cc6876cd9e2a846adf96d22a4d2061b8bf4e98`
- **Dataset Artifact:** `backtest/historical_dataset_XAUUSDT.json.gz` (`6bcac947a842db06b4edb11e3db8164421dfcd27a3c0b9d6e571bf07543c3fb5`)
- **Decision Timestamps Evaluated:** `30` (`150` model evaluation windows)
- **Fabricated Forward Candles Count:** `0`
- **Synthetic Forward Candles Used:** `False`
- **Chronological Window Reconciliation Status:** `PASS` (`outcome_mismatches=0`)

## 1. Removal of Synthetic `last_c +/- 0.30` Fallback (`DEF-179012-F02`)
- Removed the synthetic 12-bar `last_c +/- 0.30` fallback from `execute_chronological_backtest_window_for_parity()` in `backend/services/v17_8_backtest_runner.py`.
- Enforced `validate_decision_context_no_future_leakage()` on decision-context candles (`close_time <= decision_timestamp_ms`) and `validate_forward_candles_for_simulation()` on post-decision simulation bars (`open_time >= decision_timestamp_ms`, strictly monotonic timestamps, valid OHLC geometry, zero synthetic flags).
- When `forward_1m` is missing or empty, `execute_chronological_backtest_window_for_parity()` records `outcome_status = 'NOT_EVALUATED'` and `missing_forward_data_reason = 'NO_REAL_FORWARD_CANDLES_PROVIDED'` rather than fabricating candles (`N55`–`N60`).
