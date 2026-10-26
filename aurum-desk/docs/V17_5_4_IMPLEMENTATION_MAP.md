# AURUM V17.5.4 — IMPLEMENTATION MAP

**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `d43d3cb5c4593bc05dc291131a320e3cd70c49cd` (`fix(v17.5.3): verify structural origins and executable research lifecycle`)  
**Patch Proposal:** V17.5.4 Production Context, Cost Parity, Structural SL Proof, and Real Chart Tests  
**Technical Status:** `IMPLEMENTATION_VERIFIED`  
**Empirical Status:** `EMPIRICAL_UNPROVEN` (Pending > 30 live closed trades in production)  

---

## 1. Architectural Call Hierarchy & Production Flow

```
[Live WebSocket / REST Market Quotes / Closed Candles]
                         │
                         ▼
       [ScalpWatcherOrchestrator.evaluate_cycle]
                         │
        ├── 1. Context validation: htf_bias, h1_alignment, policy_dict, session_ctx
        ├── 2. ATR & Volatility Context: compute_stop_atr (1M/5M), build_stop_volatility_context
        ├── 3. Model candidate evaluation:
        │      ├── evaluate_m1_continuation_candidate
        │      ├── evaluate_m1_fvg_continuation_candidate
        │      ├── evaluate_m1_reversal_candidate
        │      └── evaluate_cg01_candidate
        │
        ├── 4. [Structural SL Proof Gate] resolve_bound_stop_leg:
        │      ├── Pivot candle exists in closed_bars (exact timestamp match)
        │      ├── Pivot price matches candle extreme within tick tolerance (tolerance_tick / tick_size)
        │      ├── Origin belongs to displacement episode of selected break (prioritizes target_origin_id)
        │      └── Strict close-through check: SHORT (b_close < ref_px), LONG (b_close > ref_px)
        │
        ├── 5. [Stop Anchor Resolution] resolve_stop_anchor:
        │      ├── Binds bound_leg into StopAnchorResult (AVAILABLE status)
        │      └── Quantizes SL outward by instrument tick (SHORT ceil / LONG floor)
        │
        ├── 6. [Plan Structural Stop] plan_structural_stop:
        │      └── Forwards bound_leg into StopPlacementDecision across all return paths
        │
        ├── 7. [Production Context Binding] (Parts 001–050):
        │      ├── resolve_stop_comparison_execution_context:
        │      │   Extracts order_type, entry_intent, absolute expires_at_ms, decision_cutoff_ms,
        │      │   cost_snapshot, provenance, context_status="CONTEXT_VERIFIED"
        │      ├── bind_stop_comparison_execution_context:
        │      │   Binds execution context into cand.stop_comparison plan
        │      └── persist_stop_comparison (durable atomic storage)
        │
        ├── 8. [_arm_paper_candidate]:
        │      └── Arm order with verified stop_placement and frozen cost_snapshot
        │
        ▼
[ExecutionCoordinator.evaluate_orders_sync]
        ├── Order fill re-evaluates risk budget, Net RR, active position guard (max 1 position)
        └── Transition to status='paper_open' with immutable cost_snapshot preserved
                         │
                         ▼
[ExecutionConsumer._process_quote_worker & advance_stop_comparison] (Parts 051–100):
        ├── 1. read_verified_comparison_cost_snapshot:
        │      Reads entry_slippage_usd, exit_slippage_usd, fee_usd from frozen cost_snapshot
        ├── 2. Entry evaluation with entry slippage:
        │      evaluate_comparison_entry(..., order_slippage=costs["entry_slippage_usd"])
        ├── 3. Strict Trade Excursion Isolation:
        │      trade_mae / trade_mfe accumulated ONLY when variant is in OPEN state referencing actual_entry
        ├── 4. Exit evaluation with full cost parity:
        │      evaluate_order_exit passes full cost_snapshot to calculate dynamic gross & net pnl
        └── 5. Order state sync and persistence
                         │
                         ▼
[TradeLifecycleService & PostTradeReviewService] (Parts 101–150):
        ├── build_trade_outcome_from_order:
        │      Dynamically derives gross_pnl, net_pnl, realized_r from actual fills without hardcoded numbers
        ├── build_post_trade_review:
        │      Eliminates hardcoded gross_pnl=5.0; calculates exact outcome
        └── GET /api/v1/orders/{order_id}/stop-evidence -> 200 OK:
               Returns status="AVAILABLE", actual_sl, actual_entry, take_profit, stop_placement
                         │
                         ▼
[Playwright Browser E2E & Frontend UI] (Parts 151–200):
        ├── tests/e2e/v17_5_4_stop_browser.e2e.ts:
        │      ├── HTTP 200 & body assertion on /__e2e/seed_order
        │      ├── API assertion on /api/v1/orders/{order_id}/stop-evidence
        │      ├── Geometry assertions: SHORT (TP < Entry < SL), LONG (SL < Entry < TP)
        │      ├── Navigation to Journal -> Row click -> Drawer -> Tab B ("B. Giả Thuyết & SMC")
        │      └── StopPlacementDetails assertions (SL price, anchor description, verified badge)
        └── Full cross-platform verification via tools/run_v17_5_4_acceptance.py
```

