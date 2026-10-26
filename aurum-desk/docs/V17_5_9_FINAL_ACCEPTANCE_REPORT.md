# AURUM V17.5.9 — FINAL ACCEPTANCE REPORT
## PRODUCTION WIRING, STRUCTURAL SOURCE VERIFICATION, FINANCIAL PARITY & CLEAN COMMIT ACCEPTANCE

**TARGET RELEASE:** AURUM V17.5.9  
**REPOSITORY:** `quocan050302/Build-app-crypto`  
**APPLICATION ROOT:** `aurum-desk/`  
**DEVELOPMENT BRANCH:** `fix/v14-1-rr-flow`  
**ORIGINAL BASELINE SHA:** `a25a479d3aa8bd6784d02c699241ba8d7081ef9e`  
**TESTED SOURCE SHA:** `55ecbd7754872d8ef811f7cdc4c31196f5b61881`  
**SOURCE CLEAN:** YES (`git status --porcelain` empty at test execution)  
**FINAL VERDICT:** **ACCEPTED**  

---

### 1. Executive Summary

AURUM V17.5.9 represents the complete, production-verified repair of the execution pipeline, eliminating seven critical correctness gaps identified in V17.5.8:
- **D01 (Reference Provenance):** Independent source candle verification in `closed_bars`, swing extreme validation, rejection of ambiguous multi-candle matches and contradictory caller metadata.
- **D02 (Break Provenance):** Elimination of unverified caller-supplied break bar fallback; strict closed-candle proof and close-through enforcement.
- **D03 (ARM Production Wiring):** Active production invocation of `validate_arm_invariants()` directly in `_arm_paper_candidate()` before order persistence, with direction validation and safe numeric conversion.
- **D04 (Cost Authority):** Fail-closed resolution of ordered cost settings; invalid authoritative configuration immediately blocks ARM rather than falling back to lower-priority settings.
- **D05 (Financial Parity):** Multi-stage financial reconciler `assert_order_financial_parity()` verifying planned entry, SL, TP, quantity, risk budget, gross/net R:R, maker/taker fees, and slippage without defaulting absent fields.
- **D06 (Timestamp Validation):** Safe market timestamp parser preventing unhandled conversion exceptions (`ValueError`, `TypeError`) across watcher cycles.
- **D07 (Acceptance Provenance):** Authoritative acceptance runner `tools/run_v17_5_9_acceptance.py` enforcing clean git working trees, fresh JUnit report generation, dynamic test count collection, and SHA256 source fingerprinting.

All 15 technical gates passed cleanly with zero failures.

---

### 2. Defect Resolution Summary

| Defect | Root Cause Verified? | Production Patch? | Integration Test? | Result |
|---|---|---|---|---|
| **D01** Reference Provenance | YES — absence of source candle or ambiguous price match accepted | YES — `resolve_verified_reference_node()` hardened | YES — `test_v17_5_9_reference_source_authority.py` (10/10) | **FIXED** |
| **D02** Break Provenance | YES — `source_bar = supplied_bar` fallback allowed unverified bars | YES — removed fallback in `resolve_verified_break_candle()` | YES — `test_v17_5_9_break_source_authority.py` (12/12) | **FIXED** |
| **D03** ARM Invariant Wiring | YES — `validate_arm_invariants()` existed but was never invoked | YES — wired into `_arm_paper_candidate()` before DB mutation | YES — `test_v17_5_9_arm_production_wiring.py` (13/13) | **FIXED** |
| **D04** Cost Authority | YES — skipping invalid keys allowed silent fallback to lower priority | YES — `resolve_ordered_cost_setting()` fails closed | YES — `test_v17_5_9_cost_fail_closed.py` (10/10) | **FIXED** |
| **D05** Financial Parity | YES — only slippage was checked and defaulted to 0.10 | YES — expanded `assert_order_financial_parity()` | YES — `test_v17_5_9_financial_parity.py` (14/14) | **FIXED** |
| **D06** Timestamp Safety | YES — raw `int()` conversions caused unhandled exceptions | YES — `parse_valid_market_timestamp()` helper | YES — `test_b03`–`test_b06`, `test_r06`–`test_r07` | **FIXED** |
| **D07** Acceptance Provenance | YES — V17.5.8 accepted dirty trees and unverified commits | YES — `run_v17_5_9_acceptance.py` checks clean status | YES — `test_v17_5_9_acceptance_runner_self_test.py` (9/9) | **FIXED** |

