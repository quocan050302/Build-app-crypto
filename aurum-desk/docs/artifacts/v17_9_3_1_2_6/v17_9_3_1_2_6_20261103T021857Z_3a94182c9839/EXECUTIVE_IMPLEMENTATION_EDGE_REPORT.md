# AURUM DESK / BITGET XAUUSDT — V17.9.3.1.2.6
## Causal Signal Proof & Trading Edge Diagnosis Executive Report
- **Tested Code SHA**: `3a94182c9839872f128c804e0791f03f723a01fb`
- **Mandatory Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Timestamp UTC**: `2026-11-03T02:41:10.326528+00:00`
- **Engineering Verification Verdict**: **PASS** (25/25 Acceptance Gates Passed)
- **Strategy Statistical Edge Verdict**: **NEGATIVE_EXPECTANCY**
- **Activation Readiness**: **NO** (Research modifications remain strictly OFF in Production)

---

### 1. Executive Summary & Core Defects Resolved

| Defect | Description | Executable Repair & Behavioral Impact | Status |
| :--- | :--- | :--- | :--- |
| **P0-A** | FVG Retest Fallback | Eliminated `fvg_retest_valid = bool(fvg_unmitigated)`. Retest requires true post-formation closed-bar touch within zone boundaries. | **RESOLVED** |
| **P0-B** | Fabricated Timestamps | Removed `source_known_at_ms = int(target_known_at_ms or cur_ts)`. Missing provenance recorded as `UNKNOWN/None`. | **RESOLVED** |
| **P0-C** | ID Presence Scoring | Removed ID-presence fallback in candidate scoring tuple. Missing/unproven features score 0 points. | **RESOLVED** |
| **P1-E** | Quote Provenance | Research quotes explicitly classified as `SYNTHETIC_RESEARCH_QUOTE`. Zero synthetic quotes labeled as real provider data. | **RESOLVED** |
| **P1-F** | Loss Forensics | Comprehensive loss ledger reconciling signed fees, gross/net PnL, separating observed facts from hypotheses. | **RESOLVED** |

---

### 2. Forensic Loss Ledger & Root Cause Breakdown
- **Total Scheduled Baseline Episodes**: `241`
- **Closed Trades Count**: `166` (`9` Wins, `157` Losses)
- **Open At Horizon**: `74`
- **Data Gap Unresolved**: `1`
- **Loss Reconciliation**: Sum of mutually exclusive primary causes equals total losses: `True`

---

### 3. Acceptance Gates G01..G25 Summary

| Gate | Title | Result |
| :--- | :--- | :--- |
| **G01** | Mandatory branch feature/v17-9-2-structural-sl-tp-from-qa, ancestry verified, zero prior artifact files altered | **PASS** |
| **G02** | Frozen Phase A tested executable SHA matches across preflight, fresh pytest collection, JUnit shards, and backtest | **PASS** |
| **G03** | Canonical historical dataset SHA-256 matches exactly with verified closed MTF chronology and gap validation | **PASS** |
| **G04** | Full-universe causal candidate discovery evaluated across all post-warmup M1 bars without sample-index shortcut | **PASS** |
| **G05** | Per-record discovered feature ledger derived strictly from source evidence with zero fabricated truth | **PASS** |
| **G06** | Zero fallback to decision timestamp for missing provenance (unknown is recorded as UNKNOWN/None) and no future-known evidence | **PASS** |
| **G07** | FVG unmitigated-but-never-retested is verified REJECTED/UNPROVEN (unmitigated != retested) | **PASS** |
| **G08** | True postformation price-zone retest remains verified with precise closed-bar IDs and valid price touch | **PASS** |
| **G09** | Descriptor spoof, touch-before-formation, future bar, and inverted zone negatives all fail closed | **PASS** |
| **G10** | Breaker + FVG spatial overlap and strict chronological sequence verified with FVG_ONLY separation | **PASS** |
| **G11** | Pre-entry candidate scoring uses only verified Boolean proof flags with zero ID-presence fallbacks and zero post-entry outcome leakage | **PASS** |
| **G12** | Feature-OFF baseline parity (166 closed, 9W/157L, -$345.90, 74 OPEN_AT_HORIZON) and OBSERVE_ONLY 100% action parity exact | **PASS** |
| **G13** | SI7, SI8, and SI9 research policies produce traceable before/after decision changes and distinct order intents | **PASS** |
| **G14** | Quote provenance explicitly classified as SYNTHETIC_RESEARCH_QUOTE across all fill modes and episodes | **PASS** |
| **G15** | Protected structural SL anchor, unswept liquidity TP, cost-aware net RR >= 2.0, and <= $2.50 USD risk cap invariants hold | **PASS** |
| **G16** | Full per-trade forensic loss ledger exhaustive with zero duplicate opportunity IDs and signed fees/gross/net accounting reconciled | **PASS** |
| **G17** | Matched A/B baseline vs repaired policy comparisons evaluated with identical timeline and honest denominators | **PASS** |
| **G18** | Three distinct fill modes (NEXT_BAR_OPEN_BID_ASK, LEGACY_CLOSE_FILL, STRESS_WIDER_SPREAD_SLIPPAGE_2X) replayed with spread, fees, and slippage | **PASS** |
| **G19** | Full fresh pytest collection matches freshly executed JUnit testcases (0 reused shards, 0 failed, 0 error, exact bijection) | **PASS** |
| **G20** | Executable RED/GREEN source mutations (>= 16) fail with non-zero exit on injected defect and pass on restored code | **PASS** |
| **G21** | Independent 1, 2, and 4 OS-process worker runs produce identical SHA-256 digests across all required policies and fill modes | **PASS** |
| **G22** | Host resource guards verified: free disk space remains above 5.0 GiB safety floor with bounded OS worker budget (<= 4 processes) | **PASS** |
| **G23** | Walk-forward purge/embargo verified and fresh OOS status disclosed honestly as REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS | **PASS** |
| **G24** | Strategy statistical verdict evaluates all registered policies SI1..SI9 dynamically, accurately returns net USD, keeps ACTIVATION_READY == NO | **PASS** |
| **G25** | Three-phase Git commit & push receipt verified on mandatory branch feature/v17-9-2-structural-sl-tp-from-qa | **PASS** |

---

### 4. Benchmark Timings & Execution Summary
- **concurrent_junit_bt_mut_seconds**: `1329.97s`
- **total_verification_pipeline_seconds**: `1332.90s`