# AURUM Desk (Bitget XAUUSDT) — `v17_9_3_1_2_10` Executive Technical & Strategy Report

- **Version**: `v17_9_3_1_2_10`
- **Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA (Phase A)**: `b772a03bb7ccea19ea87067647d64b9e9d9a31a3`
- **Canonical Dataset SHA-256**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **Engineering / Evidence Verdict**: `BLOCKED_PENDING_EXTERNAL_PHASE_C_RECEIPT`
- **Strategy Statistical Verdict**: `INCONCLUSIVE_UNDERPOWERED`
- **Activation Ready**: `NO` (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)

## 1. Canonical Universe & HTF Census (`N = 63085`)

- Post-warmup M1 checkpoints: `22346` (`223460` model-direction evaluations).
- Canonical opportunities: `63085` (`56710` baseline + `6375` SI8 incremental).
- HTF alignment (`N = 63085`): `ALIGNED = 7814`, `COUNTERTREND = 9202`, `MIXED = 46069` (`NOT_ALIGNED_TOTAL = 55271`).

## 2. SI8/SI9 278 Proof-Eligible Materialization Diagnosis

- Proof-eligible candidates: `284` (`6` suppressed by same-timestamp arbitration; `278` arbitration winners).
- Post-proof breakdown (SI8): `{'CANCELLED_SETUP_INVALIDATED_BEFORE_CONFIRMATION': 43, 'REJECTED_CAUSAL_H1_H4_CONFLICT': 113, 'REJECTED_LATE_ENTRY_EXTENSION_OVER_4_ATR': 42, 'REJECTED_NO_CAUSAL_TARGET_IN_NET_RR_BAND': 22, 'SUPPRESSED_BY_ARBITRATION': 6, 'WAIT_WINDOW_EXPIRED_UNCONFIRMED': 58}`.
- Post-proof breakdown (SI9): `{'CANCELLED_SETUP_INVALIDATED_BEFORE_CONFIRMATION': 2, 'REJECTED_CAUSAL_H1_H4_CONFLICT': 5, 'REJECTED_NO_CAUSAL_TARGET_IN_NET_RR_BAND': 2, 'SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN': 267, 'SUPPRESSED_BY_ARBITRATION': 6, 'WAIT_WINDOW_EXPIRED_UNCONFIRMED': 2}`.

## 3. Baseline Forensic Cohort Conservation (`157` Closed Losses vs `242` Scheduled Episodes)

- `baseline_primary_closed_losses_n157` (`N = 157`): `{'OTHER/UNKNOWN': 134, 'SWEEP_NOT_RECLAIMED': 1, 'WRONG_SIDE_HTF': 22}` (sum = `157`).
- `baseline_primary_scheduled_episodes_n242` (`N = 242`): `{'FILL_GAP/AMBIGUOUS_INTRABAR': 1, 'OTHER/UNKNOWN': 208, 'SWEEP_NOT_RECLAIMED': 1, 'WRONG_SIDE_HTF': 32}` (sum = `242`; breakdown = `{'LOSS': 157, 'WIN': 9, 'OPEN_AT_HORIZON': 74, 'DATA_GAP_UNRESOLVED': 1, 'UNFILLED_OR_BLOCKED': 1}`).

## 4. Exact 15 Unique Recovered Trades (`SI1_H1_H2`, `SI2_H3`, `SI3_H4`)

- Policy closed-trade occurrences: `21` (`11 + 8 + 4`) -> `15` unique closed trades (`0` wins, `15` losses).

---

# v17_9_3_1_2_10 — Canonical Universe Lineage & V128/V129/V130 Bijection Report

- **Version Authority**: `v17_9_3_1_2_10`
- **Dataset SHA-256**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **Tested Executable SHA**: `b772a03bb7ccea19ea87067647d64b9e9d9a31a3`

## 1. Stage-by-Stage Canonical Universe Funnel

| Stage | Count |
|---|---:|
| `01_RAW_M1_BARS_TOTAL` | 22376 |
| `02_WARMUP_M1_BARS_EXCLUDED` | 30 |
| `03_RAW_M1_CHECKPOINT` | 22346 |
| `04_MODEL_DIRECTION_EVALUATION` | 223460 |
| `05_BASELINE_ELIGIBLE_OPPORTUNITIES` | 56710 |
| `06_SI8_INCREMENTAL_DISCOVERED_OPPORTUNITIES` | 6375 |
| `07_CANONICAL_OPPORTUNITY_TOTAL` | 63085 |
| `08_VERIFIED_FEATURE_SNAPSHOT_TOTAL` | 63085 |

## 2. Exact Reconciliation of Prior 75,525 vs Current 63085 (`delta = 12440`)

- Baseline eligible opportunities: `56710` (identical across V128, V129, and `v17_9_3_1_2_10`).
- Prior V128 SI8 incremental opportunities: `18815` (`56710 + 18815 = 75525`).
- Current `v17_9_3_1_2_10` SI8 incremental opportunities: `6375` (`56710 + 6375 = 63085`).
- Tuple-level bijection: `18815 - 14766 + 2326 = 6375`.
- HTF alignment census in `v17_9_3_1_2_10` (`N = 63085`): `ALIGNED = 7814`, `COUNTERTREND = 9202`, `MIXED = 46069`, `NOT_ALIGNED_TOTAL = 55271` (`7814 + 55271 = 63085`).
- Historical V128 HTF census (`N = 75525`): `ALIGNED = 8913`, `NOT_ALIGNED_TOTAL = 66612` (deltas: `1,088` aligned + `11,320` not aligned = `12440`).

---

