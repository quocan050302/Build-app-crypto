# V16.39.8 ACCEPTANCE REPORT — CLOSE PRODUCTION LESSON-TO-ARM ACCEPTANCE

## 1. Metadata
- **Repository:** `quocan050302/Build-app-crypto`
- **Project:** `aurum-desk`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline Commit:** `cbb335a5002a7db891479faa0f7b6d8ea04806bf` (*docs(v16.39.7): add acceptance report for entry-mode trust and lesson approval*)
- **Production Patch Under Test:** `15aa9dfca7d4c5d917df6fc7e1ef51c0e5934d8b` (*fix(v16.39.7): enforce historical mode trust and verify lesson API effects*)
- **Status:** PASS (All mandatory gates achieved test-first without modifying production trading flow)

---

## 2. Evidence Layer Distinction (Phân Biệt Ba Mức Bằng Chứng)

Prior reports (V16.39.7) successfully demonstrated API persistence and component-level score adjustment, but left a verification gap at the full orchestrator integration layer. V16.39.8 formally distinguishes and satisfies all three evidence levels:

1. **Level 1 — API Persistence & Mutation (HTTP / DB):**
   - Verified via real `TestClient` HTTP requests to `POST /api/v1/lessons/{id}/approve-and-activate` and `POST /api/v1/lessons/{id}/set-enable`.
   - Confirmed schema validation, revision checking (`409 Conflict`), atomic rollback on invalid rules (`422 Unprocessable Entity`), and cache invalidation.
   - Tested in `test_v16_39_7_mode_trust_lesson_api_acceptance.py`.

2. **Level 2 — Rule Service & Scorer Component:**
   - Evaluates `LessonRuleService.evaluate_lesson_score_adjustments` and `assess_candidate_quality` in isolation.
   - `test_030` in `test_v16_39_7` renamed to `test_030_scorer_component_lesson_effect_threshold_crossing` (with backward-compatible alias) to accurately reflect its component-level scope (feeding `lesson_delta` into scorer directly without calling `evaluate_cycle`).

3. **Level 3 — Full Production Integration (Closed in V16.39.8):**
   - Call graph: `HTTP mutation -> DB persistence -> cache invalidation -> evaluate_cycle -> _arm_paper_candidate -> rule service -> scorer -> ARM / Funnel decision`.
   - The orchestrator builds `score_ctx` and `lesson_scope_ctx` directly from market series and candidate metadata.
   - No mocked decisions, no synthetic `lesson_delta` injection, and no production guard bypasses.
   - Verified in `backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py`.

---

## 3. Core Acceptance Deliverables & Gates (Các Cổng Nghiệm Thu)

### Gate 1: High-Score Fixture (Pending -> Active -> Disabled)
- **Candidate Baseline:** Causal bullish candle series generating a valid LONG candidate with baseline score `90.0 >= 70.0`.
- **Phase A (Pending Lesson):** Lesson in DB has `is_approved=False`. Orchestrator applies `delta = 0.0` -> `final_score = 90.0` -> Order armed in `paper_orders` with state `ARMED`.
- **Phase B (Active Lesson):** Approved via HTTP API with `score_delta = -5.0` (matching `symbol="XAUUSDT"`, `strategy_family="CG01"`). Orchestrator dynamically queries DB, matches rule, and applies `delta = -5.0` -> `final_score = 85.0 >= 70.0` -> Order still armed in `paper_orders` (proving penalty does not indiscriminately block eligible setups).
- **Phase C (Disabled Lesson):** Disabled via HTTP API `POST /api/v1/lessons/{id}/set-enable` (`enabled=False`). Orchestrator receives `delta = 0.0` -> `final_score = 90.0` -> Baseline ARM behavior restored.

