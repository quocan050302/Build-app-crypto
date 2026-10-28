# AURUM DESK V17.8 — DEFECT & REPAIR REGISTER

## 1. Overview
In accordance with Section 15 of the Master Execution Directive, this register documents all defects encountered during the implementation, testing, and regression cycles of V17.8, their root causes, and verified source-level repairs.

---

## 2. Logged Defects & Repairs

### Defect DEF-178-01: Duplicate Breaker Blocks on Consecutive Close Bars
- **Severity:** Medium
- **Affected Area:** `backend/services/unicorn_evidence_service.py` (`detect_breaker_blocks`)
- **Reproduction Steps:** When price broke beyond an Order Block and continued trending for multiple bars, `detect_breaker_blocks` created a new BreakerBlock for every subsequent candle, incorrectly advancing `confirmed_at_ms`.
- **Root Cause:** Missing initial-break authority filter.
- **Source Repair:** Added `broken_ob_ids` set to ensure only the first candle that closes past the OB proximal boundary converts the OB into an authoritative Breaker Block.
- **Regression Test:** `test_v17_8_unicorn_evidence.py::test_bullish_breaker_and_unicorn_detection`
- **Status:** RESOLVED & VERIFIED

### Defect DEF-178-02: `UnicornEvidence` Missing Keyword Arguments
- **Severity:** Low
- **Affected Area:** `backend/services/unicorn_evidence_types.py` (`UnicornEvidence`)
- **Reproduction Steps:** Initializing `UnicornEvidence` without optional fields raised `TypeError: missing required positional arguments`.
- **Root Cause:** Optional dataclass attributes lacked default `= None` or `= 0`.
- **Source Repair:** Added default values to `mss_or_bos_id`, `displacement_id`, `sweep_id`, `invalidation_source_id`, `retest_timestamp_ms`, `known_at_ms`, `decision_cutoff_ms`.
- **Regression Test:** `test_v17_8_structural_sl.py::test_unicorn_breaker_distal_invalidation_authority`
- **Status:** RESOLVED & VERIFIED

### Defect DEF-178-03: `CalculationResult` Attribute Access `risk_usdt`
- **Severity:** Medium
- **Affected Area:** `backend/services/unicorn_replay_service.py` & tests
- **Reproduction Steps:** Accessing `calc.risk_usdt` raised `AttributeError: 'CalculationResult' object has no attribute 'risk_usdt'`.
- **Root Cause:** `CalculationResult` in `domain_calculator.py` names the field `net_risk_usdt` and `budget_usdt`.
- **Source Repair:** Updated access to `getattr(calc, 'net_risk_usdt', getattr(calc, 'budget_usdt', 0.0))`.
- **Regression Test:** `test_v17_8_structural_sl.py::test_financial_risk_conservation_and_quantity_downscaling`
- **Status:** RESOLVED & VERIFIED

### Defect DEF-178-04: `EntryGuardRequest` Missing Required Arguments
- **Severity:** Low
- **Affected Area:** `backend/tests/test_v17_8_quote_noise_guard.py`
- **Reproduction Steps:** Instantiating `EntryGuardRequest` without `execution_mode` and `origin` raised `TypeError`.
- **Root Cause:** `execution_mode` and `origin` are mandatory positional arguments on `EntryGuardRequest`.
- **Source Repair:** Added `_make_req()` helper providing `execution_mode="AUTO"` and `origin="AUTO_STRATEGY"`.
- **Regression Test:** `test_v17_8_quote_noise_guard.py` (7 tests)
- **Status:** RESOLVED & VERIFIED

### Defect DEF-178-05: Missing Acceptance Runner Gate Compatibility for V17.8
- **Severity:** High
- **Affected Area:** `tools/run_v17_7_acceptance.py` / `tools/acceptance_verdict.py`
- **Reproduction Steps:** Running `run_v17_7_acceptance.py` on V17.8 branch failed Gate 01 (`expected_branch = feat/v17-7...`).
- **Root Cause:** The runner was hardcoded to V17.7 branch and gate manifest (28 gates).
- **Source Repair:** Created `tools/run_v17_8_acceptance.py` and updated `tools/acceptance_verdict.py` with `MANDATORY_GATES_V17_8` (42 gates) and branch `feat/v17-8-unicorn-structural-sl-tp-intelligence`.
- **Regression Test:** `backend/tests/test_v17_8_acceptance_runner.py` (6 tests)
- **Status:** RESOLVED & VERIFIED
