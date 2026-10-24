# AURUM V17.5.3 — IMPLEMENTATION MAP

**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `cb51fae448356c3c74af9a9057628d7a2b34dd00`  
**Patch Proposal:** V17.5.3 Verification of Structural Stop Origins and Executable Research Lifecycle  
**Technical Status:** `IMPLEMENTATION_VERIFIED`  
**Empirical Status:** `EMPIRICAL_UNPROVEN`  

---

## 1. Architectural Call Hierarchy & Production Flow

```
[Live WebSocket / REST Market Quotes / Closed Candles]
                         │
                         ▼
       [ScalpWatcherOrchestrator.evaluate_cycle]
                         │
        ├── 1. Context validation: htf_bias ("BEARISH"/"BULLISH"), h1_alignment ("ALIGNED")
        ├── 2. resolve_stop_placement_policy (Authoritative SQL CAS read)
        ├── 3. compute_stop_atr (1M: 60,000ms, 5M: 300,000ms duration mapping)
        ├── 4. build_stop_volatility_context (spread, tick_size, finite validation)
                         │
                         ▼
             [Model Candidate Evaluation]
        ├── evaluate_m1_continuation_candidate
        │    └── build_selected_break_descriptor [Causal Barrier]:
        │        Strict decision_cutoff_ms validation (rejects b_open > cutoff or b_close > cutoff)
        ├── evaluate_m1_fvg_continuation_candidate
        │    └── build_selected_break_descriptor [Causal Barrier]
        ├── evaluate_m1_reversal_candidate
        ├── evaluate_m1_sweep_reclaim_candidate
        └── evaluate_cg01_candidate
                         │
        ├── [Bound Leg Binding]: resolve_bound_stop_leg [Causal Boundary]:
        │    ├── Origin pivot occurred strictly BEFORE break bar open (p_occ < break_time)
        │    ├── Origin pivot known before decision cutoff (p_known <= decision_cutoff_ms)
        │    ├── Origin pivot occurred before cutoff (p_occ <= decision_cutoff_ms)
        │    ├── Break bar occurred before cutoff (break_time <= decision_cutoff_ms)
        │    ├── Source candle extreme matches pivot price within tick tolerance
        │    └── Break bar close confirms reference penetration
        ├── [Anchor & Buffer Resolution]: resolve_stop_anchor
        │    ├── Uses bound leg origin anchor with nested bar timestamp fallback
        │    └── Quantizes SL outward by instrument tick (SHORT ceil / LONG floor)
        ├── [Fail-Closed Barrier]: PAPER_STRUCTURAL requires valid C anchor & ATR; no fallback to A
        └── apply_stop_placement_to_candidate -> StopApplicationResult
                         │
                         ▼
       [ScalpWatcherOrchestrator._arm_paper_candidate]
        ├── validate_applied_stop_decision:
        │   Verifies candidate SL == calc SL == decision SL == snapshot SL
        ├── Exact provenance snapshot: stop_mode_at_arm, stop_policy_version_at_arm, cost_snapshot
        └── Order persistence with status='armed'
                         │
                         ▼
        [ExecutionCoordinator.evaluate_orders_sync]
        ├── Fill verification: re-evaluates risk budget, Net RR, active position guard (max 1 position)
        ├── Market/Limit trigger with frozen order geometry (no silent price or qty modification)
        └── Transition to status='paper_open' via TradeLifecycleService.execute_fill
                         │
                         ▼
     [order_plan_snapshot_service.extract_immutable_entry_metadata]
        └── Whitelist Preservation: stop_placement, stop_comparison, stop_comparison_plan_id,
            stop_mode_at_arm, stop_policy_version, applied_variant preserved into cost_snapshot
            across fill transition so /stop-evidence API remains AVAILABLE
                         │
                         ▼
     [ExecutionConsumer._process_quote_worker]
        ├── 1. evaluate_quote_sync(quote, skip_research=True)
        │      └── Production order exit evaluated FIRST (SHORT ASK / LONG BID)
        └── 2. advance_stop_comparisons_durable:
               ├── Dedicated isolated database session (SessionLocal)
               ├── Timestamp presence check: rejects quote_ts is None or <= 0
               ├── Future quote rejection: rejects quote_ts > as_of_ms + 5000
               ├── Plan cutoff gate: rejects quote_ts < plan_cutoff
               ├── Plan expiry transition: marks WAIT_ENTRY as EXPIRED when as_of_ms >= expires_at_ms
               ├── Backlog quote freshness check: stays in WAIT_ENTRY if quote_age > 15000 or is_stale
               ├── Intent-aware entry trigger: evaluate_comparison_entry (MARKET, LIMIT, STOP)
               ├── Error handling without NameError: logger = logging.getLogger(__name__)
               └── Explicit commit on changes, rollback on error, guaranteed session close in finally
                         │
                         ▼
    [TradeLifecycleService.process_exit_tick & execute_close]
        ├── Pure exit evaluation: SHORT on ASK, LONG on BID
        └── Post-trade evidence and audit persistence (status='closed')
                         │
                         ▼
   [PostTradeReviewService.classify_post_trade_lesson]
        ├── Distinct classifications: GREEN (verified structural stop), YELLOW (unverified/missing stop facts)
        └── GET /api/v1/orders/{order_id}/stop-evidence -> 200 OK (status="AVAILABLE", facts populated)
```

