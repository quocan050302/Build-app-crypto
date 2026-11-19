# AURUM DESK — KH-01B.R1.DSC: DATA & SAFETY TRUTH CLOSURE (`KH-01B.R1.DSC-v1.0`)

- **Run ID**: `20261119T150709Z_0c247bdc466a`
- **Mandatory Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Starting HEAD (Prior KH-01B.R1 Phase C)**: `1ee4a8747e7a1205e64a5fbfacfde1da0aa72782`
- **Tested Executable Source SHA**: `0c247bdc466a76da2d17b2c9ccfd9a0431dcc73e`
- **Canonical Dataset SHA256 (`REUSED_RESEARCH`, `MODELED_BID_ASK`)**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`

## 1. Orthogonal Verdicts
- `ENGINEERING_CLOSURE_VERDICT`: **BLOCKED_LOW_DISK**
- `DISK_SAFETY_VERDICT`: **BLOCKED_LOW_DISK** (Required preflight: `>= 20.5 GiB` [`20.0 GiB` reserve + `0.5 GiB` stage]; Available: `17.1503 GiB`; Shortfall: `3.3497 GiB`; `lowered_reserve_without_approval = False`)
- `DATA_ACQUISITION_VERDICT`: **VERIFIED_PARTIAL**
- `FULL_REGRESSION_VERDICT`: **NOT_RUN_BLOCKED_LOW_DISK**
- `BACKTEST_VERDICT`: **NOT_RUN_BLOCKED_LOW_DISK**
- `WORKER_DETERMINISM_VERDICT`: **NOT_RUN_BLOCKED_LOW_DISK**
- `RESEARCH_ECONOMIC_VERDICT`: **NO_PROVEN_EDGE_BASELINE_UNCHANGED_FEE_SENSITIVITY_NEGATIVE**
- `KH01C_ADVANCEMENT_VERDICT`: **NO_ECONOMIC_ADVANCEMENT_CANDIDATE**
- `FRESH_OOS_VERDICT`: **BLOCKED_NO_UNTOUCHED_DATA**
- `ACTIVATION_READY`: **NO** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)

## 2. Repaired Data & Safety Defects Summary
1. **Gap Schema Mapping (`G-DS-02`)**: Fixed `kh_01b_r1_collect_market_data.py` to map `{'1m':'M1','5m':'M5','15m':'M15','1H':'H1','4H':'H4','1D':'D1'}` onto authoritative `gap_audit['timeframes']` (`status = PASS`). Null/missing schema now fails closed (`FAIL_INTEGRITY`).
2. **Window Coverage & Dynamic Page Budget (`G-DS-03`, `G-DS-04`)**: Removed silent `max_pages_per_tf=8` (`1600`-bar) M1 ceiling (48h M1 dynamic budget = `17` pages) and reclassified the historical `1600/2880` M1 capture as `VERIFIED_PARTIAL` (`PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION`, `1280` leading missing slots).
3. **Exact Decimal Cross-Page Boundary Dedupe (`G-DS-03`)**: Exact identical cross-page boundary bars dedupe once (`PASS_WITH_DEDUP_RECEIPT`, retaining both origin HTTP SHAs), while within-page duplicates, duplicate-page replays, conflicting OHLCV, and precision-colliding near-equals (`3700.50004` vs `3700.50001`) fail closed.
4. **Strict 20 GiB Disk Reserve (`G-DS-07`, `G-DS-08`)**: Removed automatic `10 GiB` reserve downgrade in `compute_kh01b_r1_disk_reserve_policy()`. Reconciled constants & docstrings.
5. **Decimal Fee Provenance & Sensitivity (`G-DS-05`, `G-DS-06`)**: Kept `CostAssumptions(taker_fee_rate=0.0004)` untouched (`gross = -142.7836 USD`, `fees = 203.1135 USD`, `net = -345.8971 USD`, `166` closed). Computed separate `0.0004` vs `0.0006` (`1x`, `2x`, `3x`) in `FEE_ONLY_COUNTERFACTUAL_FIXED_FILLS` (`0.0006` 1x fees = `304.6696 USD`, net = `-447.4532 USD`) and `FULL_RE_RISKED_REPLAY` (`0.0004` net = `-345.8971 USD` vs `0.0006` net = `-210.6112 USD`). Account VIP tier remains `UNKNOWN_NOT_LOCALLY_VERIFIED`.

## 3. Extended-DEV 48h Window Slot Coverage (`2026-11-16T00:00Z .. 2026-11-18T00:00Z`)
| Timeframe | Expected Closed Slots | Observed Unique Closed | Leading Missing | Internal Missing | Trailing Missing | Coverage Ratio | Coverage Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `1m` | `2880` | `1600` | `1280` | `0` | `0` | `0.555556` | `PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION` |
| `5m` | `576` | `576` | `0` | `0` | `0` | `1.0` | `VERIFIED_COMPLETE` |
| `15m` | `192` | `192` | `0` | `0` | `0` | `1.0` | `VERIFIED_COMPLETE` |
| `1H` | `48` | `48` | `0` | `0` | `0` | `1.0` | `VERIFIED_COMPLETE` |
| `4H` | `12` | `12` | `0` | `0` | `0` | `1.0` | `VERIFIED_COMPLETE` |
| `1D` | `1` | `1` | `0` | `0` | `0` | `1.0` | `VERIFIED_COMPLETE` |

## 4. Test Execution Scope & Fault Controls
- **Closure RED/GREEN Fault Suite (`12`)**: `22/22` PASS.
- **Focused Pure In-Memory Unit & Subprocess Verifier Suite**: `PASS` (`executed = 15`, `passed = 15`, `failed = 0`).
- **Full 5,500+ Repo Regression / Full Backtest / w1-w2-w4 Benchmark**: `NOT_RUN_BLOCKED_LOW_DISK` / `NOT_RUN_BLOCKED_LOW_DISK` / `NOT_RUN_BLOCKED_LOW_DISK`.