---

## 2. Component Implementation Registry (Parts 001–200)

### Group 1: Types & Data Contract (Parts 001–030)
| Symbol | File | Type | Changes & Purpose |
| :--- | :--- | :--- | :--- |
| `StopAnchorResult` | `backend/services/stop_placement_types.py` | Dataclass | Added `bound_leg: Optional[Any] = None` and serialized in `to_dict()`. |
| `StopPlacementDecision` | `backend/services/stop_placement_types.py` | Dataclass | Added `bound_leg: Optional[Any] = None` and serialized in `to_dict()`. |
| `StopComparisonPlan` | `backend/services/stop_placement_types.py` | Dataclass | Added `context_status: str = "CONTEXT_INCOMPLETE"`, `context_reason: Optional[str] = None`, `provenance: Optional[str] = None`, serialized in `to_dict()`. |

### Group 2: Structural SL Proof & Origin Validation (Parts 031–070)
| Function | File | Logic & Invariant Enforcement | Test Coverage |
| :--- | :--- | :--- | :--- |
| `resolve_bound_stop_leg` | `backend/services/stop_placement_service.py` | • Pivot timestamp MUST exist in `closed_bars`.<br>• Pivot price matches candle extreme within `tolerance_tick or tick_size` (instrument metadata, not hardcoded).<br>• Origin belongs to displacement episode of selected break (prioritizes `target_origin_id`).<br>• Strict close-through check: SHORT (`b_close < ref_px`), LONG (`b_close > ref_px`). | `test_anchor_proof_requires_source_candle_in_closed_bars`<br>`test_anchor_proof_fails_if_candle_extreme_mismatches_tick_tolerance`<br>`test_anchor_proof_requires_break_origin_episode_membership`<br>`test_anchor_proof_strict_close_through` |
| `resolve_stop_anchor` | `backend/services/stop_placement_service.py` | Added `tick_size: float = 0.01` parameter. Forwards `bound_leg` into `StopAnchorResult(status="AVAILABLE", anchor=anchor, bound_leg=bound_leg)`. | `test_anchor_proof_bound_leg_preserved` |
| `plan_structural_stop` | `backend/services/stop_placement_service.py` | Forwards `bound_leg=getattr(anchor_result, "bound_leg", None)` into `StopPlacementDecision` across all return paths. | `test_anchor_proof_bound_leg_preserved` |

### Group 3: Production Context Binding & Cost Parity (Parts 071–120)
| Function | File | Logic & Invariant Enforcement | Test Coverage |
| :--- | :--- | :--- | :--- |
| `resolve_stop_comparison_execution_context` | `backend/services/stop_placement_research_service.py` | Resolves authoritative execution context from candidate, policy settings, session context, and decision cutoff. | `test_resolve_stop_comparison_execution_context_complete` |
| `bind_stop_comparison_execution_context` | `backend/services/stop_placement_research_service.py` | Binds execution context into `cand.stop_comparison` plan, marking `context_status="CONTEXT_VERIFIED"`. | `test_bind_stop_comparison_execution_context_updates_plan` |
| `read_verified_comparison_cost_snapshot` | `backend/services/stop_placement_research_service.py` | Extracts `entry_slippage_usd`, `exit_slippage_usd`, and `fee_usd` from frozen snapshot with fallback. | `test_read_verified_comparison_cost_snapshot` |
| `advance_stop_comparison` | `backend/services/stop_placement_research_service.py` | • Passes `order_slippage=costs["entry_slippage_usd"]` to `evaluate_comparison_entry`.<br>• Isolates `trade_mae`/`trade_mfe` strictly to variants in `OPEN` state referencing `actual_entry`.<br>• Passes full `cost_snapshot` to `evaluate_order_exit`. | `test_advance_stop_comparison_cost_parity_entry_slippage`<br>`test_advance_stop_comparison_excursion_parity`<br>`test_advance_stop_comparison_exit_cost_parity` |
| `evaluate_cycle` | `backend/services/scalp_watcher_orchestrator.py` | Resolves and binds execution context before calling `persist_stop_comparison`. | `test_orchestrator_evaluate_cycle_binds_context_to_plans` |
| `apply_stop_placement_to_candidate` | `backend/services/scalp_m1_model_service.py` | Forwards `entry_intent`, `order_type`, `decision_cutoff_ms`, `expires_at_ms`, `cost_snapshot` into comparison plan builder. | `test_model_service_passes_context_to_plan` |