---

### 3. Verification & Gate Execution Matrix

Acceptance runner executed against clean source revision `55ecbd7754872d8ef811f7cdc4c31196f5b61881`:

| Gate | Description | Test Count | Passed | Failed | Status | Duration |
|---|---|---|---|---|---|---|
| **Gate 0** | V17.5.9 Core Targeted Suites (D01–D06 Repairs + Runner Self-Tests) | 71 | 71 | 0 | **PASS** | 3.269s |
| **Gate 1** | V17.5.8 Baseline Suites | 34 | 34 | 0 | **PASS** | 1.331s |
| **Gate 2** | V17.5.7 Baseline Suites | 16 | 16 | 0 | **PASS** | 1.463s |
| **Gate 3** | V17.5.6 Targeted Suites | 37 | 37 | 0 | **PASS** | 1.421s |
| **Gate 4** | V17.5.5 Baseline Suites | 23 | 23 | 0 | **PASS** | 1.128s |
| **Gate 5** | V17.5.4 Target Suites | 16 | 16 | 0 | **PASS** | 1.223s |
| **Gate 6** | V17.5.3 Boundary Probes & Cycle Review | 17 | 17 | 0 | **PASS** | 1.469s |
| **Gate 7** | V17.5.2 Baseline Suites | 7 | 7 | 0 | **PASS** | 1.345s |
| **Gate 8** | V17.5.1 Feature Suites & Probes | 26 | 26 | 0 | **PASS** | 1.731s |
| **Gate 9** | Existing V17.5 Regression Suites | 31 | 31 | 0 | **PASS** | 1.327s |
| **Gate 10** | Prior Regressions (CG01, sessions, authority) | 35 | 35 | 0 | **PASS** | 3.362s |
| **Gate 11** | Frontend Vitest Unit Tests (48 files) | 501 | 501 | 0 | **PASS** | 4.285s |
| **Gate 12** | Frontend Production Build (`tsc -b && vite build`) | — | — | 0 | **PASS** | 6.879s |
| **Gate 13** | Frontend Oxlint / ESLint Linter | — | — | 0 | **PASS** | 0.339s |
| **Gate 14** | Playwright E2E Browser Verification (`v17_5_6_chart_evidence.e2e.ts`) | 6 | 6 | 0 | **PASS** | 12.593s |

**Total Backend Tests:** 313 / 313 Passed (100%)  
**Total Frontend Tests:** 501 / 501 Passed (100%)  
**Total Browser E2E Tests:** 6 / 6 Passed (100%)  
**Total Overall Tests:** 820 Passed across full release matrix  

---

### 4. Production Code Changes

1. **`backend/services/stop_placement_service.py`**:
   - Added pure helper `parse_valid_market_timestamp(value, *, field_name, allow_zero=False)` preventing unhandled exceptions.
   - Hardened `resolve_verified_reference_node()`: strict direction normalization, finite price checks, contradictory metadata rejection, ambiguous candle match rejection, and authoritative source candle/cache validation.
   - Hardened `resolve_verified_break_candle()`: removed unverified caller fallback, required source candle in `closed_bars`, verified closure causality, and enforced strict close-through.
   - Refined `resolve_episode_membership_from_structure()`: preserved reference metadata (`ref_price`, `ref_id`, `ref_occ`) across rejection paths.
   - Hardened `derive_protected_leg_episode()`: prioritizes break candle validation, preserves episode timestamps, and fails closed without permissive fallback.
