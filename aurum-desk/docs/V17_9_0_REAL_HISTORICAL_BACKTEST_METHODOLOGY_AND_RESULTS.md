# AURUM DESK V17.9.0 — Real Historical Backtest Methodology and Results

- **Release Version**: `V17.9.0`
- **Branch**: `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Tested Executable Source SHA (`FINAL_CODE_SHA`)**: `76f725a232ab3bda5459b40efad95a08dfca7fd1`
- **Backtest Run ID**: `v17_8_0_4_bt_1793257304`
- **Dataset SHA-256**: `6bcac947a842db06b4edb11e3db8164421dfcd27a3c0b9d6e571bf07543c3fb5`
- **Saved Dataset Snapshot**: `backtest/historical_dataset_XAUUSDT.json.gz` (`9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`)
- **Backtest Methodology Verdict**: `REAL_HISTORICAL_PRODUCTION_PARITY_BACKTEST_VERIFIED`
- **Strategy Performance Verdict**: `PERFORMANCE_IMPROVED_OVER_LEGACY_STILL_NEGATIVE_OR_INCONCLUSIVE`
- **Trading Authorization**: `AUTO_PAPER_NOT_AUTHORIZED_BY_V17_9_0` / `LIVE_TRADING_NOT_AUTHORIZED`

---

## 1. Removal of Synthetic Index Triggers (`G98_SYNTHETIC_INDEX_SIGNAL_REMOVAL`)

In V17.9.0, all modulo-index trigger rules (`i % 15 != 0`, `i % 15 == 0`, `(i // 15) % 2 == 0`) were removed from `_extract_historical_opportunities_chronologically()` in `backend/services/v17_8_backtest_runner.py`.
- Candidate opportunities are now detected strictly from causal price-action break/sweep events on closed M1 bars (`c_high > prior_max_h`, `c_low < prior_min_l`, or swing-pivot break with body displacement).
- Model evaluation is performed by `SharedStrategyEvaluator` (`backend/services/shared_strategy_evaluator.py`) using causal HTF `WatchedZone` proximity (`15M`/`5M` FVG / order-block zones) and causal M1 `LiquidityCluster` proximity (`build_liquidity_clusters`).
- Verified by `verify_no_synthetic_index_signals_in_source()` (`G98`).

---

## 2. Verified Historical Dataset Coverage

- **Symbol**: `XAUUSDT` (`USDT-FUTURES`)
- **Closed `1M` Bars**: `22,376` (`20.35` calendar days, `1791437880000` to `1793195940000`)
- **Closed `5M` Bars**: `31,609` (`112.95` calendar days)
- **Closed `15M` Bars**: `15,090` (`160.95` calendar days)
- **Missing `1M` Intervals**: `6,926` (`6,347` weekend/holiday session closures + `579` intra-session gaps; `0` synthetic bars fabricated)
- **Causal Opportunities Inspected**: `7,256` (`5,884` valid setup opportunities + `1,372` early stage rejections)

---

## 3. Complete Strategy Comparison (Experiment Layer 3)

| Metric | Variant A (Legacy) | Variant B (V17.7 Structural) | Variant C (V17.8 Unicorn) | Variant D (Noise Research) |
|---|---:|---:|---:|---:|
| `total_opportunities` | 7,256 | 7,256 | 7,256 | 7,256 |
| `valid_setup_opportunities` | 5,884 | 5,884 | 5,884 | 5,884 |
| `eligible_opportunities` | 41 | 25 | 25 | 24 |
| `simulated_trades` | 41 | 25 | 25 | 24 |
| `win_rate` | 0.1463 | 0.2800 | 0.2800 | 0.2917 |
| `loss_rate` | 0.8537 | 0.7200 | 0.7200 | 0.7083 |
| `net_pnl` (USDT) | -69.6000 | -31.2265 | -31.1489 | -29.6353 |
| `gross_pnl` (USDT) | -7.0040 | -4.7743 | -4.1667 | -7.3693 |
| `fees_paid` (USDT) | 60.7370 | 25.6632 | 26.1772 | 21.6020 |
| `slippage_cost` (USDT) | 1.8590 | 0.7890 | 0.8050 | 0.6640 |
| `net_expectancy_R` | -0.7032 | -0.5179 | -0.5179 | -0.5085 |
| `profit_factor` | 0.1380 | 0.2483 | 0.2488 | 0.2394 |
| `max_drawdown` (USDT) | 69.6000 | 31.2265 | 31.1489 | 29.6353 |
| `average_stop_distance` | 2.3900 | 4.6816 | 4.5808 | 5.7237 |
| `average_quantity` | 0.4534 | 0.3156 | 0.3220 | 0.2767 |
| `early_stopout_60s` | 8 | 0 | 0 | 0 |
| `early_stopout_rate_per_executed_trade` | 0.1951 | 0.0000 | 0.0000 | 0.0000 |
| `early_stopout_rate_per_matched_trade` | 0.2083 | 0.0000 | 0.0000 | 0.0000 |
| `early_stopout_180s` | 16 | 0 | 0 | 0 |
| `ambiguous_exits` | 0 | 0 | 0 | 0 |

