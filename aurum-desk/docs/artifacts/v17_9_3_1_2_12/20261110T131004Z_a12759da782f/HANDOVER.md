# AURUM DESK — V17.9.3.1.2.11 P0 TRUTH CLOSURE & EXIT-EVENT LINEAGE HARDENING

## 1. Run Identity & Frozen Provenance
- **Version**: `v17_9_3_1_2_11`
- **Run ID**: `20261110T131004Z_a12759da782f`
- **Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA (Phase A)**: `a12759da782fbf2399657c727f57138d23280b3b`
- **Canonical Dataset SHA256**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **Quote Provenance**: `SYNTHETIC_RESEARCH_QUOTE` (`CLOSE_PLUS_MINUS_0.10_USD`, `0.20 USD` spread)
- **Evidence Origin**: `RECONSTRUCTED_FROM_EXECUTION_LEDGER` & `RECONSTRUCTED_FROM_BAR` (`0` `UNKNOWN` events)
- **Raw Exit-Event Lineage Verdict**: `RECONSTRUCTED_LINEAGE_VERIFIED`
- **Strategy Expectancy Verdict**: `NEGATIVE_EXPECTANCY`
- **Fresh OOS Verdict**: `REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS`
- **Activation Ready**: `NO` (`PAPER=OFF`, `LIVE=OFF`, `AUTO=OFF`)

## 2. P0-A & P0-B: Independent Verification Truth & 30-Partition Raw Stream Reconstruction
- **Fresh Pytest Collection & Shards**: `5249` collected, `5249` executed, `5241` passed, `0` failed, `0` errors, `8` platform skips (`0` mandatory skips) across `54` shards.
- **All 30 Policy x Fill-Mode Partitions Reconstructed from Raw Event Stream**: `30` partitions (`10` policies x `3` fill modes), `7837` raw events (`7,837`), `836` episodes (`5,154`), `566` closed trades (`566`).
- **Worker 1/2/4 + Repeat Process & Order Parity**:
  - `1-worker Ordered SHA256`: `5b2ec10d64cb5e3fab6b045fc27761aeff6d9adfa54dd69cb5d931635db07f5c`
  - `2-worker Ordered SHA256`: `5b2ec10d64cb5e3fab6b045fc27761aeff6d9adfa54dd69cb5d931635db07f5c`
  - `4-worker Ordered SHA256`: `5b2ec10d64cb5e3fab6b045fc27761aeff6d9adfa54dd69cb5d931635db07f5c` (`4` distinct non-empty OS worker PIDs)
  - `1-worker Repeat Ordered SHA256`: `5b2ec10d64cb5e3fab6b045fc27761aeff6d9adfa54dd69cb5d931635db07f5c`
  - `Membership SHA256`: `b65795668fb8422c12a7e131610245406e1c2db6ca2552fb77ade55b8bd20167`
- **Adversarial Negative Controls**: `40` (`V01`..`V22`) executed on disposable copies and all `22` rejected (`PASS`).

