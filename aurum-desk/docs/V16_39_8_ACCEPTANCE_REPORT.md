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
   - `test_030` in `test_v16_39_7` renamed to `test_030_scorer_component_lesson_effect_threshold_crossing` (with non-test-prefixed compatibility alias `orchestrator_lesson_effect_threshold_crossing`) to accurately reflect its component-level scope (feeding `lesson_delta` into scorer directly without calling `evaluate_cycle`).

3. **Level 3 — Full Production Integration (Closed in V16.39.8):**
   - Call graph: `HTTP mutation -> DB persistence -> cache invalidation -> evaluate_cycle -> _arm_paper_candidate -> rule service -> scorer -> ARM / Funnel decision`.
   - The orchestrator builds `score_ctx` and `lesson_scope_ctx` directly from market series and candidate metadata.
   - No mocked decisions, no synthetic `lesson_delta` injection, and no production guard bypasses.
   - Verified in `backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py`.
   - **Fixture & Profile Scope Notes (Rules 028, 029):**
     - *Policy Scope:* The test fixture in V16.39.8 sets `policy.is_active = False` to cleanly isolate the lesson-to-ARM pipeline from session window restrictions. Runtime execution under active TradingPolicy (NY session windows, reservation, quota) is formally verified in V16.39.9.
     - *Execution Profile Scope:* `make_test_scalp_config` uses `execution_profile = "CG01"`. This validates scoring and lesson impact for CG01; comprehensive adaptive profile routing is validated separately.

---

## 3. Core Acceptance Deliverables & Gates (Các Cổng Nghiệm Thu)

### Gate 1: High-Score Fixture (Pending -> Active -> Disabled)
- **Candidate Baseline:** Causal bullish candle series generating a valid LONG candidate with baseline score `90.0 >= 70.0`.
- **Phase A (Pending Lesson):** Lesson in DB has `is_approved=False`. Orchestrator applies `delta = 0.0` -> `final_score = 90.0` -> Order armed in `paper_orders` with state `ARMED`.
- **Phase B (Active Lesson):** Approved via HTTP API with `score_delta = -5.0` (matching `symbol="XAUUSDT"`, `strategy_family="CG01"`). Orchestrator dynamically queries DB, matches rule, and applies `delta = -5.0` -> `final_score = 85.0 >= 70.0` -> Order still armed in `paper_orders` (proving penalty does not indiscriminately block eligible setups).
- **Phase C (Disabled Lesson):** Disabled via HTTP API `POST /api/v1/lessons/{id}/set-enable` (`enabled=False`). Orchestrator receives `delta = 0.0` -> `final_score = 90.0` -> Baseline ARM behavior restored.
- **Test:** `test_010_orchestrator_high_score_long_pending_active_disabled_flow` (combining all three phases in sequence).

### Gate 2: Threshold Crossing Gate (Baseline ARM -> Penalty Below 70.0 -> Blocked Funnel -> Restored)
- **Mathematical Condition:** Base score `S >= 70.0`, penalty `D = -15.0` (within max penalty cap of 15.0), resulting in `S + D < 70.0`.
- **Candidate Baseline:** Deterministic market series producing base score `82.0 >= 70.0`. Baseline evaluation without penalty creates an ARMED order in `paper_orders`.
- **Active Penalty via API:** HTTP approval activates `-15.0` penalty on spread condition. Orchestrator computes `final_score = 82.0 - 15.0 = 67.0 < 70.0`.
- **ARM Blocked Evidence:**
  - Scorer decision: `BELOW_THRESHOLD`.
  - Zero orders created in `paper_orders` (`count == 0`).
  - `OpportunityFunnelRecord` persists stage `GUARD_BLOCKED` with reason code `BELOW_THRESHOLD` and detailed quality assessment recording rule penalty.
- **API Disable Restoration:** After disabling via HTTP, the same candidate returns `final_score = 82.0 >= 70.0` and successfully creates an ARMED order.
- **Test:** `test_020_orchestrator_threshold_crossing_candidate_rejected_by_active_lesson`.

