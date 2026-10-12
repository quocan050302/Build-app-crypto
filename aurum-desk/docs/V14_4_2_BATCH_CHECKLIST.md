# V14.4.2 Batch Progress Checklist

**Baseline:** 3d850b2 (fix/v14-1-rr-flow)
**Date:** 2026-10-12 Asia/Bangkok

## Stages Overview
- [x] **Stage A: Baseline Verification & Problem Reproduction**
  - [x] Verified clean working tree at `3d850b2`.
  - [x] Confirmed backend tests (41/41 passing).
  - [x] Confirmed frontend vitest (226/226 passing).
  - [x] Confirmed frontend build succeeds.
  - [x] Identified root causes for Ảnh 1 (15M SHORT TP inversion & fake default risk/qty) and Ảnh 2 (draft mode leak on WAITING_BREAK & HTTP 500 error).

- [x] **Stage B: Provisional Target Service & Resolved Display Plan (PHẦN 012 - 018, Appendix A)**
  - [x] Implement `backend/services/provisional_plan_service.py` (`build_provisional_plan`, `ProvisionalPlanResult`).
  - [x] Implement liquidity candidate adapter (`adapt_legacy_liquidity_candidates` / unconsumed causal target selection).
  - [x] Connect `build_provisional_plan` into `backend/services/strategy_service.py` (`evaluate_upcoming_setups`).
  - [x] Ensure non-READY stages (e.g. WAITING_PRICE) update `plan_status="INVALID_GEOMETRY"` when invalid.
  - [x] Add `resolved_display_plan` to `get_upcoming_setups` API.
  - [x] Update frontend `App.tsx` & `planLevels.ts` to consume `resolved_display_plan` and eliminate fake defaults (`|| 0.05`, `|| 2.5`).

- [x] **Stage C: Chart Selection Ownership, Draft Isolation, & HTTP 500 Root Cause (PHẦN 019 - 025, 027, Appendix D, F)**
  - [x] Fix HTTP 500 root cause in `backend/main.py` & `chart_evidence_service.py` for WAITING_BREAK / malformed / missing nodes.
  - [x] Fix draft mode lifecycle in `ChartComponent.tsx` (reset on selection key / binding change, not just `activeOverlay` ref).
  - [x] Enforce single owner: selecting a scenario with no plan renders no R:R overlay.
  - [x] Clean error handling in `useSelectedChart.ts`: clear stale bundle/overlay on selection or 500 error.
  - [x] Mount `ChartEvidenceList` via `createPortal(..., document.body)` so modal dialog escapes parent stacking context.

- [x] **Stage D: Realtime Race, Instance Reset, & Hook Integration (PHẦN 030 - 046, Appendix B)**
  - [x] Add `eventEpoch` to `useSMCScenarios.ts` to reject stale in-flight GET responses after newer WS events.
  - [x] In `applyScenarioUpdate`, check `instance_id` change before comparing revision (`new_instance rev 1` after `old_instance rev 10`).
  - [x] Fix StrictMode replay and debounce handling in `useSMCScenarios.ts`.

- [x] **Stage E: Observation Savepoint Transaction & Provenance Metadata (PHẦN 047 - 066, Appendix C)**
  - [x] In `backend/services/smc_observation_service.py`, wrap insert in `with db.begin_nested():` so SQL errors (triggers, constraints) rollback nested savepoint without poisoning parent session.
  - [x] In `backend/services/chart_evidence_service.py`, set `is_current: None` (UNKNOWN) when metadata is missing/partial, rather than defaulting to `True`.

- [x] **Stage F: Evidence Focus & Layout Collisions (PHẦN 067 - 086)**
  - [x] Verify `flattenEvidenceBundle`, `buildEvidenceAnchorIndex`, `computeEvidenceFocusRange`.
  - [x] Ensure reserved rectangles use accurate renderer measurements and update across view lifecycles.

- [x] **Stage G: Comprehensive Test Suites & Fail-Closed Acceptance Runner (PHẦN 026, 028, 116 - 120, 137 - 160, Appendix H)**
  - [x] Implement `backend/tests/test_v14_4_2_provisional_plan_regression.py` (T01–T08, T17–T20).
  - [x] Implement `frontend/tests/e2e/v14_4_2_selection_source.e2e.ts` (T09–T16).
  - [x] Update `tools/run_v14_4_2_acceptance.py` to enforce coverage check in `compute_overall` and include all 56 required cases.
  - [x] Verify runner tests (`test_v14_4_2_acceptance_runner.py`).

- [x] **Stage H: Full Verification, Documentation Sync, Commit, & Push (PHẦN 131 - 136, Appendix J, L)**
  - [x] Run full test matrix (pytest, vitest, tsc, build, acceptance runner: 56/56 PASS).
  - [x] Sync documentation (`docs/V14_4_2_IMPLEMENTATION_MAP.md`, `docs/V14_4_2_ACCEPTANCE_REPORT.md`, `docs/V14_4_2_BATCH_CHECKLIST.md`).
  - [ ] Commit with message: `fix(v14.4.2): reconcile provisional targets and enforce chart selection acceptance`.
  - [ ] Push to `origin fix/v14-1-rr-flow` and verify remote SHA.
