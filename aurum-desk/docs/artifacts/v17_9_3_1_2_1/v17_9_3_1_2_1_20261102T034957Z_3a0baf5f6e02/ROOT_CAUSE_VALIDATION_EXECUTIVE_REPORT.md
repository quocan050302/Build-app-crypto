# AURUM DESK / XAUUSDT / BITGET — V17.9.3.1.2.1 ROOT-CAUSE VALIDATION EXECUTIVE REPORT

- **Run ID**: `v17_9_3_1_2_1_20261102T034957Z_3a0baf5f6e02`
- **Mandatory Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable Source SHA (`Phase A`)**: `3a0baf5f6e0244d62375400ff047ff73053b6281`
- **Canonical Dataset SHA-256**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **Canonical Decision Stream SHA-256**: `cd86760e6e8e65c5658b78773301aea550a5c62650ef6a30ffad6accf28e7317`
- **Engineering Verification Verdict**: `PASS` (`46/46` gates PASS)
- **Strategy Statistical Verdict**: `NEGATIVE_EXPECTANCY`
- **Commercial / Production Activation Ready**: `NO`

---

## 1. Closure of P0-A..P0-F and P1-G..P1-I Defects

| Defect ID | Root Issue in V17.9.3.1.2 | V17.9.3.1.2.1 Verified Resolution |
| :--- | :--- | :--- |
| **P0-A** | `unlock_followups_per_episode=2` truncated candidate universe to `2,112` records | Full event-driven replay across all **`18744`** unique eligible M1 timestamps (**`56710`** model decisions) for `R0..R8` |
| **P0-B** | Keying matched opportunities by `setup_id` collapsed `2,112 -> 1,070` and created `120` fake newly-blocked trades on `R0` | Lossless composite `opportunity_id`; full outer join has **`18744`** unique IDs, `0` duplicates, and `R0_BASELINE_CONTROL` has **`0` newly blocked / `0` newly unlocked** |
| **P0-C** | `simulate_executable_candidate_v179312` passed `protected_structure_price=inv_px`, making `120` `TRUE_INVALIDATION_LOSS` labels circular | Independent pre-entry causal protected structure (`known_at_ms <= decision_ts`) resolved; `0` circular `stop == anchor` labels without independent provenance; `157` A1 losses reclassified (`{'PROVIDER_GAP_UNRESOLVED': 4, 'STRUCTURE_BROKE_AFTER_SL': 43, 'SL_AND_STRUCTURE_BROKEN_SAME_BAR': 84, 'STRUCTURE_BROKE_BEFORE_SL': 10, 'SL_WICK_HIT_STRUCTURE_HELD': 8, 'UNKNOWN_PROTECTED_STRUCTURE_NOT_ESTABLISHED': 8}`) |
| **P0-D** | `74` `OPEN_AT_HORIZON` and `1` `DATA_GAP_UNRESOLVED` positions released slots at bar 180 without exit | All **`74`** open positions resolved (`{'CLOSED_SL': 54, 'CENSORED_DATA_GAP_OPEN': 4, 'CLOSED_TP': 11, 'CENSORED_END_OF_DATASET_OPEN': 5}`); compared `STRICT_HOLD_UNTIL_EXIT_OR_DATA_END` (`6` closed, `100.00%` loss rate) vs `LEGACY_ARTIFICIAL_RELEASE` (`166` closed, `94.58%` loss rate) vs `EXECUTABLE_180_BAR_TIME_STOP_INTERVENTION` |
| **P0-E** | Static hardcoded root-cause prose in `compute_loss_root_cause_matrix_v179312` | Replaced by computed Evidence Hierarchy (`LEVEL_0..LEVEL_4`) across pre-registered `H1..H9`; legacy prose tagged `HYPOTHESIS_ONLY` |
| **P0-F** | Post-test edit to verification script drifted `HEAD` from `tested_code_sha` | Non-self-referential Phase A (`tested_code_sha=3a0baf5f6e0244d62375400ff047ff73053b6281`) -> Phase B (artifacts only) -> Phase C (zero executable diff vs `tested_code_sha`) |
| **P1-G** | `identical_intervention_replays=True` was hardcoded | Real 1-vs-2-vs-4 worker SHA-256 comparison across stream, portfolios, `R0..R8` interventions, and OOS (`all_1_2_4_worker_hashes_identical=True`) |
| **P1-H** | `R3` checked isolated 3-bar FVG retest without causal Breaker overlap | Separated `FVG_RETEST_ONLY` from `UNICORN_CONFIRMED`; strict Unicorn rejects `8142` candidates with `MISSING_CAUSAL_BREAKER_BINDING` |
| **P1-I** | Spot gold session hours conflated with Bitget perpetual availability | Tagged `exchange_service_state=NOT_VERIFIED_FROM_HISTORICAL_CANDLES` and `quote_source_type=SYNTHETIC_FROM_OHLC_SPREAD_TABLE`; audited `WEEKEND_CLOSED` (`1W/34L`) and `NY_PM` (`0W/30L`) |

