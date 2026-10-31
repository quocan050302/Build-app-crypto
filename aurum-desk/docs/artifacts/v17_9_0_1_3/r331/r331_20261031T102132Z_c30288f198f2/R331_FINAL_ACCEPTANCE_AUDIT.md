# AURUM DESK V17.9.0.1.3 R3.3.1 — Final Acceptance Audit, Pre-Execution Run Manifest, Resource Integrity & Independent Verification

## A. Git Identity & Pre-Execution Run Manifest Invariants (`P0-A`)
- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`
- **Run ID:** `r331_20261031T102132Z_c30288f198f2`
- **Run Directory:** `docs/artifacts/v17_9_0_1_3/r331/r331_20261031T102132Z_c30288f198f2`
- **Initial Manifest Created At (`created_at_ms`):** `1793442092793` (`2026-10-31T10:21:32Z`, `status=RUNNING`)
- **Initial Manifest SHA-256 (`initial_manifest_sha256`):** `aad21b6dd951f3f8431cc47561cbe3e88fdc3f07e2652877600d8b8600d1d607`
- **Frozen Executable Code SHA (`FROZEN_EXECUTABLE_SHA`):** `c30288f198f2cd636f334aa4d257ccadd3ca9e13`
- **Final Code SHA (`FINAL_CODE_SHA`):** `c30288f198f2cd636f334aa4d257ccadd3ca9e13`
- **Acceptance Tested Source SHA (`TESTED_SOURCE_SHA`):** `c30288f198f2cd636f334aa4d257ccadd3ca9e13`
- **Backtest Source Executable SHA (`BACKTEST_SOURCE_SHA`):** `c30288f198f2cd636f334aa4d257ccadd3ca9e13`
- **Historical Pre-Refactor Reference Commit:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Executable Source Tree SHA-256:** `242645af5ae6e8fffa429362ca4a20feaaa03e1716f9b6bd81442c1cddc43f93`

## B. R3.3.1 P0 Root-Cause Closures (`P0-A` through `P0-F`)
- **P0-A (Pre-Execution Run Manifest Lifecycle):** `CLOSED_VERIFIED`. `R331_INITIAL_RUN_MANIFEST.json` created atomically with `status='RUNNING'` before any test or backtest execution (`created_at_ms <= first_task.started_at_ms` and `<= backtest.started_at_ms`) and finalized separately in `R331_RUN_MANIFEST_FINALIZATION.json`.
- **P0-B (macOS Peak RSS Unit Normalization & Zero 64 MiB Fallback):** `CLOSED_VERIFIED`. `normalize_ru_maxrss_to_bytes()` normalizes Darwin `ru_maxrss` as bytes (`peak_process_rss_bytes=628228096`, `current_rss_bytes=628228096`), with separated process/child/tree/system scopes and zero `64 MiB` fallback.
- **P0-C (JUnit Scope & Cross-Directory Reconciliation):** `CLOSED_VERIFIED`. `g58_release_artifact_truth.xml` materialized before `G87` and `G100` selector aligned with `AUTHORITATIVE_GATE_MAPPING_V17_9_0_1`. Reconciled `93` XML files, `5846` raw test executions, `4451` unique test nodes, and `1395` overlapping executions.
- **P0-D (Zero Stale-Run Fallback & Unmodified Nested Provenance):** `CLOSED_VERIFIED`. Removed stale `latest_run.json` fallback and nested `run_id`/`tested_source_sha` rewriting; enforced `verify_unmodified_run_identity_tree` across all JSON and JSONL artifacts.
- **P0-E (Bounded Worker Orchestration & Real Subprocess Exit Codes):** `CLOSED_VERIFIED`. Enforced global semaphore (`max_configured_workers=8`, `max_active_workers_observed=8`), bound `.xml.meta.json` `exit_code` to real subprocess return codes, and verified `orphan_worker_count=0`.
- **P0-F (Strict Fail-Closed Verifier & Complete SHA-256 Coverage):** `CLOSED_VERIFIED`. Removed fail-open defaults in `tools/acceptance_verdict.py` and verified all 27 mandatory `R331_*` deliverables via `verify_r331_evidence_bundle`.

## C. Full Regression, JUnit Reconciliation & Negative Controls (`N01`–`N148` + `F01`–`F20` + `R331-01`–`R331-30`)
- **Mandatory Release Gates (`G01`–`G107`):** `107/107` PASSED
- **Negative & Fault-Injection Controls:** `198/198` PASSED (`failed=0`)
- **JUnit XML Suites Reconciled:** `93` files, `5846` raw test executions (`5846` passed, `0` skipped, `0` failed, `0` errors), `4451` unique test cases

## D. Five-Way Cross-Runtime Parity & Authentic Boundary Captures
- **Total 5-Way Verified Comparisons:** `210` (`21` exact matches, `189` expected documented changes, `0` unexpected divergences)
- **Runtime Boundary Captures / Snapshots / Events:** `750` / `840` / `2520` (`status=PASS`)

## E. Real Chronological XAUUSDT Backtest (`A/B/C/D` Variants)
- **Backtest Source SHA:** `c30288f198f2cd636f334aa4d257ccadd3ca9e13` (`started_at_ms=1793442094494 >= manifest.created_at_ms=1793442092793`)
- **Variant A (Baseline):** `41` trades, Net PnL = `$-69.6`, Expectancy = `-0.7032R`
- **Variant B:** `25` trades, Net PnL = `$-31.2265`, Expectancy = `-0.5179R`
- **Variant C (Authoritative Production-Parity):** `18` trades, Net PnL = `$-16.3506`, Expectancy = `-0.3796R`
- **Variant D:** `24` trades, Net PnL = `$-29.7228`, Expectancy = `-0.5114R`
- **Matched Cohort (`A` vs `C`):** `3` trades saved from SL by Variant C, Paired Delta Net PnL = `$12.5401`
- **Out-of-Sample Verdict:** `OUT_OF_SAMPLE_INCONCLUSIVE`

## F. Independent R3.3.1 Bundle Verification & Final Release Decision (`verify_r331_evidence_bundle`)
- **Independent Verifier Status:** `PASS` (`is_valid=True`, `violations=0`)
- **RELEASE_READINESS_VERDICT:** `VERIFIED_FOUNDATION_CLOSURE — READY_TO_PROCEED_TO_V17_9_1`
- **READY_FOR_V17_9_1:** `YES`
- **TRADING_AUTHORIZATION:** `MARKET_SHADOW_RESEARCH_ONLY` (`PAPER_AUTO_TRADING_NOT_AUTHORIZED`, `LIVE_TRADING_NOT_AUTHORIZED`)
