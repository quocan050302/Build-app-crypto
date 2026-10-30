# V17.9.0.1.2 — Historical Backtest & Authoritative Shared Evaluator Parity Report

- **Final Code SHA (`FINAL_CODE_SHA`):** `2202a72a3a0cadc18bbbf5559bb94cb0ed3aafcd`
- **Backtest Executed Source SHA:** `2202a72a3a0cadc18bbbf5559bb94cb0ed3aafcd`
- **Evaluator Version:** `17.9.0.1.1`
- **Dataset SHA-256:** `6bcac947a842db06b4edb11e3db8164421dfcd27a3c0b9d6e571bf07543c3fb5`
- **Saved Repository Snapshot:** `backtest/historical_dataset_XAUUSDT.json.gz` (`9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`)
- **Verified Historical Bars:** `M1=22376`, `M5=31609`, `M15=15090`, `H1=3863`, `H4=965`, `1D=181`
- **Fabricated Candles:** `0`
- **Synthetic Modulo/Index Signals:** `0`
- **Canonical SharedStrategyEvaluator Active in Backtest:** `True` (`HISTORICAL_BACKTEST_ADAPTER`)
- **Trading Authorization:** `MARKET_SHADOW_RESEARCH_ONLY` (`PAPER_AUTO_TRADING_NOT_AUTHORIZED`, `LIVE_TRADING_NOT_AUTHORIZED`)

## 1. Authoritative Production-Parity Simulation (`SharedStrategyEvaluator.evaluate_decision`)

This simulation executes the exact canonical `DecisionResult` produced by `SharedStrategyEvaluator.evaluate_decision(...)` (Variant `C` structural Unicorn stop with strict causal binding, authentic ATR M1/M5 provenance, and fail-closed rejection on non-executable decisions).

| Simulation Scope | Simulated Trades | Win Rate | Net Expectancy (R) | Gross PnL (USD) | Fees Paid (USD) | Slippage Cost (USD) | Net PnL (USD) | Max Drawdown (USD) | Early Stop-Out (<=60s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Canonical Production-Parity (`SharedStrategyEvaluator`)** | 18 | 38.89% | -0.3796 | $3.7084 | $19.461 | $0.598 | $-16.3506 | $16.3506 | 0 |

## 2. SL Variant Comparison Research (`A` / `B` / `C` / `D` — Experiment Layer 3)

| Variant | Simulated Trades | Win Rate | Net Expectancy (R) | Gross PnL (USD) | Fees Paid (USD) | Slippage Cost (USD) | Net PnL (USD) | Max Drawdown (USD) | Early Stop-Out (<=60s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Variant A** | 41 | 14.63% | -0.7032 | $-7.004 | $60.737 | $1.859 | $-69.6 | $69.6 | 8 |
| **Variant B** | 25 | 28.00% | -0.5179 | $-4.7743 | $25.6632 | $0.789 | $-31.2265 | $31.2265 | 0 |
| **Variant C** | 18 | 38.89% | -0.3796 | $3.7084 | $19.461 | $0.598 | $-16.3506 | $16.3506 | 0 |
| **Variant D** | 24 | 29.17% | -0.5114 | $-7.688 | $21.3778 | $0.657 | $-29.7228 | $29.7228 | 0 |

## 3. Experiment Layer 1 — Matched SL-Only Cohort (`A` vs `C` and `A/B/C/D`)

- **Matched All A/B/C/D Cohort Opportunities:** `18`
- **Matched A vs C Cohort Opportunities:** `18`
- **Opportunities Saved From SL by Variant C vs Variant A:** `3`
- **Mean Paired Delta Net R (C minus A):** `0.2836`
- **Total Paired Delta Net PnL (C minus A):** `$12.5401`

## 4. Per-Model Breakdown (`Experiment 3E`)

| Model ID | Valid Setups | A Trades | B Trades | C Trades | D Trades | C Net PnL (USD) | C Expectancy (R) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `SMC_M1_FVG_CONTINUATION` | 3161 | 26 | 14 | 10 | 13 | $-4.439 | -0.1899 |
| `SMC_M1_CONTINUATION` | 1167 | 9 | 7 | 5 | 7 | $-8.0231 | -0.6628 |
| `SMC_M1_REVERSAL` | 153 | 0 | 0 | 0 | 0 | $0 | None |
| `SMC_M1_SWEEP_RECLAIM` | 931 | 2 | 1 | 1 | 1 | $0.9418 | 0.3798 |
| `CG01_SWEEP_MSS_FVG` | 364 | 4 | 3 | 2 | 3 | $-4.8303 | -1.0 |

## 5. Chronological 60/20/20 Out-of-Sample Evaluation

- **Out-of-Sample Verdict:** `OUT_OF_SAMPLE_INCONCLUSIVE`
- **Final 20% OOS Opportunity Count:** `1155`
- **Variant A OOS Trades / Net PnL / Expectancy (R):** `1` / `$-0.5278` / `-0.2146`
- **Variant C OOS Trades / Net PnL / Expectancy (R):** `0` / `$0` / `None`
