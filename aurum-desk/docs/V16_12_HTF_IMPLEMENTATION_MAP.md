# V16.12 HTF STATE REPAIR, CONFIG PROPAGATION, IMMUTABLE EVIDENCE & REAL PAPER FLOW — IMPLEMENTATION MAP

**Module:** Higher-Timeframe (HTF) State Machine, Causal Config Propagation, Evidence Immutability & Paper Execution  
**Version:** V16.12  
**Baseline Commit:** `11fea7325f30495757f4ccce754b515886583b09`  
**Target Branch:** `fix/v14-1-rr-flow`  

---

## 1. Architectural Overview

```
Raw Candles / Injected Context
  │
  ▼
`normalize_validate_sort_dedupe` ──► `filter_closed_candles` (open_ms + duration_ms <= as_of_ms)
  │
  ▼
`identify_causal_swings` (pivot occurrence index `i`, confirmation at `i + right_bars` close)
  │
  ▼
`step_htf_state` (Pure chronological state machine dispatch by phase)
  ├── UNDETERMINED: `bootstrap_structure_state` (strict initial bootstrap only)
  ├── ESTABLISHED / PULLBACK: protected high/low tracking, BOS trailing, break detection
  ├── RANGE: `evaluate_range_state` (frozen bounds, breakout detection, wick sweep retention)
  └── TRANSITION: `confirm_transition_structure` (rejects old pre-invalidation swings; handles range reentry)
  │
  ▼
`HTFStructureSnapshot` & `HTFContext` (facts, settings fingerprint, event causal timestamps)
  │
  ├─────────────────────────────────────────────────┐
  ▼                                                 ▼
`CollectorService.update_htf_context`             `build_cg01_context`
- Queries closed candles                           - Filters at now_ms with dynamic min_bars
- Uses time.time() for periodic refresh            - Computes dealing range & causal last_close
- Publishes atomic htf_contexts                    - Calls `resolve_cg01_direction_context`
  │                                                 │
  ▼                                                 ▼
API / WebSocket (`/api/v1/market/matrix`)         `evaluate_cg01_candidate`
                                                    │
                                                    ▼
                                                  `build_cg01_snapshot(..., htf_data=htf_data)`
                                                  - Deep copies htf_context into ScenarioSnapshot
                                                    │
                                                    ▼
                                                  `_arm_paper_candidate` (persists snapshot in DB)
                                                    │
                                                    ▼
                                                  `ExecutionCoordinator` (evaluates quote & fills order)
                                                    │
                                                    ▼
                                                  `PaperBroker` (monitors TP/SL and records trade outcome)
                                                    │
                                                    ▼
                                                  `/api/v1/orders/{id}/chart-evidence`
                                                  - Reads immutable htf_context from order snapshot
                                                  - Immune to subsequent live market shifts
```

---

## 2. Component & Function Implementation Matrix

### 2.1 `backend/services/cg01_types.py` (Configuration Authority)

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Status |
|:---|:---|:---|:---|:---|:---|:---:|
| `CG01Config.htf_min_bars` | `int` | `int` (default 12) | Engine, adapters, context builders | Added field with integer > 0 validation; added to `PARAMETER_ORIGINS`. | `test_reproduce_min_bars_ignored` | **IMPLEMENTED** |
| `CG01Config.htf_break_buffer_ticks` | `int` | `int` (default 0) | HTF state machine, break evaluators | Added field with integer >= 0 validation; added to `PARAMETER_ORIGINS`. | `test_settings_resolution_and_overrides` | **IMPLEMENTED** |

---

