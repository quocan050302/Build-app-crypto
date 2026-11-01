# AURUM Desk V17.9.3.1 — Historical Execution Truth, Causal Candidate Coverage, MTF Root-Cause & Forensic Closure

## 1. Executive Summary & Dual Verdict
- **Run ID**: `v17_9_3_1_20261101T135550Z_a00a17091bf7`
- **Working Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Starting Feature HEAD SHA**: `37ce4a2daf5ebfa0339ea219e57a81859bb568aa`
- **Prior V17.9.3 Executable Code SHA**: `8350cf25528cffa7138ee9020e7290fd1c6c5192`
- **Prior V17.9.2.1 Executable Code SHA**: `b95ef57b237cc7024ac3ef6674591794b0a059a8`
- **Base `master` SHA**: `650e4535777421077443cd3d95507cad1d99ed42`
- **Tested Executable Code SHA**: `a00a17091bf7991855aaeb3f718242354c244a08`
- **Engineering Closure Verdict**: **`PASS`** (`26/26` acceptance gates passed)
- **Strategy Statistical Verdict**: **`INCONCLUSIVE`** (`D1`: `INCONCLUSIVE`, `D2`: `INCONCLUSIVE`, `A1`: `NEGATIVE_EVIDENCE`)
- **Ready for Strategy Activation**: **`NO`**

## 2. Modeled Execution Accounting Reconciliation
- Replaced all synthetic `round(sum(ep_r_vals) * 250.0, 2)` shortcuts with `ExecutableTradeLedgerEntryV17931` net cashflow accounting (`NEXT_BAR_OPEN_BID_ASK` primary fill mode, 0.01-lot step flooring, explicit entry/exit fees, half-spread and slippage).
- Maximum discrepancy between summed trade ledger net P&L and variant summary net P&L across all 10 variants: `\$0.000000`.

## 3. Full 22,346 Decision Universe & Causal Candidate Coverage
- Total post-warmup M1 decision bars (`idx_1m` 240..22585): **`22346`**
- Inside ICT macro windows: **`1763`** (`0.078896`) | Outside ICT macro windows: **`20583`** (`0.921104`)
- Screened causal candidate bars (100% evaluated without stride-6 skipping): **`3788`**
- Stratified non-candidate holdout bars verified: **`240`** -> False negatives: **`0`**

## 4. 10-Variant Historical Comparison (`FULL_22346_CAUSAL_UNIVERSE`, `NEXT_BAR_OPEN_BID_ASK`)
| Variant | Eligible Decisions | Independent Episodes | Closed | W/L | Win Rate | Expectancy (R net) | Net P&L (USD) | Total Friction (USD) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `A0_FROZEN_BASELINE_OFF` | 106 | 6 | 6 | 0/6 | 0.0 | -0.9992 | \$-14.80 | \$6.18 |
| `B0_V1793_OBSERVE_ONLY` | 106 | 6 | 6 | 0/6 | 0.0 | -0.9992 | \$-14.80 | \$6.18 |
| `C1_V1793_MACRO_FILTER_ONLY` | 6 | 1 | 1 | 0/1 | 0.0 | -1.0016 | \$-2.49 | \$1.47 |
| `C2_V1793_UNICORN_ONLY` | 6 | 1 | 1 | 0/1 | 0.0 | -0.9986 | \$-2.49 | \$1.38 |
| `A1_V1792_SL_TP_BASELINE` | 9467 | 202 | 139 | 7/132 | 0.0504 | -0.8235 | \$-282.49 | \$175.56 |
| `C1_MACRO_PLUS_V1792_SL_TP` | 4442 | 62 | 41 | 2/39 | 0.0488 | -0.8365 | \$-84.31 | \$42.59 |
| `C2_UNICORN_PLUS_V1792_SL_TP` | 1923 | 104 | 81 | 6/75 | 0.0741 | -0.7594 | \$-152.16 | \$108.77 |
| `D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP` | 263 | 20 | 16 | 1/15 | 0.0625 | -0.8044 | \$-31.69 | \$19.21 |
| `D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL` | 50 | 2 | 2 | 1/1 | 0.5 | 0.5617 | \$2.74 | \$2.26 |
| `E1_OUTSIDE_MACRO_COUNTERFACTUAL` | 4214 | 139 | 98 | 6/92 | 0.0612 | -0.7856 | \$-189.82 | \$116.25 |

## 5. `D1` vs `D2` MTF Root-Cause Forensics
- Fixed `bind_unicorn_to_causal_episode` in `backend/services/v17_9_3_macro_unicorn_intelligence.py` to inspect `mtf_context.mtf_structure` (with `ablation_mode="STRUCTURE_ONLY"`) instead of non-existent `mtf_context.timeframe_states`.
- On the 5 `D1` historical episodes (`4680`, `5052`, `5238`, `10434`, `19624`), checkpoint `10434` (`SMC_M1_FVG_CONTINUATION`, `SHORT`) was recovered by `D2` (`D2_ELIGIBLE_AFTER_MTF_BINDING_FIX`, LOSS `-1.0R`, `-\$226.74`), while checkpoints `4680`, `5052`, `5238`, and `19624` have genuine H4/H1 structure opposition (`TRUE_H4_H1_STRUCTURE_CONFLICT` / `TRUE_H1_STRUCTURE_CONFLICT`).

## 6. Six Frozen Baseline Episodes Reconciliation (V17.9.2.1 vs V17.9.3)
- Reconciled all 6 frozen `A0` episodes (`1795`, `4680`, `5052`, `5238`, `10434`, `19624`) under both protected-structure authorities:
  - `CAUSAL_MTF_SERVICE_SWING` (V17.9.2.1 outer anchor): `4` `PREMATURE_NOISE_SWEEP_STOP`, `1` `DELAYED_INEVITABLE_LOSS`, `1` `TRUE_INVALIDATION_LOSS`
  - `EXECUTION_SWING_EXTRACTOR_V1792` (V17.9.3 local M1 swing anchor): `0` `PREMATURE_NOISE_SWEEP_STOP`, `1` `DELAYED_INEVITABLE_LOSS`, `5` `TRUE_INVALIDATION_LOSS`
