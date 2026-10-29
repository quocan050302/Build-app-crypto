# AURUM DESK V17.9.0 — EXACT-COMMIT ARTIFACT VERIFICATION REPORT

- **Release Version:** `V17.9.0`
- **Branch:** `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Executable Source Commit (`FINAL_CODE_SHA`):** `2285b3f93a6c3516118feeb204ebe39caf408fdf`
- **Acceptance Run ID:** `run_v17_9_0_1793258845763_29840`
- **Historical Backtest Run ID:** `v17_8_0_4_bt_1793258805`
- **Historical Dataset SHA-256:** `6bcac947a842db06b4edb11e3db8164421dfcd27a3c0b9d6e571bf07543c3fb5`
- **Artifact Publication Integrity Verdict:** `ALL_MANDATORY_ARTIFACTS_COMMITTED_AND_VERIFIED`

---

## 1. Two-Commit Exact-Commit Discipline (Section 19)

1. **Step 1 — Executable Source Commit (`FINAL_CODE_SHA`):**
   - Commit SHA: `2285b3f93a6c3516118feeb204ebe39caf408fdf`
   - All executable source files (`backend/`, `frontend/`, `tools/`, `.gitignore`, `.env.example`, `_dev_tools.py`, `run.ps1`, `run.sh`) were committed prior to running the historical backtest and 107-gate acceptance runner.
2. **Step 2 — Acceptance & Historical Backtest Execution Against `FINAL_CODE_SHA`:**
   - Historical backtest (`v17_8_0_4_bt_1793258805`) executed against `2285b3f93a6c3516118feeb204ebe39caf408fdf`.
   - 107-gate acceptance runner (`run_v17_9_0_1793258845763_29840`) executed against `2285b3f93a6c3516118feeb204ebe39caf408fdf`.
   - Source tree hash across all 873 tracked executable files remained unchanged (`d75827c858df5d388b92fe1b4ff4bb94a1ca2691bc3abcf2db96e26d27a7f6c6`) before and after execution.
3. **Step 3 — Clean Executable Diff Verification:**
   - `git diff 2285b3f93a6c3516118feeb204ebe39caf408fdf -- backend frontend tools .gitignore .env.example _dev_tools.py run.ps1 run.sh` verified zero diff lines (`0` bytes).

---

## 2. Run-Scoped Acceptance Artifact Bundle (`docs/artifacts/v17_9_0/run_v17_9_0_1793258845763_29840/`)

Verified by [`tools/artifact_verifier.py`](file:///d:/build%20app-crypto/aurum-desk/tools/artifact_verifier.py):

| Artifact Path | Purpose | Status |
|---|---|---|
| `acceptance_results.json` | Full 107-gate machine-readable results & 10-verdict bundle | `VERIFIED` |
| `artifact_manifest.json` | SHA-256 leaf manifest of all published artifacts | `VERIFIED` |
| `committed_source_manifest.json` | Per-file SHA-256 of all 873 executable source files at `FINAL_CODE_SHA` | `VERIFIED` |
| `junit_reconciliation.json` | Reconciled counts across 92 JUnit XML files (`5,592/5,602` passed, `0` failed, `0` mismatches) | `VERIFIED` |
| `runtime_safety_proof.json` | `MARKET_SHADOW` zero-execution & live-order HTTP spy proof | `VERIFIED` |
| `live_order_spy_report.json` | Outbound order spy audit (`live_orders_sent: 0`, negative control exercised) | `VERIFIED` |
| `resource_usage_report.json` | Disk budget guard preflight & worker temp directory cleanup audit (`temp_dirs_remaining: 0`) | `VERIFIED` |
| `execution_state.json` | Atomic checkpoint & resume state record tied to `2285b3f93a6c3516118feeb204ebe39caf408fdf` | `VERIFIED` |
| `parity/baseline_decisions.json` | Stage-1 production baseline decision snapshots across all 5 models (`LONG`/`SHORT`) | `VERIFIED` |
| `parity/shared_decisions.json` | `SharedStrategyEvaluator` decision snapshots across all 5 models (`LONG`/`SHORT`) | `VERIFIED` |
| `parity/shadow_decisions.json` | `MARKET_SHADOW` adapter decision snapshots across all 5 models (`LONG`/`SHORT`) | `VERIFIED` |
| `parity/backtest_decisions.json` | Historical backtest adapter decision snapshots across all 5 models (`LONG`/`SHORT`) | `VERIFIED` |
| `parity/parity_diff.json` | 4-way cross-adapter parity comparison (`10/10` matched, `0` mismatches) | `VERIFIED` |
| `junit/*.xml` | 92 gate JUnit XML files + `vitest.xml` + `playwright.xml` | `VERIFIED` |
| `logs/*.log` | Gate execution logs (`G01` through `G107`) | `VERIFIED` |
| `backtest/*` | Run-scoped copy of all 21 historical backtest artifacts | `VERIFIED` |

---

## 3. Top-Level Historical Backtest Bundle (`backtest/`)

All 21 historical backtest artifacts in `backtest/` are verified non-empty, schema-valid, and tied to `source_sha = 2285b3f93a6c3516118feeb204ebe39caf408fdf` and `dataset_sha256 = 6bcac947a842db06b4edb11e3db8164421dfcd27a3c0b9d6e571bf07543c3fb5`:
- `dataset_manifest.json`, `data_quality_report.json`, `baseline_A.json`, `structural_B.json`, `unicorn_C.json`, `noise_D.json`, `model_results.json`, `matched_sl_comparison.json`, `matched_comparison.json`, `full_strategy_comparison.json`, `trade_level_results.json`, `trade_ledger.csv`, `rejected_opportunities.json`, `equity_curves.json`, `drawdown_report.json`, `stop_out_forensics.json`, `out_of_sample_report.json`, `sensitivity_report.json`, `stress_test_report.json`, `backtest_summary.md`, `backtest_execution.log`.
