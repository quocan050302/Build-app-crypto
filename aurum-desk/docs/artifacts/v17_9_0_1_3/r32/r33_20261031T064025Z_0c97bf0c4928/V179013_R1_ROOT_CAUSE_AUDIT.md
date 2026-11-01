# V17.9.0.1.3 Root Cause Audit (`DEF-179013-R01` – `DEF-179013-R08`)

- **Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`
- **Tested Code SHA:** `0c97bf0c492831480f23f581c5cd205a6a7b4094`
- **Historical Pre-Refactor Reference SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`

## Defect Root Causes and Verifications

### DEF-179013-R01: Hardcoded PASS & Fake Readiness Statements
- **Root Cause:** Acceptance reports previously contained static template strings for release verdicts regardless of underlying test results.
- **Repair:** Implemented `derive_authoritative_acceptance_verdicts` in `tools/acceptance_verdict.py` and fail-closed disk verifier `verify_persisted_acceptance_artifacts`.
- **Verification:** Tested by negative controls `N94` and `N95` verifying that any corrupted artifact or checksum mismatch fails closed.

### DEF-179013-R02: Five-Way Parity Count Contradiction
- **Root Cause:** `compute_cross_runtime_parity_report` keyed `five_way_compared_count` but `V179013_FIVE_WAY_PARITY_REPORT.json` looked for `five_way_verified_count`, falling back to 0 while `comparison_accounting['five_way_verified']` was 210.
- **Repair:** Aligned top-level dictionary key `five_way_verified_count: 210` in `compute_cross_runtime_parity_report` and verified exact match with internal accounting.
- **Verification:** Tested by `N78` and `N79` ensuring missing sources or duplicate source masquerading fails closed.

### DEF-179013-R03: Canonical Hash Copy Across Runtimes
- **Root Cause:** Historical raw hashes were previously overwritten with the shared evaluator's raw hash string.
- **Repair:** Preserved raw untouched hashes (`raw_input_hash`, `raw_configuration_hash`) across all runtimes; computed independent canonical semantic hashes per runtime; validated equivalence via `validate_comparison_record_hashes`.
- **Verification:** Tested by `N75`, `N76`, and `N77` ensuring raw hash preservation and detection of hash collisions.

### DEF-179013-R04: Historical Production Lineage Disconnect
- **Root Cause:** Historical baseline oracle previously read decision output from `raw_shadow` rather than authentic orchestrator downstream candidate consumption.
- **Repair:** Connected historical baseline oracle directly to historical `ScalpWatcherOrchestrator.evaluate_cycle` execution; recorded `OpportunityFunnelRecord` stages; emitted full consumed lifecycle (`ORDER_INTENT_CREATED`, `CANDIDATE_CONSUMED`, `ORDER_BLOCKED`, `EVALUATED_NOT_CONSUMED`, `REJECTED`, `NOT_EXECUTED`).
- **Verification:** Tested by `N71`, `N72`, `N73`, `N80`, and `N82`.

### DEF-179013-R05: Toy Asserts in Negative Controls Suite
- **Root Cause:** Negative controls `N71`–`N95` previously had placeholder asserts (`assert True is False`).
- **Repair:** Replaced all 25 placeholder controls with real fault injections invoking actual production validators.
- **Verification:** Verified all 95 negative controls (`N01`–`N95`) passing against actual codebase.

### DEF-179013-R06: Asynchronous Runner Speedup Contradiction
- **Root Cause:** Shard regression recorded 500.7s wall clock, but performance report claimed unverified 4.05x speedup.
- **Repair:** Recorded genuine 10-shard wall clock (500.7s) and explicitly designated speedup as `SPEEDUP_NOT_MEASURED`.
- **Verification:** Tested by `N91`, `N92`, and `N93`.

### DEF-179013-R07: Hardcoded Test Numbers vs Actual JUnit Artifacts
- **Root Cause:** Static numbers (5,771) were quoted in report templates rather than dynamically parsing reports.
- **Repair:** Built dynamic aggregator `aggregate_junit_xml_reports()` reading all 93 JUnit XML reports on disk.
- **Verification:** Verified dynamic counts (5,839 test executions, 4,348 unique test cases, 5,829 passed, 0 failures, 10 skipped).

### DEF-179013-R08: Fallback Dummy Config Hash and Missing Fail-Closed Verification
- **Root Cause:** Fallback `'c' * 64` was used when configuration hash was absent.
- **Repair:** Removed dummy fallback; enforced fail-closed validation rejecting non-derived or dummy config hashes.
- **Verification:** Tested by `N88` and `N89`.