# v17_9_3_1_2_10 — Exact 15 Unique Recovered Closed Trades Casebook (`SI1_H1_H2`, `SI2_H3`, `SI3_H4`)

- **Version Authority**: `v17_9_3_1_2_10`
- **Policy Closed-Trade Occurrences**: `SI1_H1_H2=6`, `SI2_H3=9`, `SI3_H4=6` (total occurrences = `21`)
- **Unique Closed Trades**: `15` (`0` wins, `15` losses)
- **Venn Overlap Partition**: `{'SI1_H1_H2+SI2_H3+SI3_H4': 0, 'SI1_H1_H2+SI2_H3_only': 0, 'SI1_H1_H2+SI3_H4_only': 6, 'SI2_H3+SI3_H4_only': 0, 'SI1_H1_H2_only': 0, 'SI2_H3_only': 9, 'SI3_H4_only': 0}`

| # | Opportunity ID | Model | Dir | Policies | Entry | SL | TP | Gross ($) | Fees ($) | Net ($) | Net R | HTF State | Observed Primary Label |
|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 1 | `opp_9f301fee2e62...` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | `SI2_H3` | 4100.87 | 4104.43 | 4081.07 | -1.3160 | 1.1487 | -2.4647 | -1.0019 | `ALIGNED` | `OTHER/UNKNOWN` |
| 2 | `opp_9f301fee2e62...` | `SMC_M1_CONTINUATION` | `SHORT` | `SI1_H1_H2+SI3_H4` | 4107.68 | 4114.11 | 4081.07 | -1.6325 | 0.8222 | -2.4547 | -0.9898 | `ALIGNED` | `OTHER/UNKNOWN` |
| 3 | `opp_9f301fee2e62...` | `SMC_M1_CONTINUATION` | `SHORT` | `SI1_H1_H2+SI3_H4` | 4110.75 | 4115.34 | 4088.78 | -1.3980 | 0.9872 | -2.3852 | -0.9856 | `ALIGNED` | `OTHER/UNKNOWN` |
| 4 | `opp_9f301fee2e62...` | `SMC_M1_CONTINUATION` | `SHORT` | `SI1_H1_H2+SI3_H4` | 4146.97 | 4155.72 | 4118.88 | -1.7640 | 0.6643 | -2.4283 | -0.9911 | `ALIGNED` | `OTHER/UNKNOWN` |
| 5 | `opp_9f301fee2e62...` | `SMC_M1_CONTINUATION` | `SHORT` | `SI2_H3` | 4122.54 | 4134.05 | 4081.07 | -1.8736 | 0.5284 | -2.4020 | -1.0008 | `ALIGNED` | `OTHER/UNKNOWN` |
| 6 | `opp_9f301fee2e62...` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | `SI2_H3` | 4130.01 | 4133.96 | 4081.07 | -1.3695 | 1.0908 | -2.4603 | -1.0001 | `ALIGNED` | `OTHER/UNKNOWN` |
| 7 | `opp_9f301fee2e62...` | `SMC_M1_CONTINUATION` | `SHORT` | `SI1_H1_H2+SI3_H4` | 4138.28 | 4141.01 | 4120.00 | -1.1200 | 1.3247 | -2.4447 | -0.9779 | `ALIGNED` | `OTHER/UNKNOWN` |
| 8 | `opp_9f301fee2e62...` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | `SI1_H1_H2+SI3_H4` | 4138.98 | 4142.81 | 4117.27 | -1.3362 | 1.1263 | -2.4625 | -0.9850 | `ALIGNED` | `OTHER/UNKNOWN` |
| 9 | `opp_9f301fee2e62...` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | `SI2_H3` | 4098.98 | 4103.00 | 4075.48 | -1.3926 | 1.0827 | -2.4753 | -0.9981 | `ALIGNED` | `OTHER/UNKNOWN` |
| 10 | `opp_9f301fee2e62...` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | `SI2_H3` | 4097.35 | 4100.21 | 4075.48 | -1.1934 | 1.2788 | -2.4722 | -1.0009 | `ALIGNED` | `OTHER/UNKNOWN` |
| 11 | `opp_9f301fee2e62...` | `SMC_M1_CONTINUATION` | `SHORT` | `SI1_H1_H2+SI3_H4` | 3999.24 | 4001.23 | 3985.16 | -0.9522 | 1.4721 | -2.4243 | -0.9775 | `ALIGNED` | `OTHER/UNKNOWN` |
| 12 | `opp_9f301fee2e62...` | `SMC_M1_CONTINUATION` | `SHORT` | `SI2_H3` | 3999.27 | 4000.20 | 3985.16 | -0.6441 | 1.8239 | -2.4680 | -0.9992 | `ALIGNED` | `OTHER/UNKNOWN` |
| 13 | `opp_9f301fee2e62...` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | `SI2_H3` | 3991.71 | 3992.53 | 3979.01 | -0.6018 | 1.8843 | -2.4861 | -0.9984 | `ALIGNED` | `OTHER/UNKNOWN` |
| 14 | `opp_9f301fee2e62...` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | `SI2_H3` | 3992.88 | 3994.00 | 3979.01 | -0.7260 | 1.7571 | -2.4831 | -1.0012 | `ALIGNED` | `OTHER/UNKNOWN` |
| 15 | `opp_9f301fee2e62...` | `SMC_M1_CONTINUATION` | `SHORT` | `SI2_H3` | 3993.91 | 3994.88 | 3979.01 | -0.6669 | 1.8215 | -2.4884 | -0.9994 | `ALIGNED` | `OTHER/UNKNOWN` |