### Gate 2: Threshold Crossing Gate (Baseline ARM -> Penalty Below 70.0 -> Blocked Funnel -> Restored)
- **Mathematical Condition:** Base score `S >= 70.0`, penalty `D = -15.0` (within max penalty cap of 15.0), resulting in `S + D < 70.0`.
- **Candidate Baseline:** Deterministic market series producing base score `82.0 >= 70.0`. Baseline evaluation without penalty creates an ARMED order in `paper_orders`.
- **Active Penalty via API:** HTTP approval activates `-15.0` penalty on spread condition. Orchestrator computes `final_score = 82.0 - 15.0 = 67.0 < 70.0`.
- **ARM Blocked Evidence:**
  - Scorer decision: `BELOW_THRESHOLD`.
  - Zero orders created in `paper_orders` (`count == 0`).
  - `OpportunityFunnelRecord` persists stage `GUARD_BLOCKED` with reason code `BELOW_THRESHOLD` and detailed quality assessment recording rule penalty.
- **API Disable Restoration:** After disabling via HTTP, the same candidate returns `final_score = 82.0 >= 70.0` and successfully creates an ARMED order.

### Gate 3: Bearish SHORT Geometry & Direction Consistency
- Evaluated with bearish market candle series producing a valid SHORT candidate.
- Price geometry strictly preserved: `TP (2622.0) < planned_entry (2628.0) < SL (2631.0)`.
- Direction and candidate snapshot remain strictly SHORT; lesson evaluation does not alter geometry or entry mode.

### Gate 4: Full Execution Coordinator & Lifecycle Integration
- **ARM -> Fill:** Armed order processed by `ExecutionCoordinator.evaluate_orders_sync` with quote hitting planned entry -> transitions to `FILLED` / position `OPEN`.
- **Fill -> Exit:** Market tick hitting TP processed by `process_exit_tick` -> order closes at TP, recording net positive PnL (`realized_pnl_net = +116.59`).
- **Post-Trade Review & Lesson Creation:** Post-trade review worker processes closed order and creates a new pending lesson (`is_approved=False`). Confirms new lesson remains inactive and does not self-activate without administrative HTTP approval.

### Gate 5: Negatives, Boundary & Invariants
- **Timeline Causal Order:** Decisions evaluated at `now_ms < effective_at` receive `delta = 0.0`.
- **Scope Negatives:** Rules scoped to `symbol="BTCUSDT"`, `strategy_family="ICT_CLASSIC"`, `session="LONDON"`, or `stage="BEFORE_FILL"` do not match `XAUUSDT / CG01 / BEFORE_ARM / NY` candidate (`delta = 0.0`).
- **Score Boundaries:** Score `70.0` evaluates to `ELIGIBLE_BY_SCORE`; score `69.9` evaluates to `BELOW_THRESHOLD`.

---

## 4. Acceptance Matrix

| Case ID | Scenario / Test Focus | Evidence Level | Test Function / File | Expected Behavior | Actual Result | Status |
|---|---|---|---|---|---|---|
| **ACC-01** | High-Score Long (Pending) | Level 3 (Full Integration) | `test_010_orchestrator_high_score_long_pending` | `delta = 0.0`, score 90.0, order ARMED | `delta = 0.0`, score 90.0, 1 ARMED order | **PASS** |
| **ACC-02** | High-Score Long (Active) | Level 3 (Full Integration) | `test_020_orchestrator_high_score_long_active` | `delta = -5.0`, score 85.0, order ARMED | `delta = -5.0`, score 85.0, 1 ARMED order | **PASS** |
| **ACC-03** | High-Score Long (Disabled) | Level 3 (Full Integration) | `test_030_orchestrator_high_score_long_disabled` | `delta = 0.0`, score 90.0, order ARMED | `delta = 0.0`, score 90.0, 1 ARMED order | **PASS** |
| **ACC-04** | Threshold Crossing Gate | Level 3 (Full Integration) | `test_040_orchestrator_threshold_crossing_blocks_arm` | Baseline: 82.0 (ARM)<br>Active: 67.0 (BLOCKED)<br>Disabled: 82.0 (ARM) | Baseline: 82.0, 1 order<br>Active: 67.0, 0 orders, Funnel: GUARD_BLOCKED<br>Disabled: 82.0, 1 order | **PASS** |
| **ACC-05** | Bearish Short Geometry | Level 3 (Full Integration) | `test_050_orchestrator_short_candidate_geometry_preserved` | SHORT geometry valid (`TP < Entry < SL`), order ARMED | Geometry verified, 1 ARMED SHORT order | **PASS** |
| **ACC-06** | Full Lifecycle (Fill -> Exit -> Lesson) | Level 3 (Full Integration) | `test_060_full_lifecycle_arm_fill_exit_review_lesson` | ARMED -> FILLED -> CLOSED at TP -> pending review & lesson (not active) | Order closed with profit, review created, lesson pending | **PASS** |
| **ACC-07** | Scope & Temporal Negatives | Level 3 (Full Integration) | `test_070_negative_and_scope_isolation` | Mismatched symbol/session/stage or before effective_at -> delta = 0.0 | All 5 negative scenarios returned delta = 0.0 | **PASS** |
| **ACC-08** | Score Boundary Unit Checks | Level 2 (Scorer Gate) | `test_080_threshold_boundary_unit_checks` | Score 70.0 is ELIGIBLE; 69.9 is BELOW_THRESHOLD | Boundary checks match canonical inclusive logic | **PASS** |
| **ACC-COMP** | Component Crossing Gate | Level 2 (Component Scorer) | `test_030_scorer_component_lesson_effect_threshold_crossing` | Scorer component drops 72.5 -> 67.5 | Verified component-level threshold crossing | **PASS** |

