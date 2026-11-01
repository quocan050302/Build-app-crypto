# AURUM Desk V17.9.3 — Final Verification & Historical A/B/C/D/E Report

## 1. Executive Summary & Verdicts
- **Run ID:** `v17_9_3_20261101T101517Z_8350cf25528c`
- **Working Branch:** `feature/v17-9-2-structural-sl-tp-from-qa`
- **Base Master SHA:** `650e4535777421077443cd3d95507cad1d99ed42`
- **Original Feature HEAD SHA (V17.9.2.1):** `2aa3041e47c2a3d98073fef3e46ec854955aba11`
- **Previous V17.9.2.1 Tested Code SHA:** `b95ef57b237cc7024ac3ef6674591794b0a059a8`
- **Tested Executable SHA (V17.9.3):** `8350cf25528cffa7138ee9020e7290fd1c6c5192`
- **Executable Source Tree Hash:** `fad495b746b98dc1abeadded920b76706913b7857d2fb44310af751ab3e106c0`
- **Engineering Verdict:** `PASS` (`20/20` Gates)
- **Historical Replay Verdict:** `PASS`
- **Strategy Statistical Verdict:** `INCONCLUSIVE` (`D1=INCONCLUSIVE`, `A1=NEGATIVE_EVIDENCE`)
- **Ready For Strategy Activation (LIVE/AUTO-PAPER):** `NO`

## 2. Historical Dataset & Macro/Unicorn Universe Scan
- **Verified M1 Bars:** `22376` (`491` Stage-2 deep checkpoints, `2,455` model evaluations per variant)
- **All-M1 Checkpoints Inside NY ICT Macro Windows:** `1763` / `22346`
- **Stage-2 Deep Checkpoints Inside NY ICT Macro Windows:** `51` / `491` (`255` model evaluations)
- **Future Data / Unclosed Bar Leaks:** `0` / `0`

## 3. 10-Variant Historical A/B/C/D/E Comparison
| Variant | Mode | Eligible Decisions | Non-Overlap Episodes | Closed | Wins | Losses | Win Rate | Expectancy (R) | Net PnL (USD) | Profit Factor |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `A0_FROZEN_BASELINE_OFF` | `OFF` | 106 | 6 | 6 | 0 | 6 | 0.0 | -1.0 | -15.0 | 0.0 |
| `B0_V1793_OBSERVE_ONLY` | `OBSERVE_ONLY` | 106 | 6 | 6 | 0 | 6 | 0.0 | -1.0 | -15.0 | 0.0 |
| `C1_V1793_MACRO_FILTER_ONLY` | `MACRO_FILTER_ONLY` | 6 | 1 | 1 | 0 | 1 | 0.0 | -1.0 | -2.5 | 0.0 |
| `C2_V1793_UNICORN_ONLY` | `UNICORN_ONLY` | 6 | 1 | 1 | 0 | 1 | 0.0 | -1.0 | -2.5 | 0.0 |
| `A1_V1792_SL_TP_BASELINE` | `OFF` | 1057 | 152 | 103 | 7 | 96 | 0.068 | -0.5851 | -150.6728 | 0.3722 |
| `C1_MACRO_PLUS_V1792_SL_TP` | `MACRO_FILTER_ONLY` | 91 | 21 | 18 | 1 | 17 | 0.0556 | -0.6725 | -30.2608 | 0.288 |
| `C2_UNICORN_PLUS_V1792_SL_TP` | `UNICORN_ONLY` | 58 | 23 | 19 | 2 | 17 | 0.1053 | -0.3632 | -17.2536 | 0.594 |
| `D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP` | `MACRO_PLUS_UNICORN_V1792_SL_TP` | 12 | 5 | 5 | 1 | 4 | 0.2 | 0.1791 | 2.2392 | 1.2239 |
| `D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL` | `MACRO_PLUS_UNICORN_MTF_FULL` | 0 | 0 | 0 | 0 | 0 | None | None | 0 | None |
| `E1_OUTSIDE_MACRO_COUNTERFACTUAL` | `OUTSIDE_MACRO_COUNTERFACTUAL` | 750 | 110 | 74 | 6 | 68 | 0.0811 | -0.5022 | -92.912 | 0.4535 |

