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

### Defect DEF-178-06: UnboundLocalError for `unicorn_ev` When `stop_policy` Is None
- **Severity:** Medium
- **Affected Area:** `backend/services/scalp_m1_model_service.py` (`_evaluate_fvg_continuation_model`)
- **Reproduction Steps:** When evaluating an FVG continuation candidate without a stop policy (`stop_policy is None`), `unicorn_ev` was not initialized before being passed to `apply_stop_placement_to_candidate`, raising `UnboundLocalError`.
- **Root Cause:** `unicorn_ev` variable was scoped only inside `if stop_policy is not None:`.
- **Source Repair:** Initialized `unicorn_ev = None` alongside `anchor_res = None` prior to the `if stop_policy is not None:` block.
- **Regression Test:** `backend/tests/test_v17_7_adaptive_stop_loss.py::test_sl_39_fvg_continuation_behavior` (G11)
- **Status:** RESOLVED & VERIFIED

---

## 3. V17.8.0.1 Independent Audit Findings & Runtime Closure (`DEF-178-07` to `DEF-178-15`)

All nine post-V17.8.0 audit findings (`DEF-178-07` through `DEF-178-15`) are documented with full source reproduction and verification in `docs/V17_8_0_1_RUNTIME_DEFECT_REPRODUCTION.md`:

- **DEF-178-07 (P0):** Unicorn Detector Interface Mismatch (`selected_fvg` canonical contract in `unicorn_evidence_service.py` & `scalp_m1_model_service.py`) — **RESOLVED & VERIFIED (`G43`)**
- **DEF-178-08 (P0):** V17.8 Feature Flags Persistence, Strict Validation, CAS, No-Op, Reload & Mode Separation (`stop_placement_policy_service.py`, `schemas.py`, `main.py`) — **RESOLVED & VERIFIED (`G44`)**
- **DEF-178-09 (P0):** Fill-Time Protective Distance & Stop-Breach Quote Guard (`scalp_watcher_orchestrator.py`, `order_plan_snapshot_service.py`, `entry_guard_service.py`, `execution_coordinator.py`) — **RESOLVED & VERIFIED (`G45`, `G46`)**
- **DEF-178-10 (P1):** Causal Unicorn Episode Binding, Pullback OB Ordering, Provenance IDs & Anti-Lookahead (`unicorn_evidence_types.py`, `unicorn_evidence_service.py`, `stop_placement_service.py`) — **RESOLVED & VERIFIED (`G47`, `G48`)**
- **DEF-178-11 (P1):** 12-Scenario End-to-End Runtime Integration Suite (`backend/tests/test_v17_8_runtime_integration.py`) — **RESOLVED & VERIFIED (`G49`)**
- **DEF-178-12 (P1):** Mounted `SLTPIntelligenceView.tsx` in `JournalTab.tsx` + Vitest & Playwright Chromium E2E (`frontend/tests/e2e/v17_8_0_1_sltp_intelligence.e2e.ts`) — **RESOLVED & VERIFIED (`G50`)**
- **DEF-178-13 (P1):** Honest Replay Comparison & Empirical P90 Calibration (`unicorn_replay_service.py`, `main.py`) — **RESOLVED & VERIFIED (`G52`)**
- **DEF-178-14 (P1):** Forensic Stop-Out Truth & Calculator Reconciliation (`docs/V17_8_0_1_FORENSIC_TRUTH_RECONCILIATION.md`) — **RESOLVED & VERIFIED (`G51`)**
- **DEF-178-15 (P0):** 52-Gate Fail-Closed Acceptance Runner (`tools/acceptance_verdict.py`, `tools/run_v17_8_acceptance.py`) — **RESOLVED & VERIFIED (`G01`–`G52`)**