---

## 2. Component & Function Implementation Registry

### A. Stop Placement Core & Types (`backend/services/stop_placement_types.py`)
| Function / Symbol | Type | Input / Output | Caller | Test Coverage | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `StopComparisonPlan` | `HIỆN CÓ` (updated) | Dataclass fields: `entry_intent`, `order_type`, `decision_cutoff_ms`, `expires_at_ms`, `cost_snapshot` | `build_stop_comparison_plan` | `test_r3_expired_plan_cannot_open`<br>`test_r4_short_limit_away_from_entry_cannot_fill_at_planned_price` | Verified |
| `StopComparisonPlan.to_dict` | `HIỆN CÓ` (updated) | `to_dict() -> Dict[str, Any]` | Persistence layer & serializations | `tests/test_v17_5_3_boundary_probes.py` | Verified |

---

### B. Bound Leg & Anchor Verification (`backend/services/stop_placement_service.py`)
| Function / Symbol | Type | Input / Output | Caller | Test Coverage | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `build_selected_break_descriptor` | `HIỆN CÓ` (updated) | `(...) -> Dict[str, Any]` | `evaluate_m1_continuation_candidate`<br>`evaluate_m1_fvg_continuation_candidate` | `test_r5_future_break_cannot_bind_anchor`<br>`test_r5_positive_twin_causal_break_binds_anchor` | Verified |
| `resolve_bound_stop_leg` | `HIỆN CÓ` (updated) | `(...) -> Optional[SelectedStopLeg]` | `resolve_stop_anchor` | `test_r5_future_break_cannot_bind_anchor`<br>`test_full_cycle_short_continuation_to_review_api` | Verified |
| `resolve_stop_anchor` | `HIỆN CÓ` | `(...) -> Tuple[Optional[float], Dict[str, Any]]` | `plan_structural_stop` | `test_full_cycle_long_continuation_to_review_api` | Verified |

**Key Causal Validations:**
1. In `build_selected_break_descriptor`: when `decision_cutoff_ms > 0`, if `b_open > decision_cutoff_ms` or `b_close > decision_cutoff_ms`, returns `{}` immediately.
2. In `resolve_bound_stop_leg`: checks `break_time > cutoff`, `break_open_ms > cutoff`, `known_at_ms > cutoff`, and `bar_open > cutoff` against `decision_cutoff_ms`. Any post-decision breaks or pivots are rejected with `reason="origin_after_cutoff"` or `"break_after_cutoff"`.

---

### C. Durable Research Lifecycle (`backend/services/stop_placement_research_service.py`)
| Function / Symbol | Type | Input / Output | Caller | Test Coverage | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `logger` | `MỚI` (module-level) | `logging.getLogger(__name__)` | Error reporting in durable worker | `test_r6_durable_error_returns_error_without_nameerror` | Verified |
| `evaluate_comparison_entry` | `MỚI` | `(plan, quote) -> Tuple[bool, Optional[float]]` | `advance_stop_comparison` | `test_r4_short_limit_away_from_entry_cannot_fill_at_planned_price`<br>`test_r4_positive_twin_short_limit_reached_fills` | Verified |
| `build_stop_comparison_plan` | `HIỆN CÓ` (updated) | Accepts & forwards `entry_intent`, `order_type`, `decision_cutoff_ms`, `expires_at_ms`, `cost_snapshot` | Research planning callers | `tests/test_v17_5_3_boundary_probes.py` | Verified |
| `advance_stop_comparison` | `HIỆN CÓ` (updated) | `(plan, state, quote, as_of_ms) -> StopComparisonState` | `advance_active_stop_comparisons` | `test_r1_*`, `test_r2_*`, `test_r3_*`, `test_r4_*`, `test_r7_*` | Verified |
| `advance_stop_comparisons_durable` | `HIỆN CÓ` (updated) | `(session_factory, quote, as_of_ms, namespace) -> Dict[str, Any]` | `ExecutionConsumer._process_quote_worker` | `test_r6_durable_error_returns_error_without_nameerror`<br>`test_r6_positive_twin_durable_success` | Verified |