### Gate 3: Bearish SHORT Geometry & Direction Consistency
- Evaluated with bearish market candle series producing a valid SHORT candidate.
- Price geometry strictly preserved: `TP (2622.0) < planned_entry (2628.0) < SL (2631.0)`.
- Direction and candidate snapshot remain strictly SHORT; lesson evaluation does not alter geometry or entry mode.
- **Test:** `test_030_orchestrator_short_candidate_geometry_and_lesson_flow`.

### Gate 4: Lifecycle Execution & Post-Trade Review Integration
- **ARM -> Direct Fill:** Armed order filled via `TradeLifecycleService.execute_fill` at planned entry -> transitions to `paper_open` / position `OPEN`. (Note: Full execution coordinator `evaluate_orders_sync` with quote evaluation and active trading policy is verified in V16.39.9).
- **Fill -> Exit:** Market tick hitting TP processed by `process_exit_tick` -> order closes at TP, recording net positive PnL (`realized_pnl_net = +116.59`).
- **Post-Trade Review & Lesson Creation:** Post-trade review worker processes closed order and creates a new pending lesson (`is_approved=False`). Confirms new lesson remains inactive and does not self-activate without administrative HTTP approval.
- **Test:** `test_040_full_production_lifecycle_arm_fill_exit_review_lesson`.

### Gate 5: Negatives, Boundary & Invariants
- **Timeline Causal Order:** Decisions evaluated at `now_ms < effective_at` receive `delta = 0.0`. (`test_050_timeline_decision_before_effective_at_not_penalized`).
- **Scope Negatives:** Rules scoped to `symbol="BTCUSDT"`, `strategy_family="ICT_CLASSIC"`, `session="LONDON"`, or `stage="BEFORE_FILL"` do not match `XAUUSDT / CG01 / BEFORE_ARM / NY` candidate (`delta = 0.0`). (`test_060_scope_negative_isolations`).
- **Score Boundaries:** Score `70.0` evaluates to `ELIGIBLE_BY_SCORE`; score `69.9` evaluates to `BELOW_THRESHOLD`. (`test_070_threshold_boundary_unit_gates`).
- **No Double Deduction & Cap:** Multiple identical rule matches do not double-deduct, and penalties never exceed 15.0 max cap. (`test_080_no_double_deduction_and_penalty_cap_invariants`).

---

## 4. Acceptance Matrix

| Case ID | Scenario / Test Focus | Evidence Level | Test Function / File | Expected Behavior | Actual Result | Status |
|---|---|---|---|---|---|---|
| **ACC-01** | High-Score Long (Pending) | Level 3 (Full Integration) | `test_010_orchestrator_high_score_long_pending_active_disabled_flow` (Phase A) | `delta = 0.0`, score 90.0, order ARMED | `delta = 0.0`, score 90.0, 1 ARMED order | **PASS** |
| **ACC-02** | High-Score Long (Active) | Level 3 (Full Integration) | `test_010_orchestrator_high_score_long_pending_active_disabled_flow` (Phase B) | `delta = -5.0`, score 85.0, order ARMED | `delta = -5.0`, score 85.0, 1 ARMED order | **PASS** |
| **ACC-03** | High-Score Long (Disabled) | Level 3 (Full Integration) | `test_010_orchestrator_high_score_long_pending_active_disabled_flow` (Phase C) | `delta = 0.0`, score 90.0, order ARMED | `delta = 0.0`, score 90.0, 1 ARMED order | **PASS** |
| **ACC-04** | Threshold Crossing Gate | Level 3 (Full Integration) | `test_020_orchestrator_threshold_crossing_candidate_rejected_by_active_lesson` | Baseline: 82.0 (ARM)<br>Active: 67.0 (BLOCKED)<br>Disabled: 82.0 (ARM) | Baseline: 82.0, 1 order<br>Active: 67.0, 0 orders, Funnel: GUARD_BLOCKED<br>Disabled: 82.0, 1 order | **PASS** |
| **ACC-05** | Bearish Short Geometry | Level 3 (Full Integration) | `test_030_orchestrator_short_candidate_geometry_and_lesson_flow` | SHORT geometry valid (`TP < Entry < SL`), order ARMED | Geometry verified, 1 ARMED SHORT order | **PASS** |
| **ACC-06** | Full Lifecycle (Direct Fill -> Exit -> Lesson) | Level 3 (Lifecycle Layer) | `test_040_full_production_lifecycle_arm_fill_exit_review_lesson` | ARMED -> direct execute_fill -> CLOSED at TP -> pending review & lesson | Order closed with profit, review created, lesson pending | **PASS** |
| **ACC-07a** | Timeline Causal Order | Level 3 (Full Integration) | `test_050_timeline_decision_before_effective_at_not_penalized` | Decision before effective_at -> delta = 0.0 | Verified delta = 0.0 | **PASS** |
| **ACC-07b** | Scope Negatives | Level 3 (Full Integration) | `test_060_scope_negative_isolations` | Mismatched symbol/session/stage -> delta = 0.0 | All 4 negative scopes returned delta = 0.0 | **PASS** |
| **ACC-08** | Score Boundary Unit Checks | Level 2 (Scorer Gate) | `test_070_threshold_boundary_unit_gates` | Score 70.0 is ELIGIBLE; 69.9 is BELOW_THRESHOLD | Canonical inclusive boundary verified | **PASS** |
| **ACC-09** | Deduplication & Cap Invariants | Level 2 (Scorer Gate) | `test_080_no_double_deduction_and_penalty_cap_invariants` | No double-deduction; max penalty capped at 15.0 | Invariants strictly enforced | **PASS** |
| **ACC-COMP** | Component Crossing Gate | Level 2 (Component Scorer) | `test_030_scorer_component_lesson_effect_threshold_crossing` (in V16.39.7) | Scorer component drops 72.5 -> 67.5 | Verified component-level threshold crossing | **PASS** |

