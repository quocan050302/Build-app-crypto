# AURUM DESK — V17.9.3.1.2.3 EXECUTIVE TRADING LOGIC REPAIR & EDGE REPORT

- **Run ID**: `v17_9_3_1_2_3_20261102T104332Z_ece0b3cf9bcb`
- **Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA**: `ece0b3cf9bcbd9011842d9e52b99bcce4ccb8274`
- **Canonical Dataset SHA-256**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **ENGINEERING_VERIFICATION_VERDICT**: `PASS` (`15/15` gates passed)
- **STRATEGY_STATISTICAL_VERDICT**: `NEGATIVE_EXPECTANCY`
- **ACTIVATION_READY**: `NO`

## 1. Ground-Truth Loss Forensics (`STAGE G0` — 166 Closed, 74 Open, 1 Data Gap)

- **Closed Trades**: `166` (`9` wins, `157` losses)
- **Open at Horizon**: `74`
- **Data Gap Unresolved**: `1`
- **Primary Loss Root Causes (`157` losses)**: `{"IMPULSE_CHASING": 24, "WRONG_SIDE_HTF": 133}`

## 2. Portfolio Replay Performance Across Policies (`SI0..SI8`, `NEXT_BAR_OPEN_BID_ASK`)

| Policy ID | Closed Trades | Wins | Losses | Win Rate | Loss Rate | Expectancy (Net R) | Realized Net USD | Profit Factor | Max DD USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `SI0_OFF_BASELINE` | 166 | 9 | 157 | 5.42% | 94.58% | -0.8474R | $-345.90 | 0.1335 | $345.90 |
| `SI1_MODEL_QUALITY_AND_ARBITRATION` | 92 | 5 | 87 | 5.43% | 94.57% | -0.8266R | $-186.83 | 0.1256 | $186.83 |
| `SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION` | 111 | 5 | 106 | 4.50% | 95.50% | -0.8458R | $-231.47 | 0.1243 | $231.47 |
| `SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY` | 83 | 3 | 80 | 3.61% | 96.39% | -0.8875R | $-180.92 | 0.0800 | $180.92 |
| `SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN` | 0 | 0 | 0 | 0.00% | 0.00% | 0.0000R | $0.00 | 0.0000 | $0.00 |
| `SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET` | 45 | 4 | 41 | 8.89% | 91.11% | -0.7783R | $-85.87 | 0.1919 | $85.87 |
| `SI6_COMBINED_PREDECLARED` | 0 | 0 | 0 | 0.00% | 0.00% | 0.0000R | $0.00 | 0.0000 | $0.00 |
| `SI7_CORE_TRADING_LOGIC_REPAIRED` | 2 | 0 | 2 | 0.00% | 100.00% | -0.9926R | $-4.89 | 0.0000 | $4.89 |
| `SI8_RESEARCH_DISCOVERY_REPAIRED` | 2 | 0 | 2 | 0.00% | 100.00% | -0.9926R | $-4.89 | 0.0000 | $4.89 |

## 3. Core Trading Logic Repairs (`STAGE G1..G6`) & Verification Truth
- **Candidate Discovery & Starvation Repair (`04`)**: `SI7_CORE_TRADING_LOGIC_REPAIRED` (`2` closed trades, `-0.9926R`) and `SI8_RESEARCH_DISCOVERY_REPAIRED` (`2` closed trades, `-0.9926R`) resolve the `SI4`/`SI6` zero-trade starvation and discover `35` causal setups across all 5 models without rescuing rejected baseline setups in `SI7`.
- **Targeted V17.9.3.1.2.3 Regression (`18`)**: `34/34` tests (`T01..T34`) passed.
- **Full Backend Pytest Bijection (`19`, `20`)**: Collected `4924` tests across `454` files; executed `4924` in JUnit XMLs with exact 1-to-1 bijection (`True`).
- **Executable RED/GREEN Source Mutations (`21`)**: `16/16` caught with non-zero exit on RED and zero exit on GREEN.
- **1/2/4-Worker Determinism (`22`)**: Identical SHA-256 (`58a71118c721be415ae59628fd974499b820dcb95aaf171fb8b57fcddcace885`) across 1, 2, and 4 OS processes.
- **Statistical Verdict Honesty (`25`)**: `All powered signal intelligence policies (['SI1_MODEL_QUALITY_AND_ARBITRATION', 'SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION', 'SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY', 'SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET']) remained negative net expectancy after realistic bid/ask spread, slippage, and taker fees (best=SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET: -0.7783R).`

