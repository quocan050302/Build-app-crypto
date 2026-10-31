# AURUM DESK V17.9.0.1.3 R3.3.2 — Final Acceptance Audit, Raw Regression Truth, Non-Masking Retries & Independent Verification

## A. Git Identity & Pre-Execution Run Manifest Invariants
- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`
- **Run ID:** `r332_20261031T120439Z_79c435a2bf7d`
- **Run Directory:** `docs/artifacts/v17_9_0_1_3/r332/r332_20261031T120439Z_79c435a2bf7d`
- **Initial Manifest Created At (`created_at_ms`):** `1793448280272` (`2026-10-31T12:04:40Z`, `status=RUNNING`)
- **Initial Manifest SHA-256 (`initial_manifest_sha256`):** `dc3c3c3cb531cbf74b317674b483d0c3453fcfd687b081b030ca5edd89702205`
- **Frozen Executable Code SHA (`FROZEN_EXECUTABLE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Final Code SHA (`FINAL_CODE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Acceptance Tested Source SHA (`TESTED_SOURCE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Backtest Source Executable SHA (`BACKTEST_SOURCE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Historical Pre-Refactor Reference Commit:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Executable Source Tree SHA-256:** `e6a94d3b8c4b8b3cc7584ccf1bf9641e83953aa753ae2abd01d00248a18e37ad`

## B. Master Primary Shard Attempts, Non-Masking Retries & Raw XML Retention
- **Master Subprocess Attempts Verified:** `20` primary attempts, `0` retry attempts
- **Primary Shards Failed / Retried:** `0` failed, `0` isolated retries (`clean_primary_acceptance_status=PASS`)
- **Raw Primary Shard XML Totals:** `{'tests': 4487, 'passed': 4487, 'failed': 0, 'errors': 0, 'skipped': 0}` across `10` retained raw primary shard XMLs (`reports/raw/primary/shard_0.xml`..`shard_9.xml`)
- **Unique Primary Testcase Nodes:** `4487` (`replacement_performed=False`, `primary_exit_codes_rewritten=False`, `raw_shard_xml_retained=True`)

## C. Full Regression, Honest Gate Projection Provenance & Negative Controls (`234/234`)
- **Mandatory Release Gates (`G01`–`G107`):** `107/107` PASSED
- **Negative & Fault-Injection Controls (`N01`–`N148` + `F01`–`F20` + `R331-01`–`R331-30` + `R332-01`–`R332-36`):** `234/234` PASSED (`failed=0`)
- **Gate XML Scope Reconciled:** `93` files, `5882` raw test executions (`5882` passed, `0` skipped, `0` failed, `0` errors), `4487` unique test cases

## D. Five-Way Cross-Runtime Parity & Authentic Boundary Captures
- **Total 5-Way Verified Comparisons:** `210` (`21` exact matches, `189` expected documented changes, `0` unexpected divergences)
- **Runtime Boundary Captures / Snapshots / Events:** `750` / `840` / `2520` (`status=PASS`)

## E. Real Chronological XAUUSDT Backtest (`A/B/C/D` Variants)
- **Backtest Source SHA:** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030` (`started_at_ms=1793448282182 >= manifest.created_at_ms=1793448280272`)
- **Variant A (Baseline):** `41` trades, Net PnL = `$-69.6`, Expectancy = `-0.7032R`
- **Variant B:** `25` trades, Net PnL = `$-31.2265`, Expectancy = `-0.5179R`
- **Variant C (Authoritative Production-Parity):** `18` trades, Net PnL = `$-16.3506`, Expectancy = `-0.3796R`
- **Variant D:** `24` trades, Net PnL = `$-29.7228`, Expectancy = `-0.5114R`
- **Matched Cohort (`A` vs `C`):** `3` trades saved from SL by Variant C, Paired Delta Net PnL = `$12.5401`
- **Out-of-Sample Verdict:** `OUT_OF_SAMPLE_INCONCLUSIVE`

## F. Independent R3.3.2 Bundle Verification & Final Release Decision (`verify_r332_evidence_bundle`)
- **Independent Verifier Status:** `PASS` (`is_valid=True`, `violations=0`)
- **RELEASE_READINESS_VERDICT:** `VERIFIED_FOUNDATION_CLOSURE`
- **READY_FOR_V17_9_1:** `YES`
- **TRADING_AUTHORIZATION:** `MARKET_SHADOW_RESEARCH_ONLY` (`PAPER_AUTO_TRADING_NOT_AUTHORIZED`, `LIVE_TRADING_NOT_AUTHORIZED`)
