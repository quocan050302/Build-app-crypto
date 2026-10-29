# AURUM DESK V17.8.0.4 — Production-Parity Real Historical Backtest Summary

- **Run ID**: `v17_8_0_4_bt_1793248253`
- **Tested Source SHA**: `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
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
| `total_opportunities` | 4672 | 4672 | 4672 | 4672 |
| `valid_setup_opportunities` | 3839 | 3839 | 3839 | 3839 |
| `eligible_opportunities` | 30 | 17 | 17 | 17 |
| `rejected_opportunities` | 4642 | 4655 | 4655 | 4655 |
| `simulated_trades` | 30 | 17 | 17 | 17 |
| `win_rate` | 0.1333 | 0.2941 | 0.2941 | 0.2941 |
| `loss_rate` | 0.8667 | 0.7059 | 0.7059 | 0.7059 |
| `net_pnl` | -52.6941 | -21.4507 | -21.3731 | -21.8244 |
| `gross_pnl` | -3.5563 | -3.7671 | -3.1595 | -5.4142 |
| `net_expectancy_R` | -0.7276 | -0.5235 | -0.5235 | -0.5322 |
| `average_win_R` | 0.8469 | 0.6203 | 0.6203 | 0.5906 |
| `average_loss_R` | 0.9698 | 1.0 | 1.0 | 1.0 |
| `profit_factor` | 0.1376 | 0.2632 | 0.2639 | 0.2493 |
| `max_drawdown` | 55.8225 | 21.4507 | 21.3731 | 21.8244 |
| `average_net_rr` | 4.6247 | 4.6067 | 4.6889 | 4.2901 |
| `average_stop_distance` | 1.8233 | 4.8471 | 4.6988 | 5.42 |
| `stop_distance_atr` | 1.0843 | 2.6412 | 2.5256 | 3.002 |
| `average_quantity` | 0.4863 | 0.31 | 0.3194 | 0.2876 |
| `fees_paid` | 47.6788 | 17.1566 | 17.6706 | 15.9212 |
| `slippage_cost` | 1.459 | 0.527 | 0.543 | 0.489 |
| `MAE` | 2.227 | 4.6321 | 4.6321 | 4.9944 |
| `MFE` | 3.25 | 5.5179 | 5.5179 | 5.5179 |
| `early_stopout_30s` | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC |
| `early_stopout_60s` | 8 | 0 | 0 | 0 |
| `early_stopout_rate_per_executed_trade` | 0.2667 | 0.0 | 0.0 | 0.0 |
| `early_stopout_rate_per_matched_trade` | 0.3529 | 0.0 | 0.0 | 0.0 |
| `early_stopout_180s` | 14 | 0 | 0 | 0 |
| `post_stop_recovery_rate` | 0.0 | 0.0 | 0.0 | 0.0 |
| `ambiguous_exits` | 0 | 0 | 0 | 0 |

## 3. Experiment Layer 1 — Matched SL-Only Cohort Comparison

- **Matched All A/B/C/D Cohort Opportunities**: `17`
- **Matched A vs C Cohort Opportunities**: `17`
- **Opportunities Saved From SL by Variant C vs Variant A**: `3`
- **Mean Paired Delta Net R (C minus A)**: `0.3032`
- **Total Paired Delta Net PnL (C minus A)**: `12.6424`

## 4. Experiment Layer 3E — Per-Model Breakdown

| Model ID | Valid Setups | A Trades | B Trades | C Trades | D Trades | C Net PnL | C Expectancy (R) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `SMC_M1_FVG_CONTINUATION` | 3218 | 26 | 14 | 14 | 14 | -14.1447 | -0.4213 |
| `SMC_M1_CONTINUATION` | 119 | 0 | 0 | 0 | 0 | 0 | None |
| `SMC_M1_REVERSAL` | 78 | 0 | 0 | 0 | 0 | 0 | None |
| `SMC_M1_SWEEP_RECLAIM` | 60 | 0 | 0 | 0 | 0 | 0 | None |
| `CG01_SWEEP_MSS_FVG` | 364 | 4 | 3 | 3 | 3 | -7.2284 | -1.0 |

## 5. Out-of-Sample Evaluation Summary

- **Chronological 60/20/20 OOS Verdict**: `OUT_OF_SAMPLE_INCONCLUSIVE`
- **OOS Opportunity Count (Final 20%)**: `768`
- **Variant A OOS Simulated Trades / Net Expectancy (R)**: `1` / `-0.2146`
- **Variant B OOS Simulated Trades / Net Expectancy (R)**: `0` / `None`
- **Variant C OOS Simulated Trades / Net Expectancy (R)**: `0` / `None`
- **Variant D OOS Simulated Trades / Net Expectancy (R)**: `0` / `None`
