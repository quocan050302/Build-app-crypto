# AURUM Desk V17.9.3.1.1 — Verification Integrity, OOS Leakage Repair & Loss Forensics Closure Report

## 1. Provenance & Git Identity
- **Working Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Starting V17.9.3.1.1 Feature HEAD SHA**: `86f13fafbbffb62528fe110458a83c129cefc20b`
- **Previous V17.9.3.1 Tested Code SHA**: `a00a17091bf7991855aaeb3f718242354c244a08`
- **Tested Executable Code SHA**: `e43c785a5bb916bc00b31b378be1130ab3308bc9`
- **Final Local HEAD SHA**: `e43c785a5bb916bc00b31b378be1130ab3308bc9`
- **Remote Origin HEAD SHA**: `e43c785a5bb916bc00b31b378be1130ab3308bc9`
- **Evidence Run ID**: `v17_9_3_1_1_20261101T154301Z_e43c785a5bb9`

## 2. Executive Dual Verdict
- **Engineering Verification Verdict**: `PASS`
- **Strategy Statistical Verdict**: `INCONCLUSIVE` (`A1`: `NEGATIVE_EVIDENCE`, `D1`: `INCONCLUSIVE`, `D2`: `INCONCLUSIVE`)
- **Ready for Strategy Activation (`LIVE` / `PAPER` / `AUTO`)**: **`NO`**

## 3. Phase P0 Verification Integrity Repairs
1. **Real Exit-Timestamp & 180-Bar Gap-Aware OOS Auditor (`P0-1`)**:
   - Detected `18` raw unpurged boundary-crossing episodes (`C2` at checkpoint `18928`, `E1` at `11126` and `18906`), purged/classified them as `EXCLUDED_BOUNDARY_LEAK_RISK`, and verified `measured_admissible_leak_count == 0` across `50/25/25`, `70/15/15`, and 3-fold expanding walk-forward splits.
2. **Canonical Risk Policy Snapshot (`P0-2`)**:
   - Enforced `capital_usdt = 1000.0`, `risk_pct_percent = 0.25` -> `max_planned_risk_usd = 2.50 USD` and `min_net_rr = 2.0` across all `543` execution ledger entries (`0` violations).
3. **Exhaustive 22,346 Candidate Coverage Audit (`P0-4`)**:
   - Audited all `18,558` outside-screen M1 timestamps plus `3,788` screened timestamps (`22,346 / 22,346 = 100%`) using `SharedStrategyEvaluator.evaluate_model_direction`: `0` false negatives for `A0, B0, C1, C1_SLTP, C2, C2_SLTP, D1, D2`; `47243` outside-screen decisions across `15597` timestamps for unfiltered `A1`/`E1` where M1 target `< $7.0` but V17.9.2 HTF target `>= $7.0`.

## 4. Historical Loss Rate & Root-Cause Forensics (`A1` vs `D1` vs `D2`)
- **`A1_V1792_SL_TP_BASELINE`**: `139` closed episodes (`7W / 132L`), loss rate `94.96%`, modeled net PnL `$-282.49`.
  - Primary root causes: `{"STRUCTURE_DIRECTION_CONFLICT_H1_H4": 69, "LIQUIDITY_SWEEP_MISSING_OR_STALE": 3, "PRE_ENTRY_MSS_BOS_UNCONFIRMED": 51, "MACRO_WINDOW_MISMATCH_OR_SESSION_RISK": 1, "PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD": 7, "ENTRY_TOO_LATE_AFTER_MOVE": 1}`
- **`D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP`**: `16` closed episodes (`1W / 15L`) + `4` open-at-horizon censored, loss rate `93.75%`, modeled net PnL `$-31.69`.
  - Primary root causes: `{"STRUCTURE_DIRECTION_CONFLICT_H1_H4": 6, "PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD": 3, "PRE_ENTRY_MSS_BOS_UNCONFIRMED": 6}`
- **`D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL`**: `2` closed episodes (`1W / 1L`), loss rate `50.00%` (95% Wilson CI `[9.45%, 90.55%]`). Severe small-sample caveat (`n=2` total, `n=1` in OOS which was a LOSS).

## 5. Full Regression & Resource Guard Summary
- **Collected Node IDs**: `4686`
- **Passed Tests**: `4686` (`0` failed, `0` skipped, `bijection_valid=True`)
- **Free Disk Space**: `23.79 GB`
