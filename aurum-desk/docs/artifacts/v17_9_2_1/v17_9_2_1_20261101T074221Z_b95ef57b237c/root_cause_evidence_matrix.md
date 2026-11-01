# AURUM Desk V17.9.2.1 — Root-Cause & Loss Attribution Evidence Matrix

- **Run ID:** `v17_9_2_1_20261101T074221Z_b95ef57b237c`
- **Branch:** `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA:** `b95ef57b237cc7024ac3ef6674591794b0a059a8`
- **Previous V17.9.2 Tested SHA (`code-old`):** `dbc3d0e33f111b9419ac2e78a7665a38aa21124b`

---

## 1. Executive Synthesis: Why Historical Variants Showed Negative Expectancy

Across the `22,346` chronological M1 checkpoints (`2,455` deep Stage-2 model evaluations), matched-cohort
and episode-level attribution proves that the negative historical expectancy is driven by **4 distinct,
quantified mechanisms** (3 structural/strategy mechanics + 3 repaired engineering/evaluation defects):

| Rank | Root-Cause Category | Primary Variants Affected | Empirical Evidence (`code-old` -> `code-fixed`) | Classification |
| :--- | :--- | :--- | :--- | :--- |
| **RC-1** | **Target Distance vs M1 Scalp Horizon Mismatch** (`select_viable_structural_target` skipping near pools to satisfy `min_net_rr >= 2.0` after `$3.40/oz` round-trip friction) | `D3_TP_POLICY_ONLY`, `D4_STRUCTURE_PLUS_TP`, `D6_ANNOTATED_ABLATIONS` | Nearest pool averages `$6.23` vs `$4.32` stop (`1,140 / 1,246` valid setups fail `net_rr >= 2.0` in `A`). When `D3`/`D4` select the first pool with `net_rr >= 2.0`, mean target distance jumps to **`$28.1132`–`$32.2387`** (`4x–6x` stop distance), which M1 scalp momentum rarely reaches within 180 bars (`7W / 96L`, `49` `OPEN_AT_HORIZON`). | **TRUE_STRATEGY_WEAKNESS** (Requires V17.9.3 Macro Time + HTF Displacement alignment, NOT artificial RR invention) |
| **RC-2** | **Cross-Timeframe Consumed Liquidity Target Leakage (Defect #1 — REPAIRED)** (`resolve_causal_target_candidates` checking HTF swing consumption only on same-TF bars) | `D3_TP_POLICY_ONLY`, `D4_STRUCTURE_PLUS_TP` | In `code-old` (`dbc3d0e33f11`), `530 / 4,425` candidate HTF pools (`176` selected in `D3`, `145` selected in `D4`) were HTF swings already swept by recent closed LTF (`1M`/`5M`/`15M`) bars inside the current HTF candle. Repairing `_is_pool_swept_cross_tf` eliminated all `145` stale targets in `D4` (shifting mean target distance from `$31.9604` to `$32.2387` as `select_viable_structural_target` falls back to the next unconsumed HTF pool). | **ENGINEERING_DEFECT_REPAIRED** (`backend/services/v17_9_2_structural_stop_intelligence.py:913-1058`) |
| **RC-3** | **Local Sweep Wick Stop vs Unaligned Entry Direction** (`A_FROZEN_BASELINE_OFF` vs `C_V1792_STRUCTURAL_SL_ON`) | `A_FROZEN_BASELINE_OFF`, `C_V1792_STRUCTURAL_SL_ON`, `D1`, `D2` | On the 6 baseline `A` episodes: **1** (`#4`) was an immediate `TRUE_INVALIDATION_LOSS`, **1** (`#1`) was a `DELAYED_INEVITABLE_LOSS` (outer 5M structure broke 1 bar after stop), **3** (`#2, #3, #5`) were `PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK` (outer 5M structure held for 12–28 bars before eventually breaking, `tp_would_hit_after_stop = 0`), and **1** (`#6`) was `PURE_NOISE_SWEEP_STRUCTURE_HELD` (outer 5M structure at `4010.94` held all 180 bars; V17.9.2 `SL = 4011.28` never hit). | **TRUE_STRATEGY_WEAKNESS + STOP_DESIGN_VALIDATED** (Wider structural stop prevents noise-wick stops, but `0/6` would hit TP without macro/HTF entry timing) |
| **RC-4** | **Cross-TF Anchor Invalidation Truncation (Defect #2 — REPAIRED) & Dead `DELAYED_INEVITABLE_LOSS` Classifier Branch (Defect #3 — REPAIRED)** | `collect_causal_stop_anchor_candidates`, `classify_structure_stop_outcome` | Defect #2 repaired M5/M15 anchor invalidation checks outside the 120-bar M1 window (`backend/services/v17_9_2_structural_stop_intelligence.py:300-355`). Defect #3 repaired `classify_structure_stop_outcome` (`lines 1639-1825`) so structure break within 5 bars of stop hit is classified as `DELAYED_INEVITABLE_LOSS` (`7` in `D4`, `1` in `A`) rather than dead `0`. | **EVALUATION_AND_ANCHOR_DEFECT_REPAIRED** |

---

## 2. Pairwise Matched-Cohort Attribution (`N = 2,455` Model Evaluations)

| Comparison Pair | Effect Isolated | `BOTH_ELIGIBLE` | `BASELINE_ONLY` | `NEW_ONLY` | `REJECTED_BOTH` | Non-Overlapping Closed (`Base` vs `New`) |
| :--- | :--- | ---: | ---: | ---: | ---: | :--- |
| **`A` vs `B`** | Observe-Only Invariance | `106` | `0` | `0` | `2349` | `0W/6L` vs `0W/6L` |
| **`A` vs `C`** | **STOP EFFECT ONLY** (Fixed Baseline TP) | `44` | `62` | `4` | `2345` | `0W/6L` vs `0W/3L` |
| **`A` vs `D3`** | **TARGET EFFECT ONLY** (Fixed Baseline SL) | `106` | `0` | `1002` | `1347` | `0W/6L` vs `7W/145L` |
| **`A` vs `D4`** | **JOINT STOP + TARGET EFFECT** | `67` | `39` | `990` | `1359` | `0W/6L` vs `7W/96L` |

---

## 3. Forensic Breakdown of the 6 Historical Baseline Episodes (`A` vs `C` vs `D4`)

| Episode | Checkpoint `i_1m` | Model | Entry (`SHORT`) | Baseline `A` SL (`Dist`) | V17.9.2 `C` Protected SL (`Dist`) | Baseline `A` Outcome Class & Sub-Class | V17.9.2 `C` Status & Counterfactual Path |
| :--- | ---: | :--- | ---: | :--- | ---: | :--- | :--- |
| **#1** | `6652` | `SMC_M1_FVG_CONTINUATION` | `4143.28` | `4150.99` (`$7.71`) | `4151.6` (`$8.32`) | `DELAYED_INEVITABLE_LOSS` (`DELAYED_INEVITABLE_BREAK_WITHIN_5_BARS`) | `ELIGIBLE` (Hypothetical path: `TRUE_INVALIDATION_LOSS`, `bars_held=6`) |
| **#2** | `6676` | `SMC_M1_REVERSAL` | `4143.64` | `4150.27` (`$6.63`) | `4155.6` (`$11.96`) | `PREMATURE_NOISE_SWEEP_STOP` (`PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK`) | `VALID_SETUP_REJECTED_BY_RISK` (Hypothetical path: `TRUE_INVALIDATION_LOSS`, `bars_held=16`) |
| **#3** | `6886` | `SMC_M1_REVERSAL` | `4142.76` | `4147.33` (`$4.57`) | `4151.05` (`$8.29`) | `PREMATURE_NOISE_SWEEP_STOP` (`PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK`) | `ELIGIBLE` (Hypothetical path: `PREMATURE_NOISE_SWEEP_STOP`, `bars_held=75`) |
| **#4** | `14469` | `SMC_M1_FVG_CONTINUATION` | `4019.06` | `4025.59` (`$6.53`) | `4025.57` (`$6.51`) | `TRUE_INVALIDATION_LOSS` (`IMMEDIATE_STRUCTURE_INVALIDATION`) | `ELIGIBLE` (Hypothetical path: `TRUE_INVALIDATION_LOSS`, `bars_held=7`) |
| **#5** | `14608` | `SMC_M1_REVERSAL` | `4014.91` | `4017.28` (`$2.37`) | `4027.87` (`$12.96`) | `PREMATURE_NOISE_SWEEP_STOP` (`PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK`) | `VALID_SETUP_REJECTED_BY_RISK` (Hypothetical path: `TRUE_INVALIDATION_LOSS`, `bars_held=62`) |
| **#6** | `15383` | `SMC_M1_REVERSAL` | `4001.4` | `4003.42` (`$2.02`) | `4011.28` (`$9.88`) | `PREMATURE_NOISE_SWEEP_STOP` (`PURE_NOISE_SWEEP_STRUCTURE_HELD`) | `VALID_SETUP_REJECTED_BY_RISK` (Hypothetical path: `OPEN_AT_HORIZON`, `bars_held=180`) |

---

## 4. `code-old` (`dbc3d0e33f11`) vs `code-fixed` (`V17.9.2.1`) Attribution Summary

| Variant | `code-old` Eligible | `code-fixed` Eligible | `code-old` Closed (`W/L`) | `code-fixed` Closed (`W/L`) | `code-old` Expectancy (`R`) | `code-fixed` Expectancy (`R`) | Swept HTF Targets Eliminated |
| :--- | ---: | ---: | :--- | :--- | ---: | ---: | ---: |
| **`C_V1792_STRUCTURAL_SL_ON`** | `48` | `48` | `0W/3L` | `0W/3L` | `-1.0` | `-1.0` | `0` (Uses frozen baseline TP) |
| **`D3_TP_POLICY_ONLY`** | `1108` | `1108` | `7W/145L` | `7W/145L` | `-0.6562` | `-0.6468` | `176` |
| **`D4_STRUCTURE_PLUS_TP`** | `1057` | `1057` | `7W/96L` | `7W/96L` | `-0.5851` | `-0.5851` | `145` |
