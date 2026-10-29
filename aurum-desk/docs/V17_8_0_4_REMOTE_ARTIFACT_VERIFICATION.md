# AURUM DESK V17.8.0.4 — REMOTE ARTIFACT CLOSURE & `.gitignore` REPAIR (`DEF-17803-05`)

## 1. Executive Summary
- **Defect Closed:** `DEF-17803-05` (Missing Remote Acceptance Logs & Unverifiable Remote Bundle)
- **Final Code SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Acceptance Run ID:** `run_v17_8_0_4_1793248273004_25296`
- **Published Bundle Path:** `docs/artifacts/v17_8_0_4/run_v17_8_0_4_1793248273004_25296/`
- **All Gate Logs Present & Tracked:** `PASS` (`G76_REMOTE_ACCEPTANCE_LOG_COMPLETENESS`, `G88_EXACT_COMMIT_REMOTE_EVIDENCE_VERIFICATION`)

## 2. Root Cause of `DEF-17803-05`
`aurum-desk/.gitignore` contained a global `*.log` rule on line 24 without negation exceptions for `docs/artifacts/v17_8_0_3/**/*.log`, `docs/artifacts/v17_8_0_4/**/*.log`, or `backtest/**/*.log`. Consequently, `git add docs/artifacts/...` silently skipped all `logs/*.log` files in V17.8.0.3 while `artifact_manifest.json` listed 55 log files in `leaves`.

## 3. Repair & Fail-Closed Verification
1. **`.gitignore` Un-Ignore Rules:** Added explicit negation rules in `aurum-desk/.gitignore`:
   - `!docs/artifacts/v17_8_0_3/**/*.log`
   - `!docs/artifacts/v17_8_0_4/**/*.log`
   - `!backtest/**/*.log`
2. **`verify_acceptance_bundle()` & `verify_remote_acceptance_bundle()`:** Updated `tools/artifact_verifier.py` so any missing `log_path` or `report_path` or `git check-ignore` match fails closed with `ACCEPTANCE_EVIDENCE_INCOMPLETE`.
