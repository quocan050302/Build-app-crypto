# AURUM DESK — V17.9.3.1.2.4 SI8 RESEARCH DISCOVERY REPAIR, FRESH REGRESSION TRUTH & REAL STRATEGY VALIDATION

- **Run ID**: `v17_9_3_1_2_4_20261102T150439Z_13df4606efeb`
- **Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA (Phase A)**: `13df4606efeb20bcdbe864845746da82cca7bfda`
- **Canonical Dataset SHA-256**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **ENGINEERING_VERIFICATION_VERDICT**: `PASS` (`17/17` gates passed)
- **STRATEGY_STATISTICAL_VERDICT**: `NEGATIVE_EXPECTANCY`
- **ACTIVATION_READY**: `NO`

## 1. Stage R1 & R2 — SI8 Mode/Consumer Repair, Candidate Discovery Funnel & SI7 vs SI8 Comparison

- **Canonical SI8 Contract**: `v17_9_3_1_2_2_signal_intelligence_mode = 'RESEARCH_APPLY'` + `v17_9_3_1_2_2_policy_id = 'SI8_RESEARCH_DISCOVERY_REPAIRED'`
- **Invalid Mode Fail-Closed Proof**: Passing `'RESEARCH_DISCOVERY'` returns `['SI_INVALID_MODE_FAIL_CLOSED']` (`fail_closed_verified = True`)
- **Non-Rescue Proof**: `si7_rejected_baseline_rescued_count = 0`, `si8_rejected_baseline_rescued_count = 0`
- **SI8 Newly Discovered Setups**: `100` (`30` incremental eligible vs `SI7` across all 5 models: `{"CG01_SWEEP_MSS_FVG": 1, "SMC_M1_CONTINUATION": 3, "SMC_M1_FVG_CONTINUATION": 2, "SMC_M1_REVERSAL": 11, "SMC_M1_SWEEP_RECLAIM": 13}`)
- **SI7 vs SI8 Portfolio Replay Non-Identity**: `SI7` (`2` closed trades, `-0.9926R`, `$-4.89`, ledger SHA `7a3461bd98966759...`) vs `SI8` (`10` closed trades, `-0.9983R`, `$-24.71`, ledger SHA `ce74ad234ed2615d...`)

## 2. Portfolio Replay Performance Across Policies (`SI0..SI8`, `NEXT_BAR_OPEN_BID_ASK`)

| Policy ID | Closed Trades | Wins | Losses | Open at Horizon | Win Rate | Loss Rate | Expectancy (Net R) | Realized Net USD | Profit Factor | Max DD USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `SI0_OFF_BASELINE` | 166 | 9 | 157 | 74 | 5.42% | 94.58% | -0.8474R | $-345.90 | 0.1335 | $345.90 |
| `SI1_MODEL_QUALITY_AND_ARBITRATION` | 92 | 5 | 87 | 44 | 5.43% | 94.57% | -0.8266R | $-186.83 | 0.1256 | $186.83 |
| `SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION` | 111 | 5 | 106 | 46 | 4.50% | 95.50% | -0.8458R | $-231.47 | 0.1243 | $231.47 |
| `SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY` | 83 | 3 | 80 | 51 | 3.61% | 96.39% | -0.8875R | $-180.92 | 0.0800 | $180.92 |
| `SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN` | 0 | 0 | 0 | 0 | 0.00% | 0.00% | 0.0000R | $0.00 | 0.0000 | $0.00 |
| `SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET` | 45 | 4 | 41 | 23 | 8.89% | 91.11% | -0.7783R | $-85.87 | 0.1919 | $85.87 |
| `SI6_COMBINED_PREDECLARED` | 0 | 0 | 0 | 0 | 0.00% | 0.00% | 0.0000R | $0.00 | 0.0000 | $0.00 |
| `SI7_CORE_TRADING_LOGIC_REPAIRED` | 2 | 0 | 2 | 0 | 0.00% | 100.00% | -0.9926R | $-4.89 | 0.0000 | $4.89 |
| `SI8_RESEARCH_DISCOVERY_REPAIRED` | 10 | 0 | 10 | 1 | 0.00% | 100.00% | -0.9983R | $-24.71 | 0.0000 | $24.71 |

## 3. Stage R3 — Fresh Full Pytest Regression & Bijection Proof (Zero Reused Shards)

- **Discovered Test Files**: `455`
- **Collected Node IDs**: `4930`
- **Executed JUnit Node IDs**: `4930` (`passed=4930`, `failed=0`, `error=0`, `skipped=0`)
- **Freshly Executed Shards**: `24/24` (`reused_shards_count = 0`)
- **Stale JUnit Negative Controls**: `True`
- **1/2/4-Worker Determinism (`SI0, SI5, SI7, SI8`)**: `True` (`07a622c580ee31e766d1515f825ab97d35c1c29ea7e5f5f4be9a8c97c7db2204`)