---

## 5. Test Execution Evidence & Commands

### 1. New V16.39.8 Production Acceptance Suite
```powershell
pytest tests/test_v16_39_8_production_lesson_arm_acceptance.py -v
```
**Output:**
```text
tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_010_orchestrator_high_score_long_pending PASSED [ 12%]
tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_020_orchestrator_high_score_long_active PASSED  [ 25%]
tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_030_orchestrator_high_score_long_disabled PASSED [ 37%]
tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_040_orchestrator_threshold_crossing_blocks_arm PASSED [ 50%]
tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_050_orchestrator_short_candidate_geometry_preserved PASSED [ 62%]
tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_060_full_lifecycle_arm_fill_exit_review_lesson PASSED [ 75%]
tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_070_negative_and_scope_isolation PASSED         [ 87%]
tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_080_threshold_boundary_unit_checks PASSED     [100%]
============================== 8 passed in 1.44s ==============================
```

### 2. Combined V16.39.7 & V16.39.8 Regression
```powershell
pytest tests/test_v16_39_7_mode_trust_lesson_api_acceptance.py tests/test_v16_39_8_production_lesson_arm_acceptance.py -v
```
**Output:**
```text
============================== 29 passed in 1.80s ==============================
```

### 3. Full V16.39 Series Test Suite
```powershell
pytest tests/test_v16_39_*.py -v
```
**Output:**
```text
============================== 129 passed, 1 warning in 4.49s ==============================
```

### 4. Core Subsystem Regression Verification
```powershell
pytest tests/test_v16_38_adaptive_profiles.py tests/test_v16_3_cg01_pipeline.py tests/test_v16_3_cg01_contracts.py tests/test_v14_2_entry_guard.py tests/test_v14_1_rr_preview.py tests/test_v10_lesson_rules.py -v
```
**Output:**
```text
============================== 154 passed in 9.76s ==============================
```

**Grand Total Validated:** 283 passed regression tests across all related layers without failure.

---

## 6. Preserved Production Invariants
1. **Zero Unnecessary Production Diffs:** The existing production code (`scalp_watcher_orchestrator.py`, `setup_quality_service.py`, `lesson_rule_service.py`) correctly handles the full call graph when supplied with causal timestamps and proper scope context. No synthetic bypasses or hacks were introduced.
2. **Scoring Threshold Unmodified:** Strict `70.0` quality score cutoff maintained.
3. **Budget & Weight Contracts Preserved:** No adjustments made to factor weights, group budgets, or the 15.0 max penalty cap.
4. **Trading & Guard Parameters Intact:** Daily fill cap (3 fills/day), risk settings (`risk_pct`, leverage, capital, margin mode), and all entry guards remain identical to production.
5. **Network & External Isolation:** All tests operate with SQLite memory engines, TestClient transports, and zero calls to live Bitget or Telegram APIs.
