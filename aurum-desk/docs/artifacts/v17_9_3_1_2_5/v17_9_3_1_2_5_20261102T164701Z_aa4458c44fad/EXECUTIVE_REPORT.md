# AURUM DESK — V17.9.3.1.2.5 FULL-UNIVERSE SIGNAL DISCOVERY & EVIDENCE INTEGRITY REPAIR

- **Run ID**: `v17_9_3_1_2_5_20261102T164701Z_aa4458c44fad`
- **Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA (Phase A)**: `aa4458c44fadfd972d354cd84130cc41217f475d`
- **Canonical Dataset SHA-256**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **ENGINEERING_VERIFICATION_VERDICT**: `PASS` (`19/19` gates passed)
- **STRATEGY_STATISTICAL_VERDICT**: `NEGATIVE_EXPECTANCY`
- **ACTIVATION_READY**: `NO`

## 1. P0-A & P0-B — Full-Universe Discovery & Measured Evidence Integrity

- **Official Full-Universe Status**: `is_official_full_universe = True` (`sampled_checkpoint_count = 0`, `causal_checkpoint_count = 22346`)
- **Canonical SI8 Contract**: `v17_9_3_1_2_2_signal_intelligence_mode = 'RESEARCH_APPLY'` + `v17_9_3_1_2_2_policy_id = 'SI8_RESEARCH_DISCOVERY_REPAIRED'`
- **Non-Rescue Proof (P1-C)**: `si7_rejected_baseline_rescued_count = 0`, `si8_rejected_baseline_rescued_count = 0`, `si7_upgraded_invalid = 0`
- **SI8 Newly Discovered Setups**: `103647` (`19144` incremental eligible vs `SI7` across all 5 models: `{"CG01_SWEEP_MSS_FVG": 81, "SMC_M1_CONTINUATION": 1009, "SMC_M1_FVG_CONTINUATION": 603, "SMC_M1_REVERSAL": 8403, "SMC_M1_SWEEP_RECLAIM": 9048}`)
- **Feature Ledger Proof (P0-B)**: 0 hardcoded booleans; all features derived strictly from measured evidence dictionaries with explicit PROVEN_TRUE / UNPROVEN status.

## 2. Portfolio Replay Performance Across Policies (`SI0..SI8`, `NEXT_BAR_OPEN_BID_ASK`)

| Policy ID | Closed Trades | Wins | Losses | Open at Horizon | Win Rate | Loss Rate | Expectancy (Net R) | Realized Net USD | Profit Factor | Max DD USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `SI0_OFF_BASELINE` | 166 | 9 | 157 | 74 | 5.42% | 94.58% | -0.8474R | `$-345.90` | 0.1335 | `$345.90` |
| `SI1_MODEL_QUALITY_AND_ARBITRATION` | 92 | 5 | 87 | 44 | 5.43% | 94.57% | -0.8266R | `$-186.83` | 0.1256 | `$186.83` |
| `SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION` | 111 | 5 | 106 | 46 | 4.50% | 95.50% | -0.8458R | `$-231.47` | 0.1243 | `$231.47` |
| `SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY` | 83 | 3 | 80 | 51 | 3.61% | 96.39% | -0.8875R | `$-180.92` | 0.0800 | `$180.92` |
| `SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN` | 0 | 0 | 0 | 0 | 0.00% | 0.00% | 0.0000R | `$0.00` | 0.0000 | `$0.00` |
| `SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET` | 45 | 4 | 41 | 23 | 8.89% | 91.11% | -0.7783R | `$-85.87` | 0.1919 | `$85.87` |
| `SI6_COMBINED_PREDECLARED` | 0 | 0 | 0 | 0 | 0.00% | 0.00% | 0.0000R | `$0.00` | 0.0000 | `$0.00` |
| `SI7_CORE_TRADING_LOGIC_REPAIRED` | 2 | 0 | 2 | 0 | 0.00% | 100.00% | -0.9926R | `$-4.89` | 0.0000 | `$4.89` |
| `SI8_RESEARCH_DISCOVERY_REPAIRED` | 177 | 10 | 167 | 56 | 5.65% | 94.35% | -0.8120R | `$-354.06` | 0.1424 | `$354.06` |

## 3. Fresh Full Pytest Regression & Bijection Proof (Zero Reused Shards)

- **Discovered Test Files**: `456`
- **Collected Node IDs**: `4935`
- **Executed JUnit Node IDs**: `4935` (`passed=4935`, `failed=0`, `error=0`, `skipped=0`)
- **Freshly Executed Shards**: `25/25` (`reused_shards_count = 0`)
- **Stale JUnit Negative Controls**: `True`
- **1/2/4-Worker Determinism (`SI0, SI5, SI7, SI8`)**: `True` (`0b033b8adff342710cad49d28d06fa8e6db00aecac3a97a0cc7b8d60af2cf765`)

## 4. Dual Verdict & Risk Disclosure

- **ENGINEERING_VERIFICATION_VERDICT**: `PASS`
- **STRATEGY_STATISTICAL_VERDICT**: `NEGATIVE_EXPECTANCY` (Best Research Policy: `SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET` with `-0.7783R` and `$-85.8702` net realized PnL)
- **ACTIVATION_READY**: `NO` (No activation on live/paper accounts allowed)

