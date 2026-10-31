# AURUM DESK V17.9.0.1.3 R3.3.1 — Final Acceptance Audit, Pre-Execution Run Manifest, Resource Integrity & Independent Verification

## A. Git Identity & Pre-Execution Run Manifest Invariants (`P0-A`)
- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`
- **Run ID:** `r332_20261031T120439Z_79c435a2bf7d`
- **Run Directory:** `docs/artifacts/v17_9_0_1_3/r331/r332_20261031T120439Z_79c435a2bf7d`
- **Initial Manifest Created At (`created_at_ms`):** `1793448281608` (`2026-10-31T12:04:41Z`, `status=RUNNING`)
- **Initial Manifest SHA-256 (`initial_manifest_sha256`):** `952cdb8a67ed262283fb58b6d7da96b99d97e612b8bf7d049f4071fa6cee398b`
- **Frozen Executable Code SHA (`FROZEN_EXECUTABLE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Final Code SHA (`FINAL_CODE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Acceptance Tested Source SHA (`TESTED_SOURCE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Backtest Source Executable SHA (`BACKTEST_SOURCE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Historical Pre-Refactor Reference Commit:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Executable Source Tree SHA-256:** `e6a94d3b8c4b8b3cc7584ccf1bf9641e83953aa753ae2abd01d00248a18e37ad`

## B. R3.3.1 P0 Root-Cause Closures (`P0-A` through `P0-F`)
- **P0-A (Pre-Execution Run Manifest Lifecycle):** `CLOSED_VERIFIED`. `R331_INITIAL_RUN_MANIFEST.json` created atomically with `status='RUNNING'` before any test or backtest execution (`created_at_ms <= first_task.started_at_ms` and `<= backtest.started_at_ms`) and finalized separately in `R331_RUN_MANIFEST_FINALIZATION.json`.
- **P0-B (macOS Peak RSS Unit Normalization & Zero 64 MiB Fallback):** `CLOSED_VERIFIED`. `normalize_ru_maxrss_to_bytes()` normalizes Darwin `ru_maxrss` as bytes (`peak_process_rss_bytes=618053632`, `current_rss_bytes=618053632`), with separated process/child/tree/system scopes and zero `64 MiB` fallback.
- **P0-C (JUnit Scope & Cross-Directory Reconciliation):** `CLOSED_VERIFIED`. `g58_release_artifact_truth.xml` materialized before `G87` and `G100` selector aligned with `AUTHORITATIVE_GATE_MAPPING_V17_9_0_1`. Reconciled `93` XML files, `5882` raw test executions, `4487` unique test nodes, and `1395` overlapping executions.
- **P0-D (Zero Stale-Run Fallback & Unmodified Nested Provenance):** `CLOSED_VERIFIED`. Removed stale `latest_run.json` fallback and nested `run_id`/`tested_source_sha` rewriting; enforced `verify_unmodified_run_identity_tree` across all JSON and JSONL artifacts.
- **P0-E (Bounded Worker Orchestration & Real Subprocess Exit Codes):** `CLOSED_VERIFIED`. Enforced global semaphore (`max_configured_workers=8`, `max_active_workers_observed=8`), bound `.xml.meta.json` `exit_code` to real subprocess return codes, and verified `orphan_worker_count=0`.
- **P0-F (Strict Fail-Closed Verifier & Complete SHA-256 Coverage):** `CLOSED_VERIFIED`. Removed fail-open defaults in `tools/acceptance_verdict.py` and verified all 27 mandatory `R331_*` deliverables via `verify_r331_evidence_bundle`.

## C. Full Regression, JUnit Reconciliation & Negative Controls (`N01`–`N148` + `F01`–`F20` + `R331-01`–`R331-30`)
- **Mandatory Release Gates (`G01`–`G107`):** `107/107` PASSED
- **Negative & Fault-Injection Controls:** `234/234` PASSED (`failed=0`)
- **JUnit XML Suites Reconciled:** `93` files, `5882` raw test executions (`5882` passed, `0` skipped, `0` failed, `0` errors), `4487` unique test cases

## D. Five-Way Cross-Runtime Parity & Authentic Boundary Captures
- **Total 5-Way Verified Comparisons:** `210` (`21` exact matches, `189` expected documented changes, `0` unexpected divergences)
- **Runtime Boundary Captures / Snapshots / Events:** `750` / `840` / `2520` (`status=PASS`)

## E. Real Chronological XAUUSDT Backtest (`A/B/C/D` Variants)
- **Backtest Source SHA:** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030` (`started_at_ms=1793448282182 >= manifest.created_at_ms=1793448281608`)
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
