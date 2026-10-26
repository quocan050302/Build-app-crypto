# AURUM V17.5.2 — IMPLEMENTATION MAP

**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `e2b614b5e4c5f9748294eb1eca09995c4d490dd9`  
**Patch Proposal:** V17.5.2 Final Production Closure for Structural SL & Causal Paper Research  
**Overall Status:** `IMPLEMENTATION_VERIFIED`  
**Empirical Status:** `EMPIRICAL_UNPROVEN`  

---

## 1. Architectural Call Hierarchy & Production Flow

```
[Live WebSocket / REST Market Quotes / Closed Candles]
                         │
                         ▼
       [ScalpWatcherOrchestrator.evaluate_cycle]
                         │
        ├── 1. resolve_stop_placement_policy (Authoritative SQL CAS read)
        ├── 2. compute_stop_atr (1M: 60,000ms, 5M: 300,000ms duration mapping)
        ├── 3. build_stop_volatility_context (spread, tick_size, finite validation)
                         │
                         ▼
             [Model Candidate Evaluation]
        ├── evaluate_m1_continuation_candidate [HIỆN CÓ — updated]
        │    └── build_selected_break_descriptor [MỚI]: binds break_open_ms, broken_at_ms,
        │        known_at_ms, broken_reference_id, and last_sl/last_sh reference
        ├── evaluate_m1_fvg_continuation_candidate [HIỆN CÓ — updated]
        │    └── build_selected_break_descriptor [MỚI]: binds break_res timestamps + reference,
        │        and passes real FVG middle candle timestamp + confirmed_at (not missing start_ms)
        ├── evaluate_m1_reversal_candidate [HIỆN CÓ]
        ├── evaluate_m1_sweep_reclaim_candidate [HIỆN CÓ]
        └── evaluate_cg01_candidate [HIỆN CÓ]
                         │
        ├── [Bound Leg Binding]: resolve_bound_stop_leg [HIỆN CÓ — connected & upgraded]
        │    ├── Origin pivot occurred strictly BEFORE break bar open (p_occ < break_time)
        │    ├── Origin pivot known before decision cutoff (p_known <= decision_cutoff_ms)
        │    ├── Source candle extreme matches pivot price within tick tolerance
        │    └── Break bar close confirms reference penetration
        ├── [Anchor & Buffer Resolution]: resolve_stop_anchor [HIỆN CÓ — updated]
        │    ├── Uses bound leg origin anchor with nested bar timestamp fallback
        │    └── Quantizes SL outward by instrument tick (SHORT ceil / LONG floor)
        ├── [Fail-Closed Barrier]: PAPER_STRUCTURAL requires valid C anchor & ATR; no fallback to A
        └── apply_stop_placement_to_candidate -> StopApplicationResult [HIỆN CÓ]
                         │
                         ▼
       [ScalpWatcherOrchestrator._arm_paper_candidate]
        ├── validate_applied_stop_decision [HIỆN CÓ]:
        │   Verifies candidate SL == calc SL == decision SL == snapshot SL
        └── Exact provenance snapshot: stop_mode_at_arm, stop_policy_version_at_arm, cost_snapshot
                         │
                         ▼
        [ExecutionCoordinator.evaluate_orders_sync]
        ├── Fill verification: re-evaluates risk budget, Net RR, active position guard (max 1 position)
        └── Market/Limit trigger with frozen order geometry (no silent price or qty modification)
                         │
                         ▼
     [ExecutionConsumer._process_quote_worker]
        ├── 1. evaluate_quote_sync(quote, skip_research=True) [HIỆN CÓ — updated]
        │      └── Production order exit evaluated FIRST (SHORT ASK / LONG BID)
        └── 2. advance_stop_comparisons_durable [MỚI]:
               ├── Dedicated isolated database session (SessionLocal)
               ├── Causal Timeline Gate: rejects quotes where quote_ts < plan_cutoff
               ├── Full Eligibility Gate: requires is_valid AND can_execute AND meets_min_rr
               ├── Pure state advancement across live stream
               └── Explicit commit on changes, rollback on error, guaranteed session close
                         │
                         ▼
    [TradeLifecycleService.process_exit_tick & execute_close]
        ├── Pure exit evaluation: SHORT on ASK, LONG on BID
        └── Post-trade evidence and audit persistence
                         │
                         ▼
   [PostTradeReviewService.classify_post_trade_lesson]
        └── Distinct classifications: GREEN (verified structural stop), YELLOW (unverified/missing stop facts)
```

