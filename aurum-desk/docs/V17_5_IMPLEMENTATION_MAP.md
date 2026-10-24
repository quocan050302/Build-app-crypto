# AURUM V17.5 — IMPLEMENTATION MAP

**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `2c67c28dc9dfd2c869477fc7082a05a4671ba043`  
**Patch Proposal:** V17.5 Structural Stop Placement, Execution Evidence & Paired Paper Research  

---

## 1. Architecture & Call Hierarchy

```
[Candle Stream / Quotes]
           │
           ▼
[ScalpWatcherOrchestrator.evaluate_cycle]
     │
     ├── 1. resolve_stop_placement_policy (stop_placement_policy_service.py) -> StopPlacementPolicy
     ├── 2. compute_stop_atr (stop_placement_service.py) -> StopATRResult (M1, M5)
     ├── 3. build_stop_volatility_context (spread, tick_size)
     │
     ▼
[Model Candidate Evaluation] (Dispatch with stop_policy & stop_context)
     ├── evaluate_m1_continuation_candidate
     ├── evaluate_m1_reversal_candidate
     ├── evaluate_m1_fvg_continuation_candidate
     ├── evaluate_m1_sweep_reclaim_candidate
     └── evaluate_cg01_candidate
           │
           ├── [Target Invariance]: Freeze legacy baseline TP before structural SL
           ├── resolve_stop_anchor: Anchor to protected leg origin / sweep extreme
           ├── quantize_stop_outward: Decimal outward rounding to tick grid
           ├── calculate_risk_reward: Independent canonical sizing (budget preserved)
           └── plan_structural_stop -> (StopPlacementDecision, StopComparisonPlan)
     │
     ▼
[ScalpWatcherOrchestrator._arm_paper_candidate]
     ├── Invariant Check: cand.stop_loss == calc.stop_loss (rejects STOP_PLAN_MISMATCH)
     └── Persists stop_placement, stop_mode_at_arm, comparison_plan_id into cost_snapshot
     │
     ▼
[ExecutionCoordinator.evaluate_orders_sync]
     └── Fill execution with frozen order SL and actual entry
     │
     ▼
[TradeLifecycleService.process_exit_tick & execute_close]
     ├── Exit evaluated via shared_exit_evaluator:
     │     - SHORT SL evaluated against ASK
     │     - LONG SL evaluated against BID
     └── Stored into cost_snapshot['exit_evidence']
     │
     ▼
[PostTradeReviewService.classify_post_trade_lesson]
     └── Decouples plan compliance from stop quality (YELLOW if missing structural proof)
     │
     ▼
[Frontend UI]
     ├── JournalTab (hypotheses drawer) -> StopPlacementDetails (6-line compact card)
     ├── SettingsTab -> StopPlacementSettings (3 modes, CAS versioning)
     └── StopComparisonModal -> Side-by-side A / B / C comparison table
```

---

## 2. File & Function Implementation Mapping

