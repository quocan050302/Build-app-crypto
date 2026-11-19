# AURUM DESK — KH-01B.R1: ENTRY EDGE DISCOVERY, BASELINE THESIS AUDIT & PROSPECTIVE DATA READINESS (`KH-01B.R1-v1.0`)

- **Run ID**: `20261119T115223Z_3661c2eeb562`
- **Mandatory Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Audited Starting HEAD**: `fe19e6b5b4b08425553ce5d2431b3a8c4aac4198`
- **Tested Executable SHA (Phase A)**: `3661c2eeb562d326c3065bd71c89df7878203736`
- **Frozen R1 Manifest SHA256**: `d5605ccec9b86394af403684fbe3141b8b771ff2e2402eec2aa7f343c102cd15`
- **Canonical Dataset SHA256 (`REUSED_RESEARCH`, `MODELED_BID_ASK`)**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`

## 1. Orthogonal Verdicts
- `ENGINEERING_VERDICT`: **PASS** (`16/16` mandatory gates PASS)
- `BASELINE_PARITY_VERDICT`: **PASS** (`56710/56710` A1 + `246` 3-adapter episodes matched with `0` mismatches)
- `RESEARCH_HYPOTHESIS_VERDICT`: **NO_DEFENSIBLE_EDGE**
- `FRESH_OOS_VERDICT`: **BLOCKED_NO_UNTOUCHED_DATA** (`strategy_evaluation_count = 0`)
- `DATA_ACQUISITION_VERDICT`: **PASS_VERIFIED_WITH_ORIGIN_RECEIPTS**
- `KH01C_ADVANCEMENT_VERDICT`: **NO_ECONOMIC_ADVANCEMENT_CANDIDATE** (Qualified: `[]`)
- `ACTIVATION_READY`: **NO** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)
- `PUBLICATION_VERDICT` (pre-push bundle): **PENDING_REMOTE_PUSH**

## 2. Primary Thesis Audit (`SI0` / `E00` Baseline Decomposition)
- **Gross vs Fee Truth**: `SI0` closed `166` trades (`9`W / `157`L) with **Gross PnL = `-142.7836` USD** (`-0.3497`R **already negative BEFORE fees**), **Fees = `203.1135` USD** (`0.4975`R drag), **Net PnL = `-345.8971` USD** (`-0.8474`R).
- **Strict D4 (`B09`) vs Adjacent (`B10`) Isolation**: Historical D4 closed = `0` (`expectancy = None` / `NO_VALID_SETUPS`); Adjacent eligible = `6874` (`counts_toward_d4 = 0`); Detector unit fixtures = `25/25` PASS (`0` synthetic trades in PnL).

## 3. Pre-Registered Bounded Research Matrix (`E00`–`E06` + Predeclared Sub-Contrasts)
| Mã | Variant ID | Eligible / 56,710 | Sched / Closed (NB) | Gross 1x (USD) | Fees 1x (USD) | Net 1x (USD) | Exp 1x (R) | Net 2x All-Cost | Net 3x All-Cost | 95% Cluster CI (R) | Kết luận |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `E00` | `KH01B_R1-E00-UNCHANGED-B00-CONTROL` | `56710` | `242 / 166` | `-142.7836` | `203.1135` | `-345.8971` | `-0.8474` | `-594.6226` | `-803.8121` | `[-0.9625, -0.7073]` | `NO_CANDIDATE` |
| `E01` | `KH01B_R1-E01-MODEL-FAMILY-ISOLATION` | `17898` | `237 / 161` | `-164.0692` | `195.9198` | `-359.989` | `-0.9092` | `-585.0327` | `-802.2358` | `[-1.0051, -0.8031]` | `NO_CANDIDATE` |
| `E01_C1` | `KH01B_R1-E01_C1-MODEL-REVERSAL-ONLY` | `8879` | `233 / 158` | `-132.6679` | `197.9669` | `-330.6348` | `-0.8502` | `-567.2354` | `-793.0758` | `[-0.9755, -0.7029]` | `NO_CANDIDATE` |
| `E01_C2` | `KH01B_R1-E01_C2-MODEL-CONTINUATION-ONLY` | `4044` | `184 / 119` | `-140.1599` | `135.9768` | `-276.1367` | `-0.9425` | `-438.6173` | `-572.9043` | `[-1.0635, -0.8241]` | `NO_CANDIDATE` |
| `E01_C3` | `KH01B_R1-E01_C3-MODEL-FVG-CONTINUATION-ONLY` | `17898` | `237 / 161` | `-164.0692` | `195.9198` | `-359.989` | `-0.9092` | `-585.0327` | `-802.2358` | `[-1.0051, -0.8031]` | `NO_CANDIDATE` |
| `E02` | `KH01B_R1-E02-DIRECTIONAL-MISMATCH` | `34265` | `184 / 132` | `-95.1727` | `164.8751` | `-260.0478` | `-0.8017` | `-455.4541` | `-634.676` | `[-0.9753, -0.5895]` | `NO_CANDIDATE` |
| `E02_C1` | `KH01B_R1-E02_C1-LONG-ONLY` | `22445` | `116 / 72` | `-74.5082` | `94.1068` | `-168.615` | `-0.9507` | `-298.4259` | `-373.9726` | `[-1.0002, -0.8459]` | `NO_CANDIDATE` |
| `E02_C2` | `KH01B_R1-E02_C2-SHORT-ONLY` | `34265` | `184 / 132` | `-95.1727` | `164.8751` | `-260.0478` | `-0.8017` | `-455.4541` | `-634.676` | `[-0.9753, -0.5895]` | `NO_CANDIDATE` |
| `E03` | `KH01B_R1-E03-CAUSAL-REGIME-MATCHING` | `22875` | `144 / 103` | `-103.9203` | `116.9169` | `-220.8372` | `-0.8711` | `-356.0219` | `-490.919` | `[-0.9666, -0.7663]` | `NO_CANDIDATE` |
| `E03_CHOP` | `KH01B_R1-E03_CHOP-COMPLEMENT-DIAGNOSTIC` | `33835` | `173 / 115` | `-129.3328` | `144.8589` | `-274.1917` | `-0.9674` | `-465.6778` | `-590.5435` | `[-1.0901, -0.8497]` | `NO_CANDIDATE` |
| `E04` | `KH01B_R1-E04-PREENTRY-GAP-QUOTE-QUALITY-COHORT` | `24126` | `129 / 93` | `-91.5853` | `101.3859` | `-192.9712` | `-0.8452` | `-306.873` | `-414.077` | `[-1.0146, -0.6782]` | `NO_CANDIDATE` |
| `E05` | `KH01B_R1-E05-CAUSAL-DELAYED-CONFIRMATION-TRIGGER` | `27809` | `200 / 127` | `-135.9067` | `147.1265` | `-283.0332` | `-0.9043` | `-448.9364` | `-604.0242` | `[-1.0396, -0.7376]` | `NO_CANDIDATE` |
| `E06` | `KH01B_R1-E06-REGIME-X-FVG-CONTINUATION-INTERACTION` | `7054` | `137 / 96` | `-97.0075` | `106.5182` | `-203.5257` | `-0.8621` | `-327.0623` | `-450.7493` | `[-0.9637, -0.7503]` | `NO_CANDIDATE` |

## 4. Regression, Adversarial Controls, Async Speed & Disk Telemetry
- **Full Pytest Regression**: `5504` collected == `5504` executed (`5502` passed, `2` skipped, `0` failed, `0` errors).
- **Real Fault Injections**: `24/24` RED/GREEN mutations PASS.
- **Async Determinism & Speedup**: Pytest `w=4` vs `w=1` speedup = `2.038x` (`MEANINGFUL_PARALLEL_SPEEDUP`); `w=1, w=2, w=4, w=1_repeat` canonical hashes identical = `True` (pytest) & `True` (R1 replay slice).
- **Resource & Disk Safety**: `max_children_observed = 4` (`<= 4`), `preflight_baseline_free_gib = 13.4667` (`baseline_already_below_20gib = True`), `min_free_disk_gib = 12.102`, `orphan_pids = 0`.