---

## 2. Component & Function Implementation Registry

### A. Model Evaluation Layer (`backend/services/scalp_m1_model_service.py`)
| Function / Symbol | Type | Input / Output | Caller | Test Coverage | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `evaluate_m1_continuation_candidate` | `HIỆN CÓ` (updated) | `(candles_1m, candles_5m, ...)` -> `Optional[M1Candidate]` | `ScalpWatcherOrchestrator.evaluate_cycle` | `tests/test_v17_5_2_boundary_probes.py::test_probe_p1_caller_descriptor_mismatch`<br>`tests/test_v17_5_2_detector_to_exit.py` | Verified |
| `evaluate_m1_fvg_continuation_candidate` | `HIỆN CÓ` (updated) | `(candles_1m, candles_5m, ...)` -> `Optional[M1Candidate]` | `ScalpWatcherOrchestrator.evaluate_cycle` | `tests/test_v17_5_2_detector_to_exit.py` | Verified |

**Key Modifications:**
1. Continuation candidate replaces unverified `selected_break={"id": ..., "bar": ...}` with complete `build_selected_break_descriptor` payload, passing `broken_reference` (`last_sl` for SHORT / `last_sh` for LONG) and exact timestamps.
2. FVG continuation candidate constructs `build_selected_break_descriptor` using `break_res` and passes real FVG middle candle timestamp + `confirmed_at`, removing reliance on non-existent `start_ms`.

---

### B. Stop Placement & Leg Binding (`backend/services/stop_placement_service.py`)
| Function / Symbol | Type | Input / Output | Caller | Test Coverage | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `build_selected_break_descriptor` | `MỚI` | `(break_bar, broken_reference, timeframe_ms, decision_cutoff_ms, model_id, instance_id, direction)` -> `Dict[str, Any]` | `evaluate_m1_continuation_candidate`<br>`evaluate_m1_fvg_continuation_candidate` | `tests/test_v17_5_2_boundary_probes.py::test_probe_p1_caller_descriptor_mismatch`<br>`tests/test_v17_5_2_detector_to_exit.py` | Verified |
| `resolve_bound_stop_leg` | `HIỆN CÓ` (upgraded & connected) | `(swings, selected_break, selected_fvg, closed_candles, ...)` -> `Optional[SelectedStopLeg]` | `resolve_stop_anchor` | `tests/test_v17_5_2_boundary_probes.py::test_probe_p1_caller_descriptor_mismatch`<br>`tests/test_v17_5_1_anchor_leg.py` | Verified |
| `resolve_stop_anchor` | `HIỆN CÓ` (updated) | `(swings, direction, model_id, ...)` -> `Tuple[Optional[float], Dict[str, Any]]` | `plan_structural_stop` | `tests/test_v17_5_2_boundary_probes.py::test_probe_p1_caller_descriptor_mismatch`<br>`tests/test_v17_5_1_structural_gate.py` | Verified |

**Key Modifications:**
1. `build_selected_break_descriptor`: Standardizes break metadata extraction, extracting `break_open_ms`, `broken_at_ms` (open + timeframe), `known_at_ms`, `broken_reference_id`, and `reference_price`.
2. `resolve_bound_stop_leg`: Integrated directly as the authoritative anchor resolver for both Continuation and FVG Continuation. Enforces:
   - `p_occ < break_time`: origin pivot strictly precedes break bar open time.
   - `p_known <= decision_cutoff_ms`: pivot confirmed before decision cutoff.
   - Tick tolerance validation of pivot extreme against actual candle OHLC.
   - Rejection of post-break pivots.
3. `resolve_stop_anchor`: Adds nested bar extraction fallback `_candle_open_ms(selected_break["bar"])` and FVG `middle_bar_ms`/`timestamp`/`time_ms`.

---

### C. Durable Research Service (`backend/services/stop_placement_research_service.py`)
| Function / Symbol | Type | Input / Output | Caller | Test Coverage | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `advance_stop_comparisons_durable` | `MỚI` | `(*, session_factory, quote, as_of_ms, namespace)` -> `Dict[str, Any]` | `ExecutionConsumer._process_quote_worker` | `tests/test_v17_5_2_boundary_probes.py::test_probe_p2_research_worker_durability` | Verified |
| `advance_stop_comparison` | `HIỆN CÓ` (updated) | `(plan, state, quote, as_of_ms)` -> `StopComparisonState` | `advance_active_stop_comparisons` | `tests/test_v17_5_2_boundary_probes.py::test_probe_p3_causal_timeline_gate`<br>`tests/test_v17_5_2_boundary_probes.py::test_probe_p4_research_variant_eligibility` | Verified |
| `advance_active_stop_comparisons` | `HIỆN CÓ` (updated) | `(db, quote, as_of_ms, symbol)` -> `int` | `advance_stop_comparisons_durable` | `tests/test_v17_5_1_research_runtime.py` | Verified |