## 3. Canonical Universe & SI0_OFF_BASELINE Exact Accounting
- **Raw M1 Bars**: `22,376` (`30` warmup excluded -> `22,346` post-warmup checkpoints -> `223,460` model-direction evaluations).
- **Canonical Universe**: `56,710` baseline + `6,375` detector-verified `SI8` incremental = `63,085` authoritative opportunities (vs `63,117` pre-detector-filter in `.9` and `75,525` in `.8`).
- **`SI8` / `SI9` Materialization Funnel**: `278` proof-eligible -> `6` suppressed by same-timestamp arbitration + `272` rejected post-arbitration (`110 + 57 + 42 + 41 + 22 = 272`) -> `0` closed trades.
- **`SI0_OFF_BASELINE` (`NEXT_BAR_OPEN_BID_ASK`)**: `242` scheduled = `166` closed (`9` `WIN` + `157` `LOSS`) + `74` `OPEN_AT_HORIZON` + `1` `DATA_GAP_UNRESOLVED` + `1` `UNFILLED`; Gross PnL = `-142.7836 USD`, Entry Fees = `101.5524 USD`, Exit Fees = `101.5611 USD`, Total Fees = `203.1135 USD`, Net PnL = `-345.8971 USD`, Expectancy Net R = `-0.8474`.
- **`SI0_OFF_BASELINE` (`LEGACY_CLOSE_FILL`)**: `164` closed (`10` `WIN` + `154` `LOSS`), Gross PnL = `-110.2950 USD`, Entry Fees = `101.6799 USD`, Exit Fees = `101.6792 USD`, Total Fees = `203.3591 USD`, Net PnL = `-313.6541 USD`.
- **`SI0_OFF_BASELINE` (`STRESS_WIDER_SPREAD_SLIPPAGE_2X`)**: `173` closed (`9` `WIN` + `164` `LOSS`), Gross PnL = `-168.9096 USD`, Entry Fees = `106.4227 USD`, Exit Fees = `106.4338 USD`, Total Fees = `212.8565 USD`, Net PnL = `-381.7661 USD`.

## 4. Recovered 15 Unique Closed Trades (`SI1_H1_H2`, `SI2_H3`, `SI3_H4`) & Venn Partition
- **Policy Trade Occurrences**: `21` (`SI1_H1_H2 = 6`, `SI2_H3 = 9`, `SI3_H4 = 6`, `6 + 9 + 6 = 21`).
- **Unique Closed Trades**: `15` (`0` `WIN`, `15` `LOSS`).
- **Exact 7-Cell Venn Overlap Partition**:
  - `SI1_H1_H2+SI2_H3+SI3_H4`: `0`
  - `SI1_H1_H2+SI2_H3_only`: `0`
  - `SI1_H1_H2+SI3_H4_only`: `6`
  - `SI1_H1_H2_only`: `0`
  - `SI2_H3+SI3_H4_only`: `0`
  - `SI2_H3_only`: `9`
  - `SI3_H4_only`: `0`