**Key Research Lifecyle Fixes:**
1. **Quote Timestamp Presence:** Rejects quotes with `quote_ts is None` or `quote_ts <= 0` by returning existing state unmodified.
2. **Future Quote Timestamp Rejection:** Rejects quotes with `quote_ts > as_of_ms + 5000` to prevent forward quote leakage.
3. **Plan Expiry Handling:** If `as_of_ms >= plan.expires_at_ms` and state is `WAIT_ENTRY`, transitions status to `EXPIRED`.
4. **Order Intent Parity:** Uses `evaluate_comparison_entry` matching `ExecutionCoordinator` logic for MARKET, LIMIT, STOP orders.
5. **Backlog Freshness:** If `quote_age > 15000` or `is_stale=True`, keeps `WAIT_ENTRY` in waiting state rather than triggering a fill.
6. **Exception Durability:** Handles session failure and database errors gracefully with `logger.warning`, rolls back, and returns `{"status": "ERROR", "error": str(e), "changed": 0}` without raising `NameError`.

---

### D. Snapshot & Provenance Preservation (`backend/services/order_plan_snapshot_service.py`)
| Function / Symbol | Type | Input / Output | Caller | Test Coverage | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `extract_immutable_entry_metadata` | `HIỆN CÓ` (updated) | `(metadata: Dict[str, Any]) -> Dict[str, Any]` | `build_fill_plan_snapshot` | `test_full_cycle_short_continuation_to_review_api`<br>`test_full_cycle_long_continuation_to_review_api` | Verified |

**Key Whitelist Fix:**
Whitelisted `stop_placement`, `stop_comparison`, `stop_comparison_plan_id`, `stop_mode_at_arm`, `stop_policy_version`, `applied_variant` with deepcopy. When an armed order fills, `TradeLifecycleService.execute_fill` calls `build_fill_plan_snapshot` without stripping `stop_placement`. The `/api/v1/orders/{order_id}/stop-evidence` endpoint can now successfully read provenance and return `status="AVAILABLE"`.

---

## 3. Test Suites & Verification Matrix

| Gate | Focus | Test Command | Tests Passed | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Gate 1** | V17.5.3 Seven Boundary Probes (R1 - R7 + positive twins) | `pytest tests/test_v17_5_3_boundary_probes.py` | 14 / 14 | **PASS** |
| **Gate 2** | V17.5.3 Full Production Cycle evaluate_cycle to Review & API | `pytest tests/test_v17_5_3_cycle_to_review.py` | 3 / 3 | **PASS** |
| **Gate 3** | V17.5.2 Baseline Suites (Boundary Probes P1-P4 & Detector to Exit) | `pytest tests/test_v17_5_2_boundary_probes.py tests/test_v17_5_2_detector_to_exit.py` | 7 / 7 | **PASS** |
| **Gate 4** | V17.5.1 Feature Suites & Probes (R1-R6 red probes, anchors, CAS, costs) | `pytest tests/test_v17_5_1_*.py` (9 files) | 26 / 26 | **PASS** |
| **Gate 5** | Existing V17.5 Regression Suites | `pytest tests/test_v17_5_*.py` (9 files) | 31 / 31 | **PASS** |
| **Gate 6** | Prior Regressions (CG01, sessions, authority) | `pytest test_v16_2_*, test_v17_4_*` (5 files) | 35 / 35 | **PASS** |
| **Gate 7** | Frontend Vitest Unit & Contract Suite | `npm test -- --run` | 501 / 501 (48 files) | **PASS** |
| **Gate 8** | Frontend Production Build | `npm run build` | 0 errors | **PASS** |
| **Gate 9** | Frontend Lint | `npm run lint` | 0 errors | **PASS** |
| **Gate 10** | Playwright Browser Verification | `npm run test:e2e -- tests/e2e/v17_5_3_stop_browser.e2e.ts` | 3 / 3 | **PASS** |

**Totals:**
- **Backend Tests:** 116 / 116 passed (100%)
- **Frontend Tests:** 501 / 501 passed (100%)
- **Browser E2E Tests:** 3 / 3 passed (100%)
- **Total Combined Tests:** 620 / 620 passed (100%)