## 4. Unconfounded Matched-Trade Forensics vs `A1_V1792_SL_TP_BASELINE`
| Candidate Variant | Retained Eligible | Filtered Losers Avoided | Filtered Winners Forgone | Newly Eligible Rogue | Avoided Loss (R) | Forgone Win (R) | Net Filter Benefit (R) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `C1_MACRO_PLUS_V1792_SL_TP` | 91 | 595 | 25 | 0 | 595.0 | 179.7938 | 415.2062 |
| `C2_UNICORN_PLUS_V1792_SL_TP` | 58 | 630 | 23 | 0 | 630.0 | 169.388 | 460.612 |
| `D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP` | 12 | 666 | 25 | 0 | 666.0 | 179.7938 | 486.2062 |
| `D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL` | 0 | 676 | 27 | 0 | 676.0 | 189.5852 | 486.4148 |
| `E1_OUTSIDE_MACRO_COUNTERFACTUAL` | 750 | 220 | 4 | 0 | 220.0 | 75.3342 | 144.6658 |

## 5. Parity, Invariance & Full Backend Regression
- **Oracle vs `A0_FROZEN_BASELINE_OFF` Mismatches:** `0` / `None`
- **`A0_FROZEN_BASELINE_OFF` vs `B0_V1793_OBSERVE_ONLY` Trade-Action Mismatches:** `0` / `2455`
- **3-Runtime Adapter Consumed Decision Parity Checks:** `7365` (`0` mismatches)
- **Full Pytest Regression Inventory:** `4599` passed, `0` skipped, `0` xfail, `0` failed, `0` errors out of `4599` discovered (`reconciled=True`)

## 6. 20 Acceptance Gates (`G01`–`G20`)
| Gate | Title | Status |
|---|---|---|
| `G01` | Git Working Branch, master Ancestry & Clean Source Freeze | `PASS` |
| `G02` | Function Truth Map (#1-#19) & Zero Wall-Clock AST Verification | `PASS` |
| `G03` | Causal NY ICT Macro Schedule, Half-Open Boundaries & DST Transition Audit | `PASS` |
| `G04` | Verified Historical Dataset (22,376 M1 Bars / 22,346 Checkpoints) & Provenance Segregation | `PASS` |
| `G05` | Full M1 Macro & Unicorn Universe Scan (22,346 Checkpoints / 491 Deep Checkpoints) | `PASS` |
| `G06` | Causal ICT Unicorn Confluence Audit & Zero Lookahead Proof | `PASS` |
| `G07` | Strict Causal Episode Binding & No Silent selected_fvg Substitution | `PASS` |
| `G08` | Unicorn Distal Invalidation vs V17.9.2.1 Stop & Outward Tick Rounding | `PASS` |
| `G09` | Default OFF & OBSERVE_ONLY 100% Trade-Action Invariance (2,455/2,455) | `PASS` |
| `G10` | Active Mode Monotonic Gating (Zero Rogue Trade Unlocks) | `PASS` |
| `G11` | 10-Variant Historical A/B/C/D/E Comparison & Decision Funnel Completeness | `PASS` |
| `G12` | Matched-Trade & Episode Forensics + 6 Frozen Baseline Episodes Forensics | `PASS` |
| `G13` | Inside-Macro (C1) vs Outside-Macro Counterfactual (E1) Attribution | `PASS` |
| `G14` | Non-Overlapping Independent Episode Deduplication | `PASS` |
| `G15` | Chronological OOS Split (180-M1 Embargo) & Statistical Verdict Guard | `PASS` |
| `G16` | 3-Runtime Adapter Consumed Decision Parity (7,365 Checks, 0 Mismatches) | `PASS` |
| `G17` | Cost, Sizing ($2.50 Risk Cap) & min_net_rr >= 2.0 Unweakened Invariants | `PASS` |
| `G18` | 6 RED/GREEN Negative Controls & Subprocess Mutation Probe | `PASS` |
| `G19` | Full Dynamically Discovered Pytest Regression & Parallel Determinism | `PASS` |
| `G20` | Resource Guard Bounds & Truthful Statistical Verdict (Activation=NO) | `PASS` |
