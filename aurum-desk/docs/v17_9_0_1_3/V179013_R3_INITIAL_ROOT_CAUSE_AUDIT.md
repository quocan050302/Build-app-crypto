# AURUM DESK V17.9.0.1.3 R3 — INITIAL AUDIT & ROOT CAUSE FORENSICS REPORT

**Audit Date:** 2026-10-31  
**Git Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`  
**Starting Commit SHA:** `03c12313ccf4d107692578f2aa3dda7716ad0f3d`  
**Auditor Roles:** Principal Quantitative Trading Systems Engineer, Senior Python Backend Engineer, Independent QA Auditor, Test Infrastructure Reliability Engineer  

---

## 1. Executive Summary

AURUM Desk V17.9.0.1.3 R2 previously achieved 107/107 mandatory release gates passed, 131/131 negative controls passed, 210 five-way verified comparisons, and 0 unexpected divergences across 5,839 JUnit test executions. However, rigorous independent QA code review revealed four critical release-blocking evidence integrity gaps:

1. **P0-01 — Backtest Source Mismatch Still Accepted:**  
   In `tools/generate_v17_9_0_1_artifacts.py`, `v179013_bt_source_manifest_json` hardcoded `"status": "PASS"` regardless of whether `backtest_source_sha == code_sha`. In R2, `FINAL_CODE_SHA` (`a499f069...`) differed from `BACKTEST_SOURCE_SHA` (`2a494323...`), yet the source manifest claimed PASS.

2. **P0-02 — Acceptance Verifier Reads Wrong SHA Field:**  
   In `tools/acceptance_verdict.py`, `derive_authoritative_acceptance_verdicts()` extracted `bt_source_sha = str(b_auth.get("source_sha") or "")` from nested `authoritative_production_parity_simulation`. Because the nested dict did not define `source_sha`, `bt_source_sha` became empty `""`. The inequality check `if bt_source_sha and ...` evaluated to False, causing the verifier to skip exact-source verification and fail open.

3. **P0-03 — Independent Runtime Input Provenance Still Reconstructed:**  
   In `tools/generate_v17_9_0_1_artifacts.py`, the Historical Production capture point was erroneously labeled as `services.market_shadow_service.MarketShadowService._evaluate_single_model_shadow`. Market Shadow is a passive forward observer, not Historical Production. Furthermore, runtime input capture lacked an authentic, line-by-line provenance audit trail.

4. **P0-04 — Parity Summary Contradicts Detailed Classification:**  
   In `V179013_R2_FINAL_ACCEPTANCE_AUDIT.md`, section C asserted `Exact Matches: 210`. However, the detailed classification breakdown in `HISTORICAL_VS_REFACTORED_SUMMARY.json` and `ACTUAL_RUNTIME_PARITY_REPORT.json` showed `EXACT_MATCH: 21` and `EXPECTED_DOCUMENTED_CHANGE: 189`. While unexpected divergences remained strictly 0, claiming 210 exact bitwise matches conflated documented refactoring improvements with verbatim identity.

---

## 2. Root Cause Analysis & Minimal Code Repairs

### 2.1 P0-01: Backtest Source Mismatch Still Accepted

- **Defect Location:** `tools/generate_v17_9_0_1_artifacts.py` lines 2849–2858.
- **Root Cause:** Manifest generation dictionary lacked a conditional validation check, unconditionally setting `"status": "PASS"`.
- **Minimal Code Repair:**
  ```python
  bt_sha_manifest = str(bt_metrics_payload.get("backtest_source_sha") or "").strip()
  manifest_pass = bool(bt_sha_manifest and code_sha and bt_sha_manifest == code_sha.strip())
  v179013_bt_source_manifest_json = {
      "release": "V17.9.0.1.3",
      "final_code_sha": code_sha,
      "executable_source_tree_hash": exec_tree_info["executable_source_tree_hash"],
      "dataset_sha256": ds_man.get("dataset_sha256"),
      "configuration_hash": str(ds_man.get("configuration_hash") or hist_replay.get("configuration_hash") or bt_metrics_payload.get("configuration_hash") or ""),
      "evaluator_version": EVALUATOR_VERSION,
      "backtest_source_sha": bt_sha_manifest,
      "status": "PASS" if manifest_pass else "FAIL",
  }
  ```
- **Verification:** Covered by negative tests `N116`, `N117`, `N136`, `N137`.

---

### 2.2 P0-02: Acceptance Verifier Reads Wrong SHA Field

- **Defect Location:** `tools/acceptance_verdict.py` lines 1950–1960.
- **Root Cause:** Verifier inspected only nested `b_auth.get("source_sha")` rather than the canonical top-level `backtest_metrics.get("backtest_source_sha")`, and allowed empty strings to bypass verification.
- **Minimal Code Repair:**
  ```python
  top_bt_sha = str(backtest_metrics.get("backtest_source_sha") or "").strip()
  nested_bt_sha = str(b_auth.get("source_sha") or "").strip()
  bt_source_sha = top_bt_sha or nested_bt_sha

  if not bt_source_sha:
      perf_ok = False
      perf_fail_reason = "Missing authoritative backtest source SHA in backtest metrics"
  elif not final_code_sha or not final_code_sha.strip():
      perf_ok = False
      perf_fail_reason = "Missing final code SHA for exact-source provenance validation"
  elif top_bt_sha and nested_bt_sha and top_bt_sha != nested_bt_sha:
      perf_ok = False
      perf_fail_reason = f"Contradictory backtest source SHAs: top={top_bt_sha} != nested={nested_bt_sha}"
  elif bt_source_sha.strip() != final_code_sha.strip():
      perf_ok = False
      perf_fail_reason = f"Backtest source SHA mismatch: {bt_source_sha} != {final_code_sha}"
  elif sim_trades <= 0:
      perf_ok = False
      perf_fail_reason = f"Zero simulated trades in backtest: {sim_trades}"
  else:
      perf_ok = True
      perf_fail_reason = None
  ```
- **Verification:** Covered by negative tests `N132`, `N133`, `N134`, `N135`, `N144`, `N145`.

---

### 2.3 P0-03: Independent Runtime Input Provenance Reconstructed

- **Defect Location:** `tools/generate_v17_9_0_1_artifacts.py` lines 835–901.
- **Root Cause:** Historical Production capture point misattributed to Market Shadow; missing persisted line-by-line runtime input capture log.
- **Minimal Code Repair:**
  1. Relabel Historical Production capture point to:
     `services.v17_9_0_1_baseline_oracle.IndependentPreRefactorBaselineOracle.evaluate_baseline_dict`.
  2. Implement structured runtime input capture across all 5 independent runtime boundaries (`HISTORICAL_PRODUCTION`, `SHARED_STRATEGY_EVALUATOR`, `PRODUCTION`, `MARKET_SHADOW`, `CHRONOLOGICAL_BACKTEST`).
  3. Emit canonical deliverable `V179013_R3_RUNTIME_INPUT_CAPTURE.jsonl`.
  4. Ensure `V179013_R3_INDEPENDENT_RUNTIME_INPUT_HASHES.json` is fully populated.
- **Verification:** Covered by negative tests `N138`, `N139`, `N140`.

---

### 2.4 P0-04: Parity Summary Contradicts Detailed Classification

- **Defect Location:** `tools/generate_v17_9_0_1_artifacts.py` audit generation templates.
- **Root Cause:** Summary string templates printed `Exact Matches: {five_way_verified_count}` instead of extracting distinct classification counts from `hist_vs_refactored_breakdown`.
- **Minimal Code Repair:**
  1. Extract exact metrics:
     - `exact_matches = 21`
     - `expected_documented_changes = 189`
     - `unexpected_divergences = 0`
     - `total_comparisons = 210`
  2. Publish structured `V179013_R3_PARITY_CLASSIFICATION_REPORT.json`.
  3. Update all release audit summaries to truthfully present the exact breakdown.
- **Verification:** Covered by negative tests `N141`, `N142`.

---

## 3. Conclusion & R3 Strategy

All four root causes are isolated and resolved with minimal, fail-closed modifications. R3 closure enforces the invariant that `FINAL_CODE_SHA == TESTED_SOURCE_SHA == BACKTEST_SOURCE_SHA`, executes real chronological backtesting on the frozen commit, and publishes all 18 verifiable deliverables without discrepancy.
