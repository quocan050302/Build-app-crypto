# AURUM DESK — KH-01A.R1 FORENSICS TRUTH CLOSURE (`KH-01A.R1`)

- **Run ID**: `20261119T021545Z_c1ba40df7ed9`
- **Mandatory Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Audited Starting HEAD**: `4b8e24fc2c6a6bfbd6a45a3d1cff0e912afe85a7`
- **Prior KH-01A Tested SHA**: `a1fd9293e2d0ac098008264ec2a6355596baf415`
- **R1 Tested Executable SHA**: `c1ba40df7ed95c44b9d9cfa473b77a3682a8ef01`
- **Canonical Dataset SHA256**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`

## 1. Orthogonal Verdicts
- `ENGINEERING_VERDICT`: **PASS** (`12/12` mandatory R1 technical gates PASS)
- `FORENSICS_SEMANTIC_VERDICT`: **PASS**
- `REGRESSION_VERDICT`: **PASS** (`5486` collected == `5486` executed; `5478` passed, `8` skipped, `0` failed, `0` errors)
- `ACCOUNTING_VERDICT`: **PASS**
- `TRADING_PARITY_VERDICT`: **PASS** (`56710/56710` canonical A1 opportunities + `246/246` 3-adapter episodes matched with `0` mismatches)
- `D4_MATERIALIZATION_VERDICT`: **NO_VALID_SETUPS**
- `FRESH_OOS_VERDICT`: **BLOCKED_NO_UNTOUCHED_DATA**
- `STRATEGY_ECONOMIC_EDGE_VERDICT`: **NEGATIVE_NO_PROVEN_EDGE** (`SI0 NEXT_BAR_OPEN_BID_ASK`: `242` scheduled, `166` closed = `9`W + `157`L, net PnL `$-345.8971` USD, expectancy `-0.8474`R)
- `ACTIVATION_READY`: **NO** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)
- `PUBLICATION_VERDICT` (pre-push immutable bundle): **PENDING_REMOTE_PUSH** (post-push verified readback written to `aurum-desk/docs/artifacts/kh_01a_r1/publication_20261119T021545Z_c1ba40df7ed9.json`)

## 2. Core KH-01A.R1 Closures Verified
1. **ICT Macro Call-Site Bug Repaired (`D1`–`D7`)**:
   - Repaired `resolve_causal_macro_time_window("XAUUSDT", dec_ts)` -> `resolve_causal_macro_time_window(decision_timestamp_ms=dec_ts, symbol="XAUUSDT")`.
   - Prior contaminated sessions (`170/170` `INVALID`) -> `0/170` `INVALID` (`{'LONDON': 3, 'LONDON_OPEN': 1, 'NY_AM': 1, 'NY_PM': 2, 'OUTSIDE_MACRO': 128, 'WEEKEND_CLOSED': 35}`).
   - SI0 `157` losses: `7/157` (`4.46%`) `ELIGIBLE_IN_VERIFIED_WINDOW`, `150/157` (`95.54%`) `VALID_OUTSIDE_WINDOW`, `0/157` `UNKNOWN_OR_INVALID_MACRO_STATE`.
   - SI0 `9` wins: `0/9` (`0.0%`) `ELIGIBLE_IN_VERIFIED_WINDOW`, `9/9` (`100.0%`) `VALID_OUTSIDE_WINDOW`.
   - Schedule provenance explicitly disclosed as `RESEARCH_HYPOTHESIS_CONFIG` (`V17_9_3_NY_ICT_MACRO_SCHEDULE_V1`); Bitget holiday/maintenance calendar remains `CALENDAR_NOT_VERIFIED` / `UNKNOWN_UNVERIFIED`.
2. **Per-Field Source Provenance & Snapshot Honesty (`E1`–`E7`)**:
   - Full raw `MarketDecisionContext` persistence honestly marked `NOT_PROVABLE_FROM_AVAILABLE_ARTIFACTS`.
   - Point-in-time `A1_V1792_SL_TP_BASELINE` reconstruction matches persisted `decision_fingerprint` & `trade_action_fingerprint` on `166/166` SI0 rows (`NOT_COMPARABLE=4` delayed-entry SI8/SI9 rows, with `4/4` candidate-bar fingerprint match; `MISMATCH=None`).
   - Evidence completeness across 170 rows: `{'PARTIAL': 170}` (`unknown` features never converted to proven `False`).
3. **Anti-Fake-Pass Dynamic Receipts & Adversarial Suite (`F1`–`F6`)**:
   - `38/38` real executed fault injections PASS (`0` report-assertion padding), verified by separate-OS-process `kh_01a_r1_independent_verifier.py`.
4. **Bounded Async Determinism & Resource Safety (`H1`–`H8`)**:
   - Pytest subset `w=4/w=1` speedup: `2.905x` (best `2.905x`); `w=1, w=2, w=4, w=1_repeat` hashes identical across pytest, `170` forensic rows, and `242` replay episodes; `max_children_observed=4` (`<= 4`), `0` orphans.