---

## 2. Full-Stream Event-Driven Intervention Replay (`R0..R8` on 18,744 Opportunities)

| Policy Variant | Evaluated | Accepted (Mod / No-Op) | Rejected | Closed Trades | Wins / Losses | Loss Rate | Mean E[R] | Modeled Net PnL (USD) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `R0_BASELINE_CONTROL` | 18744 | 0 / 18744 | 0 | 166 | 9W / 157L | 94.58% | -0.8474 | $-345.90 |
| `R1_HTF_H1_H4_ALIGNMENT_FILTER_ONLY` | 18744 | 0 / 8416 | 10328 | 111 | 5W / 106L | 95.50% | -0.8458 | $-231.47 |
| `R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE` | 18744 | 2837 / 0 | 15907 | 73 | 2W / 71L | 97.26% | -0.9157 | $-164.08 |
| `R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE` | 18744 | 7745 / 0 | 10999 | 119 | 5W / 114L | 95.80% | -0.8954 | $-261.79 |
| `R4_OUTER_STRUCTURAL_STOP_EXECUTABLE` | 18744 | 12653 / 0 | 6091 | 132 | 6W / 126L | 95.45% | -0.8821 | $-285.76 |
| `R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE` | 18744 | 866 / 495 | 17383 | 48 | 5W / 43L | 89.58% | -0.7270 | $-85.80 |
| `R6_MACRO_SESSION_FILTER_ONLY` | 18744 | 0 / 1453 | 17291 | 42 | 2W / 40L | 95.24% | -0.9053 | $-93.33 |
| `R7_COMBINED_ENTRY_SL_TP_EXECUTABLE` | 18744 | 3 / 0 | 18741 | 1 | 0W / 1L | 100.00% | -0.9941 | $-2.43 |
| `R8_TIMING_PLUS_OUTER_SL_ABLATION_EXECUTABLE` | 18744 | 2104 / 0 | 16640 | 63 | 2W / 61L | 96.83% | -0.9154 | $-141.42 |

---

## 3. Production Model Funnel & Leave-One-Out / Solo Ablations