### Group 4: Dynamic Trade Outcomes & Review Reconciliation (Parts 121–150)
| Function | File | Logic & Invariant Enforcement | Test Coverage |
| :--- | :--- | :--- | :--- |
| `build_trade_outcome_from_order` | `backend/services/post_trade_review_service.py` | Derives `gross_pnl`, `net_pnl`, `realized_r` dynamically from closed order accounting without hardcoded values. | `test_cycle_reconciliation_dynamic_outcome_no_hardcoded_gross_pnl` |
| `build_post_trade_review` | `backend/services/post_trade_review_service.py` | Dynamically calculates gross PnL from `(exit - entry) * qty * dir_mult` when missing; uses `build_trade_outcome_from_order`. | `test_post_trade_review_uses_actual_order_pnl` |
| `test_v17_5_2_detector_to_exit.py` | `backend/tests/` | Replaced legacy hardcoded `gross_pnl=5.0` with dynamic review construction. | `test_detector_to_exit_lifecycle` |

### Group 5: Browser Verification & Evidence Assertions (Parts 151–200)
| Test / Script | File | Assertions & Invariant Checks | Status |
| :--- | :--- | :--- | :--- |
| `v17_5_4_stop_browser.e2e.ts` | `frontend/tests/e2e/` | • Seed check: HTTP 200, `status: 'SUCCESS'`.<br>• API check: `/api/v1/orders/{id}/stop-evidence` returns `status: 'AVAILABLE'`.<br>• Geometry check: SHORT (`TP < Entry < SL`), LONG (`SL < Entry < TP`).<br>• UI Drawer check: Navigates to Journal, opens Drawer, clicks Tab B, verifies SL value, anchor text, and verified badge `Xác minh cấu trúc (C)`.<br>• Legacy check: `UNAVAILABLE_LEGACY` with `SL cố định legacy`.<br>• Mobile viewport: 390x844 no horizontal scroll overflow. | **PASS** (4/4 in 9.4s) |
| `v17_5_3_stop_browser.e2e.ts` | `frontend/tests/e2e/` | Upgraded to match real seed, API, geometry, and UI drawer assertions. | **PASS** (3/3 in 6.4s) |
| `run_v17_5_4_acceptance.py` | `tools/` | 10 technical gates, dynamic JUnit XML and Vitest parsing, zero hardcoded counts, outputs `artifacts/v17_5_4/acceptance.json`. | **PASS** (10/10 gates) |

---

## 3. Technical Gates Summary

| Gate | Name | Suite / Command | Passed / Total | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Gate 1** | V17.5.4 Four Core Target Suites | `pytest tests/test_v17_5_4_*.py` | 16 / 16 | **PASS** |
| **Gate 2** | V17.5.3 Probes & Cycle Review | `pytest tests/test_v17_5_3_*.py` | 17 / 17 | **PASS** |
| **Gate 3** | V17.5.2 Baseline Suites | `pytest tests/test_v17_5_2_*.py` | 7 / 7 | **PASS** |
| **Gate 4** | V17.5.1 Feature Suites & Probes | `pytest tests/test_v17_5_1_*.py` | 26 / 26 | **PASS** |
| **Gate 5** | Existing V17.5 Suites | `pytest tests/test_v17_5_*.py` | 31 / 31 | **PASS** |
| **Gate 6** | Prior Regressions | `pytest tests/test_v16_2_* tests/test_v17_4_*` | 35 / 35 | **PASS** |
| **Gate 7** | Frontend Vitest Suites | `npm test -- --run` | 501 / 501 (48 files) | **PASS** |
| **Gate 8** | Frontend Build | `npm run build` | Clean exit (0 errors) | **PASS** |
| **Gate 9** | Frontend Lint | `npm run lint` | Clean exit (0 errors) | **PASS** |
| **Gate 10** | Playwright Browser Verification | `npm run test:e2e -- tests/e2e/v17_5_4_stop_browser.e2e.ts` | 4 / 4 | **PASS** |
| **Total** | **All Backend Test Suites** | **All 6 Backend Gates** | **132 / 132** | **PASS** |
