# AURUM DESK V17.8.0.4 — THREE-LAYER EXPERIMENT & MATCHED COHORT STOP-OUT ANALYSIS

## 1. Executive Summary
- **Final Code SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Backtest Run ID:** `v17_8_0_4_bt_1793248253`
- **Verification Gate:** `G82_MATCHED_COHORT_STOP_OUT_ANALYSIS` (`PASS`)

## 2. Experiment 1 — Matched SL-Only Cohort (`N = 17` Identical Opportunities)
Eliminates trade-count denominator distortion by comparing Variants A, B, C, and D strictly on the `17` opportunities where all compared variants are independently eligible (`net_rr >= 1.2`):

| Variant | Matched Trades | Win Rate | Net PnL (USDT) | Net Expectancy (R) | Early Stop-Out 60s Rate | Early Stop-Out 180s Rate | Saved From SL by C |
|---|---:|---:|---:|---:|---:|---:|---:|
| **A (Legacy Fixed)** | `17` | `0.1176` | `-34.0155` | `-0.8266` | `0.3529` | `0.5294` | — |
| **B (Structural Floor)** | `17` | `0.2941` | `-21.4507` | `-0.5235` | `0.0` | `0.0` | — |
| **C (Unicorn + Dyn Buffer)** | `17` | `0.2941` | `-21.3731` | `-0.5235` | `0.0` | `0.0` | `3` |
| **D (Empirical P90 Noise)** | `17` | `0.2941` | `-21.8244` | `-0.5322` | `0.0` | `0.0` | — |

## 3. Eligibility Transition Matrix Across All `3,839` Valid Opportunities
- `ELIGIBLE_IN_ALL_ABCD`: `17`
- `ELIGIBLE_IN_A_REJECTED_IN_B`: `13`
- `ELIGIBLE_IN_A_REJECTED_IN_C`: `13`
- `ELIGIBLE_IN_A_REJECTED_IN_D`: `13`
- `REJECTED_IN_ALL_ABCD`: `3809`