| Module / File | Function / Symbol | Inputs | Outputs | Callers | Tests | Status |
|---|---|---|---|---|---|---|
| `backend/services/stop_placement_types.py` | `StopAnchor`, `StopPlacementPolicy`, `StopPlacementDecision`, `StopComparisonPlan` | Dataclass parameters | Immutable frozen objects | All V17.5 services | `test_v17_5_stop_placement.py` | VERIFIED |
| `backend/services/stop_placement_service.py` | `compute_stop_atr` | `closed_bars`, `period=14`, `timeframe`, `as_of_ms` | `StopATRResult` (SMA_TR_V1) | Orchestrator cycle | `test_v17_5_stop_placement.py` | VERIFIED |
| `backend/services/stop_placement_service.py` | `quantize_stop_outward` | `anchor_price`, `buffer_price`, `direction`, `tick_size` | `float` (ceil for SHORT, floor for LONG) | `plan_structural_stop` | `test_v17_5_stop_placement.py` | VERIFIED |
| `backend/services/stop_placement_service.py` | `compute_stop_buffer` | `policy`, `tick_size`, `atr_m1`, `atr_m5`, `bid`, `ask` | `StopBufferResult` | `plan_structural_stop` | `test_v17_5_stop_placement.py` | VERIFIED |
| `backend/services/stop_placement_service.py` | `resolve_stop_anchor` | `model_id`, `direction`, `selected_break/fvg/sweep`, `swings` | `StopAnchorResult` | Scalp models & CG01 | `test_v17_5_anchor_binding.py` | VERIFIED |
| `backend/services/stop_placement_service.py` | `plan_structural_stop` | `planned_entry`, `take_profit`, `legacy_sl`, `anchor`, `buffer` | `StopPlacementDecision` | Candidate evaluation | `test_v17_5_model_integration.py` | VERIFIED |
| `backend/services/stop_placement_policy_service.py` | `resolve_stop_placement_policy` | `db: Session` | `StopPlacementPolicy` | Orchestrator, GET endpoint | `test_v17_5_policy_api.py` | VERIFIED |
| `backend/services/stop_placement_policy_service.py` | `update_stop_placement_policy` | `db`, `payload`, `expected_version` | `(StopPlacementPolicy, is_mutation)` | PUT endpoint | `test_v17_5_policy_api.py` | VERIFIED |
| `backend/services/stop_placement_research_service.py` | `build_stop_comparison_plan` | candidate inputs, `stop_decision` | `StopComparisonPlan` (A, B, C) | Scalp candidate builders | `test_v17_5_stop_research.py` | VERIFIED |
| `backend/services/stop_placement_research_service.py` | `compute_stop_excursions` | `direction`, `planned_entry`, `quote` | `{"adverse_price", "favorable_price"}` | Research advancement | `test_v17_5_stop_research.py` | VERIFIED |
| `backend/services/scalp_m1_model_service.py` | `apply_stop_placement_to_candidate` | candidate, `stop_policy`, `stop_context` | `(sl, tp, calc, stop_dec, comp_plan)` | All 4 M1 evaluators | `test_v17_5_model_integration.py` | VERIFIED |
| `backend/services/cg01_engine.py` | `evaluate_cg01_candidate` | Setup context, swings | `ScalpCandidate` with structural SL | Orchestrator | `test_v17_5_model_integration.py` | VERIFIED |
| `backend/services/trade_lifecycle_service.py` | `execute_close` | `db`, `order_id`, `exit_price`, `exit_cause`, `exit_evidence` | `PaperOrder` | `process_exit_tick` | `test_v17_5_exit_evidence.py` | VERIFIED |
| `backend/services/post_trade_review_service.py` | `classify_post_trade_lesson` | `review`, `entry_snapshot`, `compliance_result` | `LessonClassification` | Post trade review flow | `test_v17_5_stop_review.py` | VERIFIED |
| `backend/main.py` | `GET/PUT /api/v1/policy/stop-placement` | Policy patch payload | Confirmed policy DTO | Frontend API client | `test_v17_5_policy_api.py` | VERIFIED |
| `frontend/src/components/StopPlacementDetails.tsx` | Component | `order` | React 6-line card | `JournalTab.tsx` | Build verified | VERIFIED |
| `frontend/src/components/StopPlacementSettings.tsx` | Component | - | Settings form | `App.tsx` (Settings) | Build verified | VERIFIED |
| `frontend/src/components/StopComparisonModal.tsx` | Component | `plan` | Modal A/B/C table | `JournalTab.tsx` | Build verified | VERIFIED |

---

## 3. Rollout Compatibility & Invariants

1. **Default Mode:** `SHADOW_COMPARE` when `stop_placement_v1.*` keys do not exist in the database.
2. **Production Routing:**
   - Under `LEGACY` and `SHADOW_COMPARE`: Execution geometry uses Variant A (legacy SL).
   - Under `PAPER_STRUCTURAL`: Execution geometry uses Variant C (structural anchor + dynamic buffer).
3. **Target Invariance:** Baseline TP is computed from legacy model rules and kept strictly frozen. Nosing SL wider does not drag TP further away.
4. **Order State Safety:**
   - Existing open, armed, or closed orders are never mutated.
   - Sizing uses the canonical calculator, strictly preserving dollar risk budget ($10 on $1000 equity).
5. **Research Observations:**
   - Saved under namespace `STOP_PLACEMENT_V1` in `PaperObservation`.
   - Never consume daily fills quota, never touch account balances, and never trigger Telegram outbox notifications.
