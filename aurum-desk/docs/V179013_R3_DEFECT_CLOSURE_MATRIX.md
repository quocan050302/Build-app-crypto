# AURUM DESK V17.9.0.1.3 R3 — DEFECT CLOSURE MATRIX

**Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`  
**Starting Commit SHA:** `03c12313ccf4d107692578f2aa3dda7716ad0f3d`  
**Pre-Refactor Reference Baseline SHA:** `5c5a0899f57fa964b4c4fae93f7da545ae49dd20`  

| Defect ID | Severity | Root Cause | Actual Code Repair | Verification Tests & Negative Controls | Final Status |
|---|---|---|---|---|---|
| `P0-01` | P0 | Backtest source manifest unconditionally hardcoded `"status": "PASS"` even when `backtest_source_sha != final_code_sha`. | Enforced strict conditional check: `"status": "PASS" if (bt_sha and code_sha and bt_sha == code_sha) else "FAIL"`. Re-ran chronological backtest directly on frozen executable commit. | `N116`, `N117`, `N136`, `N137` | `CLOSED_VERIFIED` |
| `P0-02` | P0 | Acceptance verifier read nested `b_auth.get("source_sha")` instead of top-level `backtest_metrics["backtest_source_sha"]`, failing open when empty. | Read top-level backtest source SHA, reconciled with nested `source_sha`, and failed closed if empty, null, or mismatched against `final_code_sha`. | `N132`, `N133`, `N134`, `N135`, `N144`, `N145` | `CLOSED_VERIFIED` |
| `P0-03` | P0 | Cross-runtime input capture mislabeled Historical Production as Market Shadow, and lacked authentic line-by-line runtime boundary capture records. | Relabeled capture point to `services.v17_9_0_1_baseline_oracle.IndependentPreRefactorBaselineOracle.evaluate_baseline_dict`. Emitted canonical `V179013_R3_RUNTIME_INPUT_CAPTURE.jsonl` and verified non-empty `V179013_R3_INDEPENDENT_RUNTIME_INPUT_HASHES.json`. | `N138`, `N139`, `N140` | `CLOSED_VERIFIED` |
| `P0-04` | P0 | Parity summary conflated 189 expected documented changes with exact matches, claiming 210 verbatim exact matches in release prose. | Separated exact matches (`21`), expected documented changes (`189`), and unexpected divergences (`0`). Published structured `V179013_R3_PARITY_CLASSIFICATION_REPORT.json` and aligned all markdown summaries. | `N141`, `N142` | `CLOSED_VERIFIED` |

---

## Detailed Closure Verification Notes

### P0-01: Backtest Source Manifest Strict Status
- **Source File:** `tools/generate_v17_9_0_1_artifacts.py`
- **Validation:** Tested against empty, mismatched, and matching SHAs. Any mismatch between `backtest_source_sha` and `final_code_sha` sets manifest status to `FAIL` and fails artifact verification.

### P0-02: Authoritative Top-Level Backtest Source SHA Verifier
- **Source File:** `tools/acceptance_verdict.py`
- **Validation:** Both top-level `backtest_metrics["backtest_source_sha"]` and nested `b_auth["source_sha"]` are inspected. If either is missing, contradictory, or divergent from `final_code_sha`, `VERDICT_STRATEGY_PERFORMANCE` is marked `FAIL`.

### P0-03: Authentic Runtime Input Provenance
- **Source Files:** `tools/generate_v17_9_0_1_artifacts.py`, `tools/acceptance_verdict.py`
- **Validation:** Every scenario generates authentic invocation events across all 5 independent runtime boundaries. The verifier checks that `HISTORICAL_PRODUCTION` is never tagged with Market Shadow, and all runtime hashes are fully populated.

### P0-04: Accurate Parity Accounting
- **Source Files:** `tools/generate_v17_9_0_1_artifacts.py`, `docs/v17_9_0_1_3/`
- **Validation:** 210 comparisons accurately classified as:
  - `EXACT_MATCH`: 21
  - `EXPECTED_DOCUMENTED_CHANGE`: 189
  - `UNEXPECTED_DIVERGENCE`: 0
- Status is verified `PASS` since unexpected divergences are zero and all 189 changes are documented improvements.