### 2.2 `backend/services/htf_structure_service.py` (Canonical State Machine)

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Status |
|:---|:---|:---|:---|:---|:---|:---:|
| `HTFSettings` | `dataclass` | `HTFSettings` | Internal engine | Validated, immutable settings container (`min_bars`, `left_bars`, `right_bars`, `tick_size`, `break_buffer_ticks`). | `test_settings_resolution_and_overrides` | **IMPLEMENTED** |
| `resolve_htf_settings` | `config`, overrides | `HTFSettings` | `evaluate_htf_structure`, `build_cg01_context` | Priority: explicit override > config > default. Strict type & range validation. | `test_settings_resolution_and_overrides` | **IMPLEMENTED** |
| `normalize_validate_sort_dedupe` | `candles` | `List[CanonicalBar]` | `evaluate_htf_structure` | Validates OHLC integrity (rejects NaN/inf/inversion), deduplicates on `open_ms`, sorts ascending. | `test_closed_boundary_exact` | **IMPLEMENTED** |
| `bootstrap_structure_state` | `known_swings`, `settings`, `close_ms` | `Optional[HTFState]` | `step_htf_state` | Bootstraps initial `UNDETERMINED` state strictly from chronological swing sequences. Never used to recover after invalidation. | `test_reproduce_bullish_break_and_false_rebound` | **IMPLEMENTED** |
| `evaluate_range_state` | `state`, `bar`, `known_swings`, `settings` | `HTFStepResult` | `step_htf_state` | Tests closes against frozen range bounds before updating bounds. Dispatches `RANGE_BREAK_UP` or `RANGE_BREAK_DOWN` to `TRANSITION`. Wick sweeps retained in `RANGE`. | `test_reproduce_range_stuck_and_breakout` | **IMPLEMENTED** |
| `confirm_transition_structure` | `state`, `bar`, `known_swings`, `settings` | `Optional[StructureEvent]` | `step_htf_state` | Rejects pre-invalidation swings from re-bootstrapping trend. Validates range re-entry (`RANGE_REENTRY`) and causal post-invalidation confirmations. | `test_valid_recovery_after_bullish_invalidation`, `test_range_breakdown_reentry_and_second_breakout` | **IMPLEMENTED** |
| `step_htf_state` | `state`, `bar`, `known_swings`, `settings` | `HTFStepResult` | `evaluate_htf_structure` | Pure chronological step function. Dispatches by phase. Trails protected levels upon confirmed BOS. | `test_reproduce_bullish_break_and_false_rebound` | **IMPLEMENTED** |
| `evaluate_htf_structure` | `candles`, `timeframe`, `as_of_ms`, `config`, `overrides` | `HTFStructureSnapshot` | `build_htf_context`, replay drivers | Evaluates bar-by-bar progression up to `as_of_ms`. Computes settings fingerprint and algorithm version. | `test_reproduce_bullish_break_and_false_rebound` to `test_reproduce_relation_transition` | **IMPLEMENTED** |
| `resolve_cg01_direction_context` | `h4_ctx`, `h1_ctx`, `range_pos`, `config` | `Dict[str, Any]` | `build_cg01_context`, `evaluate_cg01_candidate` | Checks data sufficiency before phase. Checks `TRANSITION` and `RANGE` before generic `UNKNOWN`. Yields conditional directions (`["LONG", "SHORT"]`). | `test_reproduce_relation_transition`, `test_relation_matrix` | **IMPLEMENTED** |

---

### 2.3 `backend/services/smc_regime_service.py` (Context Adapter)

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Status |
|:---|:---|:---|:---|:---|:---|:---:|
| `build_htf_context` | `candles`, `timeframe`, `min_bars`, `as_of_ms`, `config`, `overrides` | `HTFContext` | `collector_service`, `cg01_engine`, `scalp_watcher_orchestrator` | Propagates `min_bars` and config overrides directly to `evaluate_htf_structure`. Reads engine-determined `snapshot.provenance`. | `test_reproduce_min_bars_ignored` | **IMPLEMENTED** |

---

### 2.4 `backend/services/cg01_evidence_adapter.py` (Evidence Serialization)

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Status |
|:---|:---|:---|:---|:---|:---|:---:|
| `build_cg01_snapshot` | `context`, `candidate`, ..., `htf_data: Optional[Dict]` (kw-only) | `ScenarioSnapshot` | `evaluate_cg01_candidate` | Deep-copies `htf_context` payload into `snap.context["htf_context"]`. Stores schema version, H4/H1 sub-snapshots, and settings fingerprint. | `test_long_full_execution_flow_with_htf_evidence` | **IMPLEMENTED** |
| `build_cg01_evidence_nodes` | `candidate`, `closed_context` | `List[EvidenceNodeDTO]` | `evaluate_cg01_candidate`, drawer | Enriches evidence nodes with HTF context level references and provenance tags. | `test_long_full_execution_flow_with_htf_evidence` | **IMPLEMENTED** |