| Production Model (Priority Order) | Eligible Decisions | Discarded at Tie | Blocked by Open Pos | Closed Trades | Wins / Losses | Closed Loss Rate |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `SMC_M1_REVERSAL` (#1) | 8879 | 0 | 8747 | 90 | 6W / 84L | 93.33% |
| `SMC_M1_CONTINUATION` (#2) | 4044 | 1817 | 3977 | 24 | 0W / 24L | 100.00% |
| `SMC_M1_FVG_CONTINUATION` (#3) | 17898 | 10262 | 17718 | 52 | 3W / 49L | 94.23% |
| `SMC_M1_SWEEP_RECLAIM` (#4) | 7991 | 7989 | 7861 | 0 | 0W / 0L | N/A |
| `CG01_SWEEP_MSS_FVG` (#5) | 17898 | 17898 | 17718 | 0 | 0W / 0L | N/A |

---

## 4. Computed Root-Cause Ranking (`H1..H9`) & Epistemic Hierarchy

| Rank | Hypothesis ID | Evidence Hierarchy Level | Best Delta E[R] | Best Delta Net PnL (USD) | Causal Verdict |
| ---: | :--- | :--- | ---: | ---: | :--- |
| #1 | `H5_UNREALISTIC_TARGET_DISTANCE_AND_MFE_REVERSAL` | `LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION` | +0.1204R | $+343.47 | `CONVERTS_HIGH_MFE_REVERSALS_TO_WINS_BUT_CANNOT_FIX_ZERO_MFE_ENTRIES` |
| #2 | `H3_HTF_H1_H4_STRUCTURAL_CONFLICT` | `LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION` | +0.0016R | $+114.43 | `WEAK_MARGINAL_EFFECT_HTF_ALIGNMENT_ALONE_DOES_NOT_RESCUE_M1_ENTRIES` |
| #3 | `H6_OFF_HOURS_WEEKEND_AND_SESSION_REGIME_DRAG` | `LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION` | +0.0000R | $+252.57 | `REDUCES_CHURN_AND_DOLLAR_DRAWDOWN_SIGNIFICANTLY_VIA_EXPOSURE_REDUCTION` |
| #4 | `H4_LOCAL_M1_STOP_INSIDE_NOISE_ZONE` | `LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION` | +0.0000R | $+204.48 | `OUTER_STOP_WITH_UNCHANGED_2R_CONSTRAINT_PUSHES_TP_TOO_FAR_OR_REJECTS_GEOMETRY` |
| #5 | `H2_IMMEDIATE_M1_ENTRY_WITHOUT_CONFIRMATION` | `LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION` | +0.0000R | $+181.82 | `PARTIAL_LOSS_REDUCTION_VIA_SELECTIVITY_BUT_EXPECTANCY_REMAINS_NEGATIVE` |
| #6 | `H1_MODEL_SELECTION_AND_ARBITRATION` | `LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION` | +0.0000R | $+0.00 | `VERIFIED_DOMINANT_SIGNAL_GENERATOR_NEGATIVE_EXPECTANCY_ACROSS_ALL_FIVE_MODELS` |
| #7 | `H7_EXECUTION_FRICTION_SPREAD_SLIPPAGE_FEE_DRAG` | `LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION` | +0.0000R | $+0.00 | `MATERIAL_COST_DRAG_AMPLIFIER_ON_TOP_OF_NEGATIVE_GROSS_SIGNAL_EXPECTANCY` |
| #8 | `H8_HORIZON_CENSORING_AND_ARTIFICIAL_SLOT_RELEASE` | `LEVEL_3_MATCHED_COUNTERFACTUAL_ABLATION` | +0.0000R | $+0.00 | `VERIFIED_CENSORING_SELECTION_BIAS_AND_PHANTOM_SLOT_RELEASE_INFLATED_TRADE_CHURN` |
| #9 | `H9_DATA_GAPS_AND_SYNTHETIC_QUOTE_MODELING` | `LEVEL_2_CONTROLLED_ASSOCIATION` | +0.0000R | $+0.00 | `MINOR_BOUNDED_CONTRIBUTION_NOT_PRIMARY_LOSS_DRIVER` |

---

## 5. Verification Summary (`G01..G46`, Async Pytest Bijection & RED/GREEN Mutations)

- **Pytest Shards**: `140` collected == `140` executed, `0` failed (`bijection_valid=True`).
- **RED/GREEN Mutation Controls**: `12` mutations verified (`all_mutations_caught_red_and_restored_green=True`).
- **Acceptance Gates (`G01..G46`)**: `46/46` PASS (`failed_gate_ids=[]`).
