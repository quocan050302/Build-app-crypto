# AURUM DESK V17.9.0 — Per-Model Performance and Cohort Analysis

- **Release Version**: `V17.9.0`
- **Branch**: `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Tested Executable Source SHA (`FINAL_CODE_SHA`)**: `76f725a232ab3bda5459b40efad95a08dfca7fd1`
- **Backtest Run ID**: `v17_8_0_4_bt_1793257304`
- **Artifacts**: `backtest/model_results.json`, `backtest/matched_sl_comparison.json`, `backtest/full_strategy_comparison.json`

---

## 1. Per-Model Breakdown Across All 5 Production Models (`backtest/model_results.json`)

| Model ID | Valid Setups | Early Rejections | Variant A Trades | Variant B Trades | Variant C Trades | Variant D Trades | Variant A Net PnL | Variant C Net PnL | Variant A Expectancy (R) | Variant C Expectancy (R) | Performance Status (C) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `SMC_M1_FVG_CONTINUATION` | 3,218 | 699 | 26 | 14 | 14 | 13 | -42.9720 | -14.1447 | -0.6856 | -0.4213 | `SUFFICIENT_SAMPLE` |
| `SMC_M1_CONTINUATION` | 1,218 | 562 | 9 | 7 | 7 | 7 | -15.4367 | -10.7176 | -0.7115 | -0.6328 | `LOW_SAMPLE_CAUTION` |
| `SMC_M1_REVERSAL` | 153 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | `None` | `None` | `INSUFFICIENT_SAMPLE` |
| `SMC_M1_SWEEP_RECLAIM` | 931 | 22 | 2 | 1 | 1 | 1 | -1.4692 | +0.9418 | -0.3005 | +0.3798 | `LOW_SAMPLE_CAUTION` |
| `CG01_SWEEP_MSS_FVG` | 364 | 77 | 4 | 3 | 3 | 3 | -9.7221 | -7.2284 | -1.0000 | -1.0000 | `LOW_SAMPLE_CAUTION` |
| **Total** | **5,884** | **1,360 (+12 gap)** | **41** | **25** | **25** | **24** | **-69.6000** | **-31.1489** | **-0.7032** | **-0.5179** | — |

---

## 2. Experiment Layer 1 — Matched SL-Only Cohort Analysis (`G105`)

To prevent denominator distortion between Variant A (`41` trades) and Variant C (`25` trades), Experiment Layer 1 evaluates variants strictly on the identical subset of opportunities where compared variants independently pass all eligibility gates:
- **Matched All A/B/C/D Cohort Count**: `24` opportunities
- **Matched A vs C Cohort Count**: `25` opportunities
  - **Opportunities Saved From SL by Variant C vs Variant A**: `3` (`12.0%` of matched cohort)
  - **Early Stop-Outs (`<= 60s`) on Matched A vs C Cohort**: Variant A = `5` (`20.0%`), Variant C = `0` (`0.0%`)
  - **Mean Paired Delta Net R (`C minus A`)**: `+0.2043 R` per matched trade
  - **Total Paired Delta Net PnL (`C minus A`)**: `+$12.5331` USDT

---

## 3. Experiment Layer 2 — Entry & Eligibility Filtering Attribution

- **Total Valid Setup Opportunities**: `5,884`
- **Eligible in Variant A**: `41` (`0.6968%`)
- **Eligible in Variant B**: `25` (`0.4249%`)
- **Eligible in Variant C**: `25` (`0.4249%`)
- **Eligible in Variant D**: `24` (`0.4079%`)
- **Eligibility Transition Matrix**:
  - `ELIGIBLE_IN_ALL_ABCD`: `24`
  - `ELIGIBLE_IN_A_REJECTED_IN_B`: `16`
  - `ELIGIBLE_IN_A_REJECTED_IN_C`: `16`
  - `ELIGIBLE_IN_A_REJECTED_IN_D`: `17`
  - `REJECTED_IN_ALL_ABCD`: `5,843`
- **Attribution Conclusion**: Of the `+$38.4511` total Net PnL improvement between Variant A (`-$69.6000`) and Variant C (`-$31.1489`), `+$12.5331` (`32.6%`) comes from pure SL survival and reduced fee drag on the `25` matched trades, while `+$25.9180` (`67.4%`) comes from rejecting `16` low-R:R trades where structural stop distance caused `net_rr < 1.50`.

