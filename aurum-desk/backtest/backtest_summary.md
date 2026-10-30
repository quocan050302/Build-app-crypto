# AURUM DESK V17.8.0.4 — Production-Parity Real Historical Backtest Summary

- **Run ID**: `v17_8_0_4_bt_1793334289`
- **Tested Source SHA**: `0d1d279392127334520b548798587731a939c78e`
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
| `valid_setup_opportunities` | 5776 | 5776 | 5776 | 5776 |
| `eligible_opportunities` | 41 | 25 | 18 | 24 |
| `rejected_opportunities` | 7215 | 7231 | 7238 | 7232 |
| `simulated_trades` | 41 | 25 | 18 | 24 |
| `win_rate` | 0.1463 | 0.28 | 0.3889 | 0.2917 |
| `loss_rate` | 0.8537 | 0.72 | 0.6111 | 0.7083 |
| `net_pnl` | -69.6 | -31.2265 | -16.3506 | -29.7228 |
| `gross_pnl` | -7.004 | -4.7743 | 3.7084 | -7.688 |
| `net_expectancy_R` | -0.7032 | -0.5179 | -0.3796 | -0.5114 |
| `average_win_R` | 0.7505 | 0.5953 | 0.5953 | 0.5463 |
| `average_loss_R` | 0.9524 | 0.9509 | 1.0 | 0.947 |
| `profit_factor` | 0.138 | 0.2483 | 0.3868 | 0.239 |
| `max_drawdown` | 69.6 | 31.2265 | 16.3506 | 29.7228 |
| `average_net_rr` | 4.4075 | 4.5552 | 5.6075 | 4.2055 |
| `average_stop_distance` | 2.39 | 4.6816 | 4.2611 | 5.785 |
| `stop_distance_atr` | 1.4768 | 2.7543 | 2.8145 | 3.4581 |
| `average_quantity` | 0.4534 | 0.3156 | 0.3322 | 0.2737 |
| `fees_paid` | 60.737 | 25.6632 | 19.461 | 21.3778 |
| `slippage_cost` | 1.859 | 0.789 | 0.598 | 0.657 |
| `MAE` | 2.7428 | 4.4562 | 3.8433 | 4.9354 |
| `MFE` | 3.521 | 5.499 | 6.7072 | 5.66 |
| `early_stopout_30s` | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC |
| `early_stopout_60s` | 8 | 0 | 0 | 0 |
| `early_stopout_rate_per_executed_trade` | 0.1951 | 0.0 | 0.0 | 0.0 |
| `early_stopout_rate_per_matched_trade` | 0.2778 | 0.0 | 0.0 | 0.0 |
| `early_stopout_180s` | 16 | 0 | 0 | 0 |
| `post_stop_recovery_rate` | 0.0 | 0.0 | 0.0 | 0.0 |
| `ambiguous_exits` | 0 | 0 | 0 | 0 |

## 3. Experiment Layer 1 — Matched SL-Only Cohort Comparison

- **Matched All A/B/C/D Cohort Opportunities**: `18`
- **Matched A vs C Cohort Opportunities**: `18`
- **Opportunities Saved From SL by Variant C vs Variant A**: `3`
- **Mean Paired Delta Net R (C minus A)**: `0.2836`
- **Total Paired Delta Net PnL (C minus A)**: `12.5401`

## 4. Experiment Layer 3E — Per-Model Breakdown

| Model ID | Valid Setups | A Trades | B Trades | C Trades | D Trades | C Net PnL | C Expectancy (R) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `SMC_M1_FVG_CONTINUATION` | 3161 | 26 | 14 | 10 | 13 | -4.439 | -0.1899 |
| `SMC_M1_CONTINUATION` | 1167 | 9 | 7 | 5 | 7 | -8.0231 | -0.6628 |
| `SMC_M1_REVERSAL` | 153 | 0 | 0 | 0 | 0 | 0 | None |
| `SMC_M1_SWEEP_RECLAIM` | 931 | 2 | 1 | 1 | 1 | 0.9418 | 0.3798 |
| `CG01_SWEEP_MSS_FVG` | 364 | 4 | 3 | 2 | 3 | -4.8303 | -1.0 |

## 5. Out-of-Sample Evaluation Summary

- **Chronological 60/20/20 OOS Verdict**: `OUT_OF_SAMPLE_INCONCLUSIVE`
- **OOS Opportunity Count (Final 20%)**: `1155`
- **Variant A OOS Simulated Trades / Net Expectancy (R)**: `1` / `-0.2146`
- **Variant B OOS Simulated Trades / Net Expectancy (R)**: `0` / `None`
- **Variant C OOS Simulated Trades / Net Expectancy (R)**: `0` / `None`
- **Variant D OOS Simulated Trades / Net Expectancy (R)**: `0` / `None`