2. **`backend/services/scalp_watcher_orchestrator.py`**:
   - Hardened `validate_arm_invariants()`: added direction authorization, safe numeric conversions, and positive quantity validation.
   - Added `resolve_ordered_cost_setting()`: enforces fail-closed semantics across prioritized configuration keys.
   - Adapted `get_system_config_cost_value()` as backward-compatible wrapper.
   - Updated `evaluate_cycle()`: detects cost configuration errors and records `policy_dict["cost_config_error"]` and `policy_dict["costs_valid"] = False`.
   - Wired `validate_arm_invariants()` and cost configuration integrity checks directly into `_arm_paper_candidate()` before order persistence.
3. **`backend/services/plan_financial_context_service.py`**:
   - Expanded `assert_order_financial_parity()`: multi-stage financial reconciler verifying planned entry, SL, TP, quantity, risk budget, gross/net R:R, maker/taker fee rates, and slippage; rejects missing mandatory fields without permissive defaults.
4. **`tools/run_v17_5_9_acceptance.py`**:
   - Authored authoritative 15-gate acceptance runner enforcing clean git checkout, dynamic test count collection, fresh JUnit timestamps, and SHA256 manifest hashing.

---

### 5. Trading Invariants & Safety Verification

- **XAUUSDT PAPER-only:** Fully preserved; zero live exchange API calls or live orders.
- **LONG / SHORT Geometry:** Verified via strict unit tests and Playwright canvas coordinate assertions (`slY > entryY > tpY` for LONG, `tpY > entryY > slY` for SHORT).
- **Effective Minimum Net R:R:** Enforced at calculator, model gate, and ARM invariant levels.
- **Frozen Cost Snapshot:** Armed orders freeze planned entry, SL, TP, fees, and slippage into `cost_snapshot`; post-ARM global settings changes do not affect order fills.
- **Duplicate ARM Protection:** Idempotency key and execution fingerprint prevent duplicate order creation across repeated cycles and concurrent sessions.
- **Daily Trade Cap:** Hard limit of max 3 daily fills strictly enforced by `validate_arm_invariants()` and entry guard.
- **One Position Rule:** Existing active position blocks new ARM attempts.
- **Causality & Source Candle Proof:** All structural reference and break decisions verified strictly against closed bars with timestamps `<= decision_cutoff_ms`.

---

### 6. Evidence Directory Structure

```text
aurum-desk/
  artifacts/
    v17_5_9/
      acceptance.json
      manifest.json
      junit/
        gate0_v17_5_9_core.xml
        gate1_v17_5_8_core.xml
        gate2_v17_5_7_core.xml
        gate3_v17_5_6_core.xml
        gate4_v17_5_5_core.xml
        gate5_v17_5_4.xml
        gate6_v17_5_3.xml
        gate7_v17_5_2.xml
        gate8_v17_5_1.xml
        gate9_v17_5.xml
        gate10_prior.xml
      logs/
        gate0_v17_5_9_core.log
        gate1_v17_5_8_core.log
        gate2_v17_5_7_core.log
        gate3_v17_5_6_core.log
        gate4_v17_5_5_core.log
        gate5_v17_5_4.log
        gate6_v17_5_3.log
        gate7_v17_5_2.log
        gate8_v17_5_1.log
        gate9_v17_5.log
        gate10_prior.log
        gate11_frontend_vitest.log
        gate12_frontend_build.log
        gate13_frontend_lint.log
        gate14_playwright_e2e.log
  docs/
    V17_5_9_DEFECT_REGISTER.md
    V17_5_9_IMPLEMENTATION_MAP.md
    V17_5_9_FINAL_ACCEPTANCE_REPORT.md
  tools/
    run_v17_5_9_acceptance.py
```

---

### 7. Final Verdict

**VERDICT: ACCEPTED**  
All mandatory technical gates have passed on a verified clean source revision (`55ecbd7754872d8ef811f7cdc4c31196f5b61881`), with complete test artifacts, manifest hashes, and logs committed.