**Key Modifications:**
1. `advance_stop_comparisons_durable`: Provides a single, clean transaction boundary for research updates. Opens isolated DB session, invokes `advance_active_stop_comparisons`, commits on changes, rolls back on error, and ensures clean session closure.
2. `advance_stop_comparison`:
   - **Causal Timeline Gate (P3)**: Validates `quote_ts >= plan_cutoff` and `as_of_ms >= plan_cutoff`. Quotes occurring prior to the plan/decision cutoff remain in `WAIT_ENTRY`.
   - **Full Eligibility Gate (P4)**: Requires `is_valid and can_execute and meets_min_rr`. Variants failing minimum Net R:R (`meets_min_rr=False`) are blocked from simulation entry (`WAIT_ENTRY`).

---

### D. Execution & Consumer Boundary (`backend/services/execution_consumer.py`)
| Function / Symbol | Type | Input / Output | Caller | Test Coverage | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `evaluate_quote_sync` | `HIỆN CÓ` (updated) | `(quote, as_of_ms, skip_research=False)` -> `None` | `_process_quote_worker`<br>`tests` | `tests/test_v17_5_1_production_flow.py` | Verified |
| `_process_quote_worker` | `HIỆN CÓ` (updated) | `(quote, clock)` -> `None` | Consumer loop | `tests/test_v17_5_2_boundary_probes.py::test_probe_p2_research_worker_durability` | Verified |

**Key Modifications:**
1. Added `skip_research: bool = False` to `evaluate_quote_sync`.
2. In `_process_quote_worker`, calls `evaluate_quote_sync(..., skip_research=True)` and then advances research via `advance_stop_comparisons_durable(session_factory=SessionLocal, quote=quote, as_of_ms=now_ms)`.
3. Ensures trading transactions and research transactions are decoupled, durable, and free from cross-contamination.

---

## 3. Test Suites & Verification Matrix

| Test File | Focus | Test Count | Status |
| :--- | :--- | :--- | :--- |
| `tests/test_v17_5_2_boundary_probes.py` | P1 (metadata mismatch), P2 (worker durability), P3 (causal timeline), P4 (eligibility) | 4 | **PASS** |
| `tests/test_v17_5_2_detector_to_exit.py` | Real detector candles -> ARM -> Coordinator fill -> Lifecycle close -> Review (SHORT, LONG, Guards) | 3 | **PASS** |
| `tests/test_v17_5_1_red_probes.py` | R1-R6 regressions (Fail-closed, ATR duration, CAS DB fence) | 6 | **PASS** |
| `tests/test_v17_5_1_anchor_leg.py` | Leg origin anchor resolution & validation | 3 | **PASS** |
| `tests/test_v17_5_1_structural_gate.py` | Candidate structural stop evaluation & gate | 3 | **PASS** |
| `tests/test_v17_5_1_atr_timeframe.py` | ATR timeframe mapping (1M/5M) & validation | 2 | **PASS** |
| `tests/test_v17_5_1_policy_cas.py` | Policy version CAS concurrency & conflict handling | 4 | **PASS** |
| `tests/test_v17_5_1_research_runtime.py` | Stop comparison research runtime & persistence | 3 | **PASS** |
| `tests/test_v17_5_1_production_flow.py` | Armed-order-to-exit flow & review classification | 2 | **PASS** |
| `tests/test_v17_5_1_guard_matrix.py` | Multi-guard negative matrix | 1 | **PASS** |
| `tests/test_v17_5_1_stop_costs.py` | Risk, sizing, contract step rounding, fee deduction | 2 | **PASS** |
| Prior Regressions (`test_v17_4_*.py`, `test_v16_2_*.py`) | Session roundtrips, atomic mirrors, baseline probes, CG-01 | 35 | **PASS** |
| Frontend Suite (`npm test -- --run`) | UI contracts, components, modals, drawers | 501 | **PASS** |
| Frontend Build (`npm run build`) | TypeScript & Vite production build | - | **PASS** |
| Frontend Lint (`npm run lint`) | ESLint validation | - | **PASS** |