## 5. Complete 30-Partition Policy x Fill-Mode Reconstructed Table
| Policy | Fill Mode | Scheduled | Closed | Wins | Losses | Open Horizon | Data Gap | Unfilled | Gross PnL (USD) | Entry Fees (USD) | Exit Fees (USD) | Total Fees (USD) | Net PnL (USD) | Expectancy Net R |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `SI0_OFF_BASELINE` | `LEGACY_CLOSE_FILL` | 238 | 164 | 10 | 154 | 73 | 0 | 1 | -110.2950 | 101.6799 | 101.6792 | 203.3591 | -313.6541 | -0.7770 |
| `SI0_OFF_BASELINE` | `NEXT_BAR_OPEN_BID_ASK` | 242 | 166 | 9 | 157 | 74 | 1 | 1 | -142.7836 | 101.5524 | 101.5611 | 203.1135 | -345.8971 | -0.8474 |
| `SI0_OFF_BASELINE` | `STRESS_WIDER_SPREAD_SLIPPAGE_2X` | 248 | 173 | 9 | 164 | 73 | 1 | 1 | -168.9096 | 106.4227 | 106.4338 | 212.8565 | -381.7661 | -0.8968 |
| `SI1_MODEL_QUALITY_AND_ARBITRATION` | `LEGACY_CLOSE_FILL` | 10 | 6 | 0 | 6 | 4 | 0 | 0 | -8.4341 | 3.1966 | 3.2000 | 6.3966 | -14.8307 | -1.0001 |
| `SI1_MODEL_QUALITY_AND_ARBITRATION` | `NEXT_BAR_OPEN_BID_ASK` | 10 | 6 | 0 | 6 | 4 | 0 | 0 | -8.2029 | 3.1968 | 3.2000 | 6.3968 | -14.5997 | -0.9845 |
| `SI1_MODEL_QUALITY_AND_ARBITRATION` | `STRESS_WIDER_SPREAD_SLIPPAGE_2X` | 10 | 6 | 0 | 6 | 4 | 0 | 0 | -8.7879 | 3.1965 | 3.2001 | 6.3966 | -15.1845 | -1.0239 |
| `SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION` | `LEGACY_CLOSE_FILL` | 16 | 9 | 0 | 9 | 7 | 0 | 0 | -9.7839 | 6.2061 | 6.2101 | 12.4162 | -22.2001 | -1.0000 |
| `SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION` | `NEXT_BAR_OPEN_BID_ASK` | 16 | 9 | 0 | 9 | 7 | 0 | 0 | -9.7839 | 6.2061 | 6.2101 | 12.4162 | -22.2001 | -1.0000 |
| `SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION` | `STRESS_WIDER_SPREAD_SLIPPAGE_2X` | 16 | 9 | 0 | 9 | 7 | 0 | 0 | -10.9359 | 6.2058 | 6.2102 | 12.4160 | -23.3519 | -1.0518 |
| `SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY` | `LEGACY_CLOSE_FILL` | 10 | 6 | 0 | 6 | 4 | 0 | 0 | -8.4341 | 3.1966 | 3.2000 | 6.3966 | -14.8307 | -1.0001 |
| `SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY` | `NEXT_BAR_OPEN_BID_ASK` | 10 | 6 | 0 | 6 | 4 | 0 | 0 | -8.2029 | 3.1968 | 3.2000 | 6.3968 | -14.5997 | -0.9845 |
| `SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY` | `STRESS_WIDER_SPREAD_SLIPPAGE_2X` | 10 | 6 | 0 | 6 | 4 | 0 | 0 | -8.7879 | 3.1965 | 3.2001 | 6.3966 | -15.1845 | -1.0239 |
| `SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN` | `LEGACY_CLOSE_FILL` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN` | `NEXT_BAR_OPEN_BID_ASK` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN` | `STRESS_WIDER_SPREAD_SLIPPAGE_2X` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET` | `LEGACY_CLOSE_FILL` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET` | `NEXT_BAR_OPEN_BID_ASK` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET` | `STRESS_WIDER_SPREAD_SLIPPAGE_2X` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI6_COMBINED_PREDECLARED` | `LEGACY_CLOSE_FILL` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI6_COMBINED_PREDECLARED` | `NEXT_BAR_OPEN_BID_ASK` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI6_COMBINED_PREDECLARED` | `STRESS_WIDER_SPREAD_SLIPPAGE_2X` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI7_CORE_TRADING_LOGIC_REPAIRED` | `LEGACY_CLOSE_FILL` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI7_CORE_TRADING_LOGIC_REPAIRED` | `NEXT_BAR_OPEN_BID_ASK` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI7_CORE_TRADING_LOGIC_REPAIRED` | `STRESS_WIDER_SPREAD_SLIPPAGE_2X` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI8_RESEARCH_DISCOVERY_REPAIRED` | `LEGACY_CLOSE_FILL` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI8_RESEARCH_DISCOVERY_REPAIRED` | `NEXT_BAR_OPEN_BID_ASK` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI8_RESEARCH_DISCOVERY_REPAIRED` | `STRESS_WIDER_SPREAD_SLIPPAGE_2X` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS` | `LEGACY_CLOSE_FILL` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS` | `NEXT_BAR_OPEN_BID_ASK` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS` | `STRESS_WIDER_SPREAD_SLIPPAGE_2X` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

## 6. Errata for Prior Initial `.11` Handover (`20261109T063349Z_65b8e1172715`)
Prior `.11` run directory (`20261109T063349Z_65b8e1172715`) and `HANDOVER_V17_9_3_1_2_11.txt` are preserved byte-immutable. The following 3 narrative discrepancies in the prior handover are corrected here from raw source artifacts:
1. **Venn Partition**: In `15_recovered_15_trades_exit_forensics_and_venn.json`, the 15 unique closed trades partition as `SI1_H1_H2+SI3_H4_only = 6` and `SI2_H3_only = 9` (with `0` in the other 5 cells; `6 + 9 = 15` unique trades from `6 + 9 + 6 = 21` occurrences), correcting the prior prose string.
2. **`SI0_OFF_BASELINE` Fee Split**: In `14_independent_realized_pnl_and_fee_recomputation.json`, `SI0_OFF_BASELINE` (`NEXT_BAR_OPEN_BID_ASK`) has `entry_fees = 101.5524 USD` and `exit_fees = 101.5611 USD` (`total_fees = 203.1135 USD`, `gross = -142.7836 USD`, `net = -345.8971 USD`), correcting the prior handover fee split.
3. **Negative Controls Count**: The prior run executed `8` exit-lineage negative controls in `18_exit_lineage_negative_controls_report.json` (plus `10` verification-truth controls in `05`); this Truth Closure run executes all `22` required negative controls (`V01`..`V22`) in `18_negative_controls_v01_to_v22_execution_report.json`.

## 7. Machine-Parseable Authoritative Metrics Block
<!-- METRICS_KV_START -->
METRICS_KV|ACTIVATION_READY|NO
METRICS_KV|BASELINE_ELIGIBLE_OPPORTUNITIES|56710
METRICS_KV|BRANCH|feature/v17-9-2-structural-sl-tp-from-qa
METRICS_KV|CANONICAL_UNIVERSE_AUTHORITATIVE|63085
METRICS_KV|COLLECTED_TEST_NODES|5249
METRICS_KV|DATASET_SHA256|9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18
METRICS_KV|ERROR_TEST_NODES|0
METRICS_KV|EXECUTED_TEST_NODES|5249
METRICS_KV|FAILED_TEST_NODES|0
METRICS_KV|FRESH_OOS_VERDICT|REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS
METRICS_KV|JUNIT_SHARD_COUNT|54
METRICS_KV|MANDATORY_SKIPPED_TEST_NODES|0
METRICS_KV|MODEL_DIRECTION_EVALUATIONS|223460
METRICS_KV|NEGATIVE_CONTROLS_COUNT|40
METRICS_KV|PASSED_TEST_NODES|5241
METRICS_KV|POST_WARMUP_CHECKPOINTS|22346
METRICS_KV|PRIOR_V128_UNIVERSE|75525
METRICS_KV|PRIOR_V129_PRE_DETECTOR_UNIVERSE|63117
METRICS_KV|RAW_EXIT_EVENT_LINEAGE_VERDICT|RECONSTRUCTED_LINEAGE_VERIFIED
METRICS_KV|RAW_M1_BARS|22376
METRICS_KV|RECOVERED_POLICY_OCCURRENCES|21
METRICS_KV|RECOVERED_UNIQUE_CLOSED_TRADES|15
METRICS_KV|RUN_ID|20261110T131004Z_a12759da782f
METRICS_KV|SI0_LEG_CLOSED|164
METRICS_KV|SI0_LEG_ENTRY_FEES_USD|101.6799
METRICS_KV|SI0_LEG_EXIT_FEES_USD|101.6792
METRICS_KV|SI0_LEG_GROSS_PNL_USD|-110.2950
METRICS_KV|SI0_LEG_LOSSES|154
METRICS_KV|SI0_LEG_NET_PNL_USD|-313.6541
METRICS_KV|SI0_LEG_TOTAL_FEES_USD|203.3591
METRICS_KV|SI0_LEG_WINS|10
METRICS_KV|SI0_NB_CLOSED|166
METRICS_KV|SI0_NB_DATA_GAP_UNRESOLVED|1
METRICS_KV|SI0_NB_ENTRY_FEES_USD|101.5524
METRICS_KV|SI0_NB_EXIT_FEES_USD|101.5611
METRICS_KV|SI0_NB_EXPECTANCY_NET_R|-0.8474
METRICS_KV|SI0_NB_GROSS_PNL_USD|-142.7836
METRICS_KV|SI0_NB_LOSSES|157
METRICS_KV|SI0_NB_NET_PNL_USD|-345.8971
METRICS_KV|SI0_NB_OPEN_AT_HORIZON|74
METRICS_KV|SI0_NB_SCHEDULED|242
METRICS_KV|SI0_NB_TOTAL_FEES_USD|203.1135
METRICS_KV|SI0_NB_UNFILLED|1
METRICS_KV|SI0_NB_WINS|9
METRICS_KV|SI0_STR_CLOSED|173
METRICS_KV|SI0_STR_ENTRY_FEES_USD|106.4227
METRICS_KV|SI0_STR_EXIT_FEES_USD|106.4338
METRICS_KV|SI0_STR_GROSS_PNL_USD|-168.9096
METRICS_KV|SI0_STR_LOSSES|164
METRICS_KV|SI0_STR_NET_PNL_USD|-381.7661
METRICS_KV|SI0_STR_TOTAL_FEES_USD|212.8565
METRICS_KV|SI0_STR_WINS|9
METRICS_KV|SI8_DETECTOR_VERIFIED_INCREMENTAL|6375
METRICS_KV|SKIPPED_TEST_NODES|8
METRICS_KV|STRATEGY_EXPECTANCY_VERDICT|NEGATIVE_EXPECTANCY
METRICS_KV|TESTED_EXECUTABLE_SHA|a12759da782fbf2399657c727f57138d23280b3b
METRICS_KV|TOTAL_CLOSED_TRADES_ALL_PARTITIONS|566
METRICS_KV|TOTAL_EPISODES_ALL_PARTITIONS|836
METRICS_KV|TOTAL_PARTITIONS_VERIFIED|30
METRICS_KV|TOTAL_RAW_LINEAGE_EVENTS|7837
METRICS_KV|VENN_SI1_ONLY|0
METRICS_KV|VENN_SI1_SI2_ONLY|0
METRICS_KV|VENN_SI1_SI2_SI3|0
METRICS_KV|VENN_SI1_SI3_ONLY|6
METRICS_KV|VENN_SI2_ONLY|9
METRICS_KV|VENN_SI2_SI3_ONLY|0
METRICS_KV|VENN_SI3_ONLY|0
METRICS_KV|VERSION|v17_9_3_1_2_11
METRICS_KV|WARMUP_M1_BARS|30
METRICS_KV|WORKER_1_MEMBERSHIP_SHA256|b65795668fb8422c12a7e131610245406e1c2db6ca2552fb77ade55b8bd20167
METRICS_KV|WORKER_1_ORDERED_SHA256|5b2ec10d64cb5e3fab6b045fc27761aeff6d9adfa54dd69cb5d931635db07f5c
METRICS_KV|WORKER_1_REPEAT_ORDERED_SHA256|5b2ec10d64cb5e3fab6b045fc27761aeff6d9adfa54dd69cb5d931635db07f5c
METRICS_KV|WORKER_2_ORDERED_SHA256|5b2ec10d64cb5e3fab6b045fc27761aeff6d9adfa54dd69cb5d931635db07f5c
METRICS_KV|WORKER_4_DISTINCT_PID_COUNT|4
METRICS_KV|WORKER_4_ORDERED_SHA256|5b2ec10d64cb5e3fab6b045fc27761aeff6d9adfa54dd69cb5d931635db07f5c
<!-- METRICS_KV_END -->

## 7. V17.9.3.1.2.12 P0-A, P0-B, R1, R2, R3, R4 Summary
- **V17.9.3.1.2.12 Run ID**: `20261110T131004Z_a12759da782f`
- **Tested Executable SHA (Phase A)**: `a12759da782fbf2399657c727f57138d23280b3b`
- **P0-A Provenance Closure**: `read_exit_event_stream_strict_v17931211` rejects missing `evidence_origin` or `quote_source_kind` without defaults, rejects `legacy_sem_hash` on active runs (`allow_legacy_hash_compat=False`), and `verify_all_policy_fill_mode_lineage_independently_v17931211` enforces mandatory `09_original_execution_ledger_source_reconciliation.jsonl.gz` (`require_source_reconciliation=True`).
- **Adversarial Negative Controls (`V01`..`V40`)**: `40 / 40` rejected in isolated temporary directories.

### 7.1 R1 Hierarchical Loss Forensics (`157` Closed Losses / `166` Closed Trades / `242` Scheduled)
- `A_PRE_ENTRY_HTF_CONFLICT`: `22`
- `B_PRE_ENTRY_UNCONFIRMED_OR_CHASING`: `1`
- `C_PRE_ENTRY_STALE_OR_UNBOUND_FVG_UNICORN`: `0`
- `D_PRE_ENTRY_SL_INSIDE_NOISE_OR_UNPROTECTED`: `0`
- `E_PRE_ENTRY_TARGET_SWEPT_OR_UNREACHABLE`: `0`
- `F_EX_POST_STOP_BEFORE_STRUCTURE_BREAK_NOISE_SWEEP` (`EX_POST_DIAGNOSTIC_ONLY`): `8`
- `G_EX_POST_TRUE_STRUCTURAL_INVALIDATION_OR_REVERSAL` (`EX_POST_DIAGNOSTIC_ONLY`): `126`
- `H_EX_POST_FRICTION_OR_AMBIGUOUS_BAR_DOMINATED` (`EX_POST_DIAGNOSTIC_ONLY`): `0`
- **Pre-Entry `OTHER/UNKNOWN` Preserved**: `134 / 157`

### 7.2 R2 Single-Axis MTF & Unicorn Ablations Across `63,085` Canonical Opportunities
- `H4_ONLY` passed: `16417`
- `H1_ONLY` passed: `15306`
- `M15_ONLY` passed: `9097`
- `M15_H1_ONLY` passed: `2825`
- `STRUCTURE_ONLY` passed: `2822`
- `LIQUIDITY_ONLY` passed: `50591`
- `FULL_MTF` passed: `1375`
- `SI8/SI9` Materialization Funnel: `284` proof-eligible -> `6` arbitration suppressed -> `278` post-arbitration rejected -> `0` trades.

### 7.3 R3 Matched Entry/SL/TP Counterfactuals (`NEXT_BAR_OPEN_BID_ASK`, `n = 242`, Risk Cap `$2.50`)
| Variant | Closed Trades | Wins | Losses | Net PnL (USD) | Expectancy Net R | Max Planned Risk (USD) |
|---|---:|---:|---:|---:|---:|---:|
| `A0_STRICT_BASELINE` | 166 | 9 | 157 | -345.8971 | -0.8474 | 2.5000 |
| `A1_ENTRY_CONFIRMATION_RETEST_ONLY` | 65 | 3 | 62 | -148.6949 | -0.9313 | 2.5000 |
| `A2_PROTECTED_SL_NOISE_BUFFER_ONLY` | 160 | 9 | 151 | -334.4796 | -0.8482 | 2.5000 |
| `A3_SOURCE_BACKED_LIQUIDITY_TP_ONLY` | 51 | 3 | 48 | -102.9636 | -0.8158 | 2.5000 |
| `A4_COMBINED_CAUSAL_ENTRY_SL_TP` | 14 | 1 | 13 | -26.1876 | -0.7621 | 2.5000 |

### 7.4 R4 Honest OOS & Activation Verdicts
- `FRESH_OOS_VERDICT`: `BLOCKED_NO_UNTOUCHED_DATA` (`REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS`)
- `ECONOMIC_VERDICT`: `INCONCLUSIVE_OR_NEGATIVE_AS_OBSERVED`
- `STRATEGY_EXPECTANCY_VERDICT`: `NEGATIVE_EXPECTANCY`
- `ACTIVATION_READY`: `NO`
