# AURUM DESK V17.7.1 — DEFECT REGISTER & REMEDIATION MATRIX
**Role:** Principal QA Architect & Senior Risk Systems Engineer  
**Date:** October 2026  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Version:** V17.7.1  

---

## 1. Classification Taxonomy

Every audited issue is classified according to the mandatory V17.7.1 defect taxonomy:
- `FIXED_WITH_TEST_EVIDENCE`: Defect fixed in code and confirmed by passing automated integration test.
- `IMPLEMENTED_NOT_VERIFIED`: Code change completed but pending empirical runtime evidence.
- `PRE_EXISTING_UNRELATED`: Identified legacy issue outside the current release scope.
- `REGRESSION_INTRODUCED`: Issue introduced by new changes (must be resolved before release).
- `ENVIRONMENT_BLOCKED`: Testing blocked by host OS/environment dependencies.
- `EMPIRICAL_UNPROVEN`: Algorithmic performance claim requiring long-term forward paper market data.

---

## 2. Defect Register

| Defect ID | Severity | File & Location | Root Cause | Actual Operational Effect | Remediation | Verification Evidence | Status |
|---|---|---|---|---|---|---|---|
| **DEF-01** | CRITICAL | `stop_placement_service.py` (`plan_structural_stop`) | Hard rejection if `final_dist > 4.0 * atr_m1` | Rejecting valid trades where structural anchor was > 4x ATR away, despite valid risk & Net R:R | Removed hard rejection; replaced with diagnostic assessment (`DISTANCE_NORMAL`, `DISTANCE_ELEVATED`, `DISTANCE_EXTREME`) | `SL-20`, `DSL-01`, `DSL-02`, `DSL-15` passing | **FIXED_WITH_TEST_EVIDENCE** |
| **DEF-02** | CRITICAL | `test_v17_7_adaptive_stop_loss.py` (`test_sl_50`) | Mock-only assertion: `LIVE_EXCHANGE_ORDERS_SENT = 0; assert ... == 0` | Gave false sense of safety without checking real outbound dispatch boundaries | Installed active spy interceptors on `httpx.Client.post` and `httpx.AsyncClient.post`, asserting `call_count == 0` | `SL-50`, `DSL-19` passing | **FIXED_WITH_TEST_EVIDENCE** |
| **DEF-03** | HIGH | `test_v17_7_adaptive_stop_loss.py` (`test_sl_36`..`41`) | Superficial tests asserting only `result is not None` or local dict math | Engine integration was not actually exercised | Bound tests to genuine service boundaries: `resolve_stop_anchor`, `build_sweep_stop_descriptor`, `DailyNYResearchService` | `SL-36` to `SL-41` passing | **FIXED_WITH_TEST_EVIDENCE** |
| **DEF-04** | HIGH | `test_v17_7_adaptive_stop_loss.py` (`test_sl_60`) | Simulated PAPER lifecycle without database persistence | Did not prove DB transaction durability, ARM frozen snapshot, or fill/close lifecycle | Implemented full SQLite lifecycle: Candidate -> ARMED -> `execute_fill` -> `execute_close` -> persisted evidence | `SL-60`, `DSL-16` passing | **FIXED_WITH_TEST_EVIDENCE** |
| **DEF-05** | HIGH | `models.py` / `test_sl_43`, `test_sl_55` | Test attempted to pass `bound_stop_decision` keyword to `PaperOrder` | `PaperOrder` has no such column; metadata is stored in `cost_snapshot` JSON column | Replaced `bound_stop_decision` with `cost_snapshot=json.dumps({"stop_placement": ...})` | `SL-43`, `SL-55`, `SL-60`, `DSL-16` passing | **FIXED_WITH_TEST_EVIDENCE** |
| **DEF-06** | MEDIUM | `cg01_engine.py` line 1413 | Fallback ATR set to hardcoded `1.0` when neither M1 nor M5 ATR available | Synthesized volatility context instead of failing closed | Set fallback ATR to `None`, ensuring `plan_structural_stop` returns `WAITING_VOLATILITY_EVIDENCE` | `SL-32`, `DSL-10` passing | **FIXED_WITH_TEST_EVIDENCE** |
| **DEF-07** | MEDIUM | `stop_placement_service.py` line 932 | Direct attribute access on `anchor.id` failed for legacy mock Anchor types | `AttributeError: 'Anchor' object has no attribute 'id'` broke V17.5 regression | Changed to safe attribute access: `getattr(anchor, 'id', getattr(anchor, 'anchor_id', None))` | `test_v17_5_model_integration.py` passing | **FIXED_WITH_TEST_EVIDENCE** |
| **DEF-08** | HIGH | `tools/run_v17_7_acceptance.py` | Unconditionally printed `ACCEPTED_FOR_PAPER_OPT_IN` without executing full regression matrix | Could mask release-blocking test failures | Completely refactored runner to parse real exit codes, exact test counts, and calculate verdict from verified gate results | `run_v17_7_acceptance.py` verified | **FIXED_WITH_TEST_EVIDENCE** |
| **DEF-09** | MEDIUM | `docs/V17_7_STOP_LOSS_CALIBRATION_REPORT.md` | Reported synthetic optimization metrics as proven empirical performance | Misleading performance claims (e.g. 41.2% -> 14.6% stop-out reduction) | Added explicit warning banner; marked metrics as `NOT_EMPIRICALLY_VERIFIED` | Document audit reconciled | **EMPIRICAL_UNPROVEN** |