---

## 5. Test Execution Evidence & Commands

### 1. V16.39.8 Production Acceptance Suite
```powershell
$env:PYTHONPATH="backend"; .\backend\venv\Scripts\pytest.exe backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py -v
```
**Actual Pytest Output:**
```text
backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_010_orchestrator_high_score_long_pending_active_disabled_flow PASSED [ 12%]
backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_020_orchestrator_threshold_crossing_candidate_rejected_by_active_lesson PASSED [ 25%]
backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_030_orchestrator_short_candidate_geometry_and_lesson_flow PASSED [ 37%]
backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_040_full_production_lifecycle_arm_fill_exit_review_lesson PASSED [ 50%]
backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_050_timeline_decision_before_effective_at_not_penalized PASSED [ 62%]
backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_060_scope_negative_isolations PASSED [ 75%]
backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_070_threshold_boundary_unit_gates PASSED [ 87%]
backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py::test_080_no_double_deduction_and_penalty_cap_invariants PASSED [100%]
======================== 8 passed, 1 warning in 1.57s =========================
```

### 2. Combined V16.39.7 & V16.39.8 Regression
```powershell
$env:PYTHONPATH="backend"; .\backend\venv\Scripts\pytest.exe backend/tests/test_v16_39_7_mode_trust_lesson_api_acceptance.py backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py -v
```
**Actual Pytest Output:**
```text
======================== 28 passed, 1 warning in 2.06s ========================
```
*(Note: 20 unique tests in V16.39.7 + 8 unique tests in V16.39.8. The duplicate collection alias `test_030_orchestrator_lesson_effect_threshold_crossing` was de-prefixed to ensure clean collection of 28 distinct test scenarios).*

---

## 6. Preserved Production Invariants
1. **Zero Unnecessary Production Diffs:** The existing production code (`scalp_watcher_orchestrator.py`, `setup_quality_service.py`, `lesson_rule_service.py`) correctly handles the full call graph when supplied with causal timestamps and proper scope context. No synthetic bypasses or hacks were introduced.
2. **Scoring Threshold Unmodified:** Strict `70.0` quality score cutoff maintained.
3. **Budget & Weight Contracts Preserved:** No adjustments made to factor weights, group budgets, or the 15.0 max penalty cap.
4. **Trading & Guard Parameters Intact:** Daily fill cap (3 fills/day), risk settings (`risk_pct`, leverage, capital, margin mode), and all entry guards remain identical to production.
5. **Network & External Isolation:** All tests operate with SQLite memory engines, TestClient transports, and zero calls to live Bitget or Telegram APIs.
6. **V16.39.9 Forward Scope:** Execution Coordinator quote matching, active session policy evaluation, and paper runtime observation tools are formally addressed in V16.39.9.
