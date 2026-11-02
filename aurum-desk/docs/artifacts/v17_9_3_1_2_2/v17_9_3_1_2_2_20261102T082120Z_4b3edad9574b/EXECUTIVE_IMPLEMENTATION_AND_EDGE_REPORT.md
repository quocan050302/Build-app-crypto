# AURUM DESK — V17.9.3.1.2.2 EXECUTIVE IMPLEMENTATION & EDGE REPORT

- **Run ID**: `v17_9_3_1_2_2_20261102T082120Z_4b3edad9574b`
- **Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA**: `4b3edad9574bf9186254e1459bc86cba52a2fbb6`
- **Canonical Dataset SHA-256**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **ENGINEERING_VERIFICATION_VERDICT**: `PASS` (`52/52` gates passed)
- **STRATEGY_STATISTICAL_VERDICT**: `NEGATIVE_EXPECTANCY`
- **ACTIVATION_READY**: `NO`

## 1. Signal Intelligence Portfolio Performance (`SI0..SI6`, `NEXT_BAR_OPEN_BID_ASK`)

| Policy ID | Closed Trades | Wins | Losses | Loss Rate | Expectancy (Net R) | Realized Net USD | Profit Factor | Max DD USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `SI0_OFF_BASELINE` | 166 | 9 | 157 | 94.58% | -0.8474R | $-345.90 | 0.1335 | $345.90 |
| `SI1_MODEL_QUALITY_AND_ARBITRATION` | 92 | 5 | 87 | 94.57% | -0.8266R | $-186.83 | 0.1256 | $186.83 |
| `SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION` | 111 | 5 | 106 | 95.50% | -0.8458R | $-231.47 | 0.1243 | $231.47 |
| `SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY` | 83 | 3 | 80 | 96.39% | -0.8875R | $-180.92 | 0.0800 | $180.92 |
| `SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN` | 0 | 0 | 0 | 0.00% | 0.0000R | $0.00 | 0.0000 | $0.00 |
| `SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET` | 45 | 4 | 41 | 91.11% | -0.7783R | $-85.87 | 0.1919 | $85.87 |
| `SI6_COMBINED_PREDECLARED` | 0 | 0 | 0 | 0.00% | 0.0000R | $0.00 | 0.0000 | $0.00 |

## 2. Verification Truth Closures
- **Full Backend Regression (`24`, `25`)**: Collected `4890` tests across `453` files; executed `4890` in JUnit XMLs with exact 1-to-1 bijection (`True`).
- **Executable RED/GREEN Source Mutations (`26`)**: `16/16` source mutations caught with real subprocess `exit != 0` on RED and `exit == 0` on GREEN.
- **1/2/4-Worker Independent Determinism (`28..31`)**: Identical SHA-256 (`58a71118c721be415ae59628fd974499b820dcb95aaf171fb8b57fcddcace885`) across 1, 2, and 4 OS processes across all 3 fill modes.
- **Computed Statistical Verdict (`34`)**: `All powered signal intelligence policies (['SI1_MODEL_QUALITY_AND_ARBITRATION', 'SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION', 'SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY', 'SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET']) remained negative net expectancy after realistic bid/ask spread, slippage, and taker fees (best=SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET: -0.7783R).`

