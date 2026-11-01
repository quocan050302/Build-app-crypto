# AURUM Desk V17.9.3.1.2 — Full-Universe Replay & Executable Entry/SL/TP Counterfactual Validation Report

## 1. Provenance & Git Identity
- **Mandatory Working Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Starting V17.9.3.1.2 Feature HEAD SHA**: `f455afc0950e13cee03dac2c912363379719aafb`
- **Previous V17.9.3.1.1 Tested Executable SHA**: `e43c785a5bb916bc00b31b378be1130ab3308bc9`
- **Previous V17.9.3.1 Tested Executable SHA**: `a00a17091bf7991855aaeb3f718242354c244a08`
- **Base `master` SHA**: `650e4535777421077443cd3d95507cad1d99ed42`
- **Tested Executable Code SHA**: `f0339ecae3152c2358eed44ff1707fb4d34e429d`
- **Final Local HEAD SHA**: `59f86d438e746a32f6ba76d21b7ba998a059a471`
- **Remote Origin HEAD SHA**: `59f86d438e746a32f6ba76d21b7ba998a059a471`
- **Evidence Run ID**: `v17_9_3_1_2_20261101T172127Z_f0339ecae315`

## 2. Three-Axis Closure Verdict
- **`ENGINEERING_VERIFICATION_VERDICT`**: **`PASS`**
- **`STRATEGY_STATISTICAL_VERDICT`**: **`NEGATIVE_EXPECTANCY`**
- **`ACTIVATION_READY` (`LIVE` / `PAPER` / `AUTO`)**: **`NO`**

## 3. P0-A — Full-Universe `22,346`-Timestamp Reconciliation & Chronological Portfolio Replay
- **Total Post-Warmup M1 Bars Evaluated**: `22,346 / 22,346` (`idx = 30..22375`, `111,730` model evaluations per variant, `0` `$7.00` M1 target-distance truncation).
- **Outside-Screen Reconciliation**:
  - Outside-screen unique timestamps with `A1` or `E1` eligible setups: `15597`
  - `A1_V1792_SL_TP_BASELINE`: `47243` outside-screen + `9467` inside-screen = `56710` total eligible model decisions.
  - `E1_OUTSIDE_MACRO_COUNTERFACTUAL`: `34406` outside-screen + `4214` inside-screen = `38620` total eligible model decisions (`12837` fewer due to `WEEKEND_OR_CLOSED` gate in `E1`).

### Side-by-Side Scope Attribution (`LEGACY_SCREENED` vs `FULL_UNIVERSE_ALL_TIMESTAMPS`, Primary Fill Mode `NEXT_BAR_OPEN_BID_ASK`)
| Variant | Scope | Raw Eligible Decisions | Closed Episodes | Wins | Losses | Loss Rate | Expectancy (R) | Modeled Net PnL (USD) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `A1_V1792_SL_TP_BASELINE` | `LEGACY_SCREENED` | 9467 | 142 | 7 | 135 | 95.07% | -0.8654 | $-302.93 |
| `A1_V1792_SL_TP_BASELINE` | `FULL_UNIVERSE_ALL_TIMESTAMPS` | 56710 | 166 | 9 | 157 | 94.58% | -0.8474 | $-345.90 |
| `E1_OUTSIDE_MACRO_COUNTERFACTUAL` | `LEGACY_SCREENED` | 4214 | 100 | 6 | 94 | 94.00% | -0.8299 | $-204.27 |
| `E1_OUTSIDE_MACRO_COUNTERFACTUAL` | `FULL_UNIVERSE_ALL_TIMESTAMPS` | 38620 | 132 | 8 | 124 | 93.94% | -0.8202 | $-265.99 |
| `C1_MACRO_PLUS_V1792_SL_TP` (`B1`) | `FULL_UNIVERSE_ALL_TIMESTAMPS` | 4442 | 42 | 2 | 40 | 95.24% | -0.9053 | $-93.33 |
| `D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP` | `FULL_UNIVERSE_ALL_TIMESTAMPS` | 263 | 16 | 1 | 15 | 93.75% | -0.8044 | $-31.69 |
| `D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL` | `FULL_UNIVERSE_ALL_TIMESTAMPS` | 50 | 2 | 1 | 1 | 50.00% | 0.5617 | $2.74 |

## 4. P0-B — Executable Entry/SL/TP Counterfactual Interventions (`R0`–`R8` on Full-Universe `A1` Cohort)
| Policy ID | Modified Geometry Count | Rejected Count | Closed Portfolio Episodes | Wins | Losses | Loss Rate | Expectancy (R) | Modeled Net PnL (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `R0_BASELINE_CONTROL` | 0 | 0 | 166 | 9 | 157 | 94.58% | -0.8474 | $-345.90 |
| `R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE` | 482 | 1558 | 45 | 2 | 43 | 95.56% | -0.8585 | $-94.70 |
| `R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE` | 224 | 1289 | 77 | 4 | 73 | 94.81% | -0.8595 | $-162.32 |
| `R4_OUTER_STRUCTURAL_STOP_EXECUTABLE` | 1935 | 177 | 141 | 8 | 133 | 94.33% | -0.8434 | $-292.05 |
| `R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE` | 1223 | 524 | 157 | 8 | 149 | 94.90% | -0.8728 | $-337.22 |
| `R7_COMBINED_ENTRY_SL_TP_EXECUTABLE` | 66 | 2046 | 5 | 0 | 5 | 100.00% | -1.0000 | $-12.18 |
| `R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE` | 541 | 1571 | 34 | 1 | 33 | 97.06% | -0.9639 | $-80.10 |

## 5. Pytest Bijection & Chronological OOS Summary
- **Collected Test Node IDs**: `4746`
- **Executed & Passed Tests**: `4746` (`0` failed, `0` skipped, `bijection_valid=True`)
- **OOS Holdout Freshness Status**: `OOS_REUSED_NOT_FRESH_HISTORICAL_WINDOW_DISCLOSED` (`admissible_embargo_leak_count = 0`)
