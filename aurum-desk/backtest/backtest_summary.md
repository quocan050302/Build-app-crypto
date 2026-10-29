# AURUM DESK V17.8.0.4 — Production-Parity Real Historical Backtest Summary

- **Run ID**: `v17_8_0_4_bt_1793258805`
- **Tested Source SHA**: `2285b3f93a6c3516118feeb204ebe39caf408fdf`
- **Dataset SHA-256**: `6bcac947a842db06b4edb11e3db8164421dfcd27a3c0b9d6e571bf07543c3fb5`
- **Saved Dataset Artifact**: `backtest/historical_dataset_XAUUSDT.json.gz` (`9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`)
- **Reproducibility Status**: `REPLAY_REPRODUCIBLE_WITH_SAVED_DATA`
- **Backtest Verdict**: `REAL_HISTORICAL_BACKTEST_COMPLETED`
- **Data Quality Status**: `VERIFIED_HISTORICAL_CANDLES_WITH_DISCLOSED_GAPS`
- **Trading Authorization**: `MARKET_SHADOW_RESEARCH_ONLY` / `PAPER_AUTO_TRADING_NOT_AUTHORIZED_BY_V17_8_0_4` / `LIVE_TRADING_NOT_AUTHORIZED`

## 1. Historical Dataset Coverage & Limitations

- **Symbol**: `XAUUSDT` (`USDT-FUTURES`)
- **Requested Window**: `1785420000000` to `1793196000000` (90.0 calendar days)
- **Actual Verified M1 Window**: `1791437880000` to `1793195940000` (`22376` closed M1 bars, `20.35` calendar days)
- **Actual Verified M5 Bars**: `31609` closed bars (`112.95` calendar days)
- **Actual Verified M15 Bars**: `15090` closed bars (`160.95` calendar days)
- **Valid Historical Bid/Ask Ticks**: `0` (`OHLCV_CANDLES_ONLY_NO_HISTORICAL_BBO_TICKS`)
- **Missing M1 Intervals**: `6926` (`6347` session closures, `579` intra-session gaps; 0 synthetic bars fabricated)

## 2. Experiment Layer 3 — Complete Strategy Comparison (All Valid Setups)

| Metric | Variant A (Legacy) | Variant B (V17.7 Structural) | Variant C (V17.8 Unicorn) | Variant D (Noise Research) |
|---|---:|---:|---:|---:|
| `total_opportunities` | 7256 | 7256 | 7256 | 7256 |
| `valid_setup_opportunities` | 5884 | 5884 | 5884 | 5884 |
| `eligible_opportunities` | 41 | 25 | 25 | 24 |
| `rejected_opportunities` | 7215 | 7231 | 7231 | 7232 |
| `simulated_trades` | 41 | 25 | 25 | 24 |
| `win_rate` | 0.1463 | 0.28 | 0.28 | 0.2917 |
| `loss_rate` | 0.8537 | 0.72 | 0.72 | 0.7083 |
| `net_pnl` | -69.6 | -31.2265 | -31.1489 | -29.6353 |
| `gross_pnl` | -7.004 | -4.7743 | -4.1667 | -7.3693 |
| `net_expectancy_R` | -0.7032 | -0.5179 | -0.5179 | -0.5085 |
| `average_win_R` | 0.7505 | 0.5953 | 0.5953 | 0.546 |
| `average_loss_R` | 0.9524 | 0.9509 | 0.9509 | 0.9428 |
| `profit_factor` | 0.138 | 0.2483 | 0.2488 | 0.2394 |
| `max_drawdown` | 69.6 | 31.2265 | 31.1489 | 29.6353 |
| `average_net_rr` | 4.4075 | 4.5552 | 4.6111 | 4.2227 |
| `average_stop_distance` | 2.39 | 4.6816 | 4.5808 | 5.7237 |
| `stop_distance_atr` | 1.4768 | 2.7543 | 2.6757 | 3.4099 |
| `average_quantity` | 0.4534 | 0.3156 | 0.322 | 0.2767 |
| `fees_paid` | 60.737 | 25.6632 | 26.1772 | 21.602 |
| `slippage_cost` | 1.859 | 0.789 | 0.805 | 0.664 |
| `MAE` | 2.7428 | 4.4562 | 4.4562 | 4.9763 |
| `MFE` | 3.521 | 5.499 | 5.499 | 5.66 |
| `early_stopout_30s` | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC |
| `early_stopout_60s` | 8 | 0 | 0 | 0 |
| `early_stopout_rate_per_executed_trade` | 0.1951 | 0.0 | 0.0 | 0.0 |
| `early_stopout_rate_per_matched_trade` | 0.2083 | 0.0 | 0.0 | 0.0 |
| `early_stopout_180s` | 16 | 0 | 0 | 0 |
| `post_stop_recovery_rate` | 0.0 | 0.0 | 0.0 | 0.0 |
| `ambiguous_exits` | 0 | 0 | 0 | 0 |

## 3. Experiment Layer 1 — Matched SL-Only Cohort Comparison

- **Matched All A/B/C/D Cohort Opportunities**: `24`
- **Matched A vs C Cohort Opportunities**: `25`
- **Opportunities Saved From SL by Variant C vs Variant A**: `3`
- **Mean Paired Delta Net R (C minus A)**: `0.2043`
- **Total Paired Delta Net PnL (C minus A)**: `12.5331`

## 4. Experiment Layer 3E — Per-Model Breakdown

| Model ID | Valid Setups | A Trades | B Trades | C Trades | D Trades | C Net PnL | C Expectancy (R) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `SMC_M1_FVG_CONTINUATION` | 3218 | 26 | 14 | 14 | 13 | -14.1447 | -0.4213 |
| `SMC_M1_CONTINUATION` | 1218 | 9 | 7 | 7 | 7 | -10.7176 | -0.6328 |
| `SMC_M1_REVERSAL` | 153 | 0 | 0 | 0 | 0 | 0 | None |
| `SMC_M1_SWEEP_RECLAIM` | 931 | 2 | 1 | 1 | 1 | 0.9418 | 0.3798 |
| `CG01_SWEEP_MSS_FVG` | 364 | 4 | 3 | 3 | 3 | -7.2284 | -1.0 |

## 5. Out-of-Sample Evaluation Summary

- **Chronological 60/20/20 OOS Verdict**: `OUT_OF_SAMPLE_INCONCLUSIVE`
- **OOS Opportunity Count (Final 20%)**: `1177`
- **Variant A OOS Simulated Trades / Net Expectancy (R)**: `1` / `-0.2146`
- **Variant B OOS Simulated Trades / Net Expectancy (R)**: `0` / `None`
- **Variant C OOS Simulated Trades / Net Expectancy (R)**: `0` / `None`
- **Variant D OOS Simulated Trades / Net Expectancy (R)**: `0` / `None`