---

### 2.5 `backend/services/cg01_engine.py` (Decision & Warmup Engine)

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Status |
|:---|:---|:---|:---|:---|:---|:---:|
| `build_cg01_context` | `closed_context`, `now_ms`, `config` | `Dict[str, Any]` | `evaluate_cg01_candidate`, replay driver | Validates warmup with dynamic `config.htf_min_bars`. Computes causal `last_close` from `c_1m_closed` or `c_15m_closed`. | `test_case_14_cg01_positive_long_flow` | **IMPLEMENTED** |
| `evaluate_cg01_candidate` | `context`, `current_quote`, `config`, `now_ms`, ... | `CG01Decision` | Orchestrator, coordinator, replay driver | Passes `htf_data=htf_data` to `build_cg01_snapshot`. Decision carries exact HTF facts used at evaluation. | `test_long_full_execution_flow_with_htf_evidence` | **IMPLEMENTED** |

---

### 2.6 `backend/services/collector_service.py` (Realtime Feed Service)

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Status |
|:---|:---|:---|:---|:---|:---|:---:|
| `update_htf_context` | `symbol: str` | `None` | Background collector thread, startup | Fixed unit bug: assigns `_last_htf_update = time.time()` (seconds) instead of milliseconds, allowing continuous periodic refreshes. Publishes bundle atomically. | `test_case_13` | **IMPLEMENTED** |

---

### 2.7 `backend/services/scalp_watcher_orchestrator.py` (Cycle Orchestrator)

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Status |
|:---|:---|:---|:---|:---|:---|:---:|
| `evaluate_cycle` | `candles_1m`, `candles_15m`, `candles_1h`, `candles_4h`, ... | `CycleEvaluationResult` | Main loop, replay drivers | Distinguishes absent (`None`) from empty (`[]`) candle injections. Derives HTF strictly from injected context when provided. Tags `context_source`. | `test_case_10_replay_isolation_no_live_collector_bias` | **IMPLEMENTED** |

---

### 2.8 `backend/schemas.py` & `backend/services/chart_evidence_service.py` (API Contract)

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Status |
|:---|:---|:---|:---|:---|:---|:---:|
| `ChartEvidenceDTO` | (fields) | DTO instance | FastAPI routes | Added `htf_context: Optional[Dict[str, Any]] = None`. | `test_order_chart_evidence_api` | **IMPLEMENTED** |
| `build_selected_chart_bundle_from_dict` | `snap_dict` | `ChartEvidenceDTO` | `/api/v1/orders/{id}/chart-evidence`, `/setups`, `/scenarios` | Extracts `htf_context` directly from snapshot dict. Preserves historical facts without querying live collector. | `test_order_chart_evidence_api`, `test_snapshot_immutability_after_subsequent_market_shift` | **IMPLEMENTED** |

---

### 2.9 Frontend Components (`frontend/src/`)

| File / Component | Logic Changes & Invariants | Associated Tests | Status |
|:---|:---|:---|:---:|
| `frontend/src/types/smcScenario.ts` | Added `htf_context?: any;` to `ChartEvidenceBundleV144`. | `npm test` | **IMPLEMENTED** |
| `frontend/src/components/EvidenceAccordion.tsx` | Added `formatHtfContext` translating biases to friendly Vietnamese ("H4 tăng · H1 đang chuyển cấu trúc — chờ xác nhận mới"). Adds newbie educational note distinguishing context from entry triggers. Fallback for legacy orders: "Lệnh cũ chưa lưu bằng chứng HTF". | `npm test` | **IMPLEMENTED** |
| `frontend/src/components/TradeDetailsDrawer.tsx` | Passes `htfContext={selectedRawBundle?.htf_context}` to `EvidenceAccordion`. | `npm test` | **IMPLEMENTED** |

---

## 3. Test Suites & Node ID Map

| Test Node ID | File | Target Requirement / Invariant |
|:---|:---|:---|
| `test_reproduce_bullish_break_and_false_rebound` | `backend/tests/test_v16_12_htf_state_machine.py` | Rules 022, 023, 071, 075, 142: Invalidation at 2515 (below 2520) shifts to `TRANSITION`; subsequent rebound to 2530 remains `TRANSITION`. |
| `test_reproduce_bearish_break_and_false_recovery` | `backend/tests/test_v16_12_htf_state_machine.py` | Rules 025, 072, 143: Break of protected high at 2585 shifts to `TRANSITION`; false recovery to 2570 remains `TRANSITION`. |
| `test_reproduce_range_stuck_and_breakout` | `backend/tests/test_v16_12_htf_state_machine.py` | Rules 026, 027, 081, 083, 145: Range breakout with close at 110 above frozen high 105 generates `RANGE_BREAK_UP` and shifts to `TRANSITION`. |
| `test_reproduce_min_bars_ignored` | `backend/tests/test_v16_12_htf_state_machine.py` | Rules 028, 038, 044, 050: Explicit `min_bars=100` parameter strictly enforced, returning `INSUFFICIENT_DATA`. |
| `test_reproduce_relation_transition` | `backend/tests/test_v16_12_htf_state_machine.py` | Rules 029, 091, 092: H4 `BULLISH` + H1 `TRANSITION` evaluates to relation `TRANSITION` instead of being masked by `UNKNOWN`. |
| `test_valid_recovery_after_bullish_invalidation` | `backend/tests/test_v16_12_htf_state_machine.py` | Rules 076, 078, 144: Causal sequence forming entirely after invalidation cutoff re-establishes `BULLISH` with `STRUCTURE_RECONFIRMED`. |
| `test_range_breakdown_reentry_and_second_breakout` | `backend/tests/test_v16_12_htf_state_machine.py` | Rules 084, 088, 090, 146: Range breakdown followed by close inside range issues `RANGE_REENTRY`, and subsequent breakdown succeeds. |
| `test_closed_boundary_exact` | `backend/tests/test_v16_12_htf_state_machine.py` | Rules 035, 147: Exact time boundary filtering (`open + duration <= as_of_ms`) rejects unclosed or future bars. |
| `test_settings_resolution_and_overrides` | `backend/tests/test_v16_12_htf_state_machine.py` | Rules 041, 042, 050: Override precedence over config and defaults; validation of integer parameters. |
| `test_relation_matrix[...]` | `backend/tests/test_v16_12_htf_state_machine.py` | Rules 091 - 100: Comprehensive parameterized relation matrix across all bias and phase permutations. |
| `test_long_full_execution_flow_with_htf_evidence` | `backend/tests/test_v16_12_cg01_execution.py` | Rules 151 - 160: Full Long production flow (Candidate -> Arm -> Coordinator Fill -> PaperBroker TP exit). |
| `test_short_full_execution_flow_with_htf_evidence` | `backend/tests/test_v16_12_cg01_execution.py` | Rules 151 - 160: Full Short production flow (Symmetric Short geometry, Coordinator Fill, PaperBroker SL exit). |
| `test_snapshot_immutability_after_subsequent_market_shift` | `backend/tests/test_v16_12_cg01_execution.py` | Rules 111 - 120, 171: Order snapshot saved at entry retains original bullish HTF facts after live market shifts to bearish. |
| `test_order_chart_evidence_api` | `backend/tests/test_v16_12_cg01_execution.py` | Rules 121 - 130: `/api/v1/orders/{order_id}/chart-evidence` returns persisted immutable `htf_context`. |
| `test_execution_guard_blocking` | `backend/tests/test_v16_12_cg01_execution.py` | Rules 008, 161 - 165: Account margin guard blocks arming when balance is insufficient, even with perfect HTF context. |
