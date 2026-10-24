# AURUM V17.5.1 — IMPLEMENTATION MAP

**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `0c2f2cee6e82163b6bab623402ee37767516ebc1`  
**Patch Proposal:** V17.5.1 Final Feature Closure: Structural Stop Loss & Real Paper Evidence  
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
        ├── evaluate_m1_reversal_candidate [HIỆN CÓ — updated]
        ├── evaluate_m1_fvg_continuation_candidate [HIỆN CÓ — updated]
        ├── evaluate_m1_sweep_reclaim_candidate [HIỆN CÓ — updated]
        └── evaluate_cg01_candidate [HIỆN CÓ — updated]
                         │
        ├── [Fail-Closed Routing]: PAPER_STRUCTURAL requires valid C anchor & ATR; no fallback to A
        ├── [Anchor Binding]: resolve_bound_stop_leg requires pivot occurred before break bar
        ├── [Sweep Extraction]: build_sweep_stop_descriptor uses sweep_time_ms, reclaim close, sweep_extreme
        ├── [Buffer & Sizing]: max(tick, k1*ATR1, k5*ATR5, k_spread*spread); quantity scaled within budget
        └── apply_stop_placement_to_candidate -> StopApplicationResult [MỚI]
                         │
                         ▼
       [ScalpWatcherOrchestrator._arm_paper_candidate]
        ├── validate_applied_stop_decision [MỚI]: Fail-closed barrier verifying candidate SL == calc SL == decision SL
        └── Exact provenance snapshot: stop_mode_at_arm, stop_policy_version_at_arm, cost_snapshot
                         │
                         ▼
        [ExecutionCoordinator.evaluate_orders_sync]
        ├── Fill verification: re-evaluates risk budget, Net RR, active position guard (max 1 position)
        └── Market/Limit trigger with frozen order geometry (no silent price or qty modification)
                         │
                         ▼
     [ExecutionConsumer.evaluate_quote_sync / _process_quote_worker]
        └── advance_active_stop_comparisons [MỚI]: Dispatches live quote to in-flight research plans
            (active even when no production order exists or production order closed)
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

### A. Core Types & DTOs (`backend/services/stop_placement_types.py`)
- `SelectedStopLeg` `[MỚI]`: Dataclass capturing explicit structural leg descriptor (`origin_pivot_id`, `origin_price`, `origin_occurred_at_ms`, `origin_known_at_ms`, `broken_reference_id`, `broken_reference_price`, `break_bar_id`, `break_open_ms`, `break_known_at_ms`, `fvg_id`, `source_bar_ids`, `decision_cutoff_ms`, `price_basis`).
- `StopApplicationResult` `[MỚI]`: Strongly typed result containing `allowed: bool`, `selected_sl: Optional[float]`, `target: float`, `calculation: Optional[CalculationResult]`, `decision: StopPlacementDecision`, `comparison: Optional[StopComparisonPlan]`, `reason_codes: Tuple[str, ...]`. Supports 5-tuple unpacking for complete backwards compatibility.
- `StopPlacementDecision` `[HIỆN CÓ — updated]`: Added `selected_sl: Optional[float] = None`, `volatility_context: Optional[Dict[str, Any]] = None`. When Variant C cannot be formed in `PAPER_STRUCTURAL`, `selected_sl` is `None` and `applied_variant="A"`.
- `StopComparisonState` `[MỚI]`: Separated mutable runtime tracking state from immutable research plan payloads.

### B. Stop Placement Core Service (`backend/services/stop_placement_service.py`)
- `compute_stop_atr` `[HIỆN CÓ — updated]`:
  - Enforces `TIMEFRAME_MS = {"1M": 60_000, "5M": 300_000}`.
  - Rejects duration mismatch with `TIMEFRAME_DURATION_MISMATCH`.
  - Fixes 5M close timestamp recording: `last_closed_at_ms = last_open + 300_000` (resolves Probe R3).
  - Handles flat zero ATR cleanly with `status="AVAILABLE"`.
- `resolve_stop_anchor` `[HIỆN CÓ — updated]`:
  - Enforces `leg_cutoff_ms`: excludes pivots occurring after the selected break bar (resolves Probe R2).
  - Sorts candidate pivots deterministically by `occurred_at_ms` descending (closest before break), then `known_at_ms`, then ID.
- `resolve_bound_stop_leg` `[MỚI]`: Validates and constructs `SelectedStopLeg`, ensuring origin pivot occurred strictly before break bar and is known before decision cutoff.
- `build_sweep_stop_descriptor` `[MỚI]`: Correctly maps detector fields `sweep_time_ms`, `sweep_extreme`, `reclaim_bar_idx` (with bar closure), `sweep_id`, `episode_id`.
- `plan_structural_stop` `[HIỆN CÓ — updated]`:
  - Fail-closed: in `PAPER_STRUCTURAL`, if anchor is missing or geometry invalid, returns `status="WAITING_EVIDENCE"` / `REJECTED_GEOMETRY`, leaves `selected_sl=None`, and sets `applied_variant="A"`.
- `validate_applied_stop_decision` `[MỚI]`: Central barrier invoked at evaluator completion and ARM critical section. Rejects any trade in `PAPER_STRUCTURAL` with missing anchor, non-C applied variant, or SL discrepancy.

### C. Policy Service & CAS DB Fence (`backend/services/stop_placement_policy_service.py`)
- `StopPolicyConflictError` `[MỚI]`: Typed exception with `code="STALE_STOP_POLICY_VERSION"` and `fresh_version: int`.
- `StopPolicyValidationError` `[MỚI]`: Typed validation exception with `code="INVALID_STOP_POLICY_PAYLOAD"`.
- `ensure_stop_policy_version_row` `[MỚI]`: Idempotent SQL insert for version row bootstrapping in active transactions.
- `resolve_stop_placement_policy` `[HIỆN CÓ — updated]`: Directly queries scalar rows via `select(models.SystemConfig.key, models.SystemConfig.value)` to bypass stale ORM entity identity maps (resolves Probe R6).
- `update_stop_placement_policy` `[HIỆN CÓ — updated]`:
  - Executes conditional SQL `UPDATE ... WHERE key='stop_placement_v1.version' AND value=expected_version`.
  - If rowcount != 1, reads authoritative DB version scalar and raises `StopPolicyConflictError`.
  - Performs true no-op detection: returns unchanged policy without version churn.

### D. Model Evaluation & Research Service (`backend/services/scalp_m1_model_service.py`, `backend/services/stop_placement_research_service.py`)
- `apply_stop_placement_to_candidate` `[HIỆN CÓ — updated]`:
  - Fail-closed: in `PAPER_STRUCTURAL`, returns `allowed=False`, `selected_sl=None`, and `calc.can_execute=False` when Variant C cannot be formed (resolves Probe R1).
  - Retains full shadow compatibility: in `SHADOW_COMPARE`, Variant A execution is never blocked by unavailable Variant C research data.
- `advance_stop_comparison` `[HIỆN CÓ — updated]`:
  - Guaranteed purity: deep copies plan state via `copy.deepcopy` (resolves Probe R4).
  - Quote validation: strictly checks finite `bid > 0`, `ask >= bid`; empty or malformed quotes cannot open positions (resolves Probe R5).
- `persist_stop_comparison` `[HIỆN CÓ — updated]`: Preserves immutable plan payload once written; accepts both dataclass and dictionary.
- `advance_active_stop_comparisons` `[MỚI]`: Production runtime caller invoked on live quote processing.
- `serialize_stop_comparison_for_ui` `[MỚI]`: Emits canonical DTO with `variants.A`, `variants.B`, `variants.C` and backwards-compatible `variant_a`, `variant_b`, `variant_c`.

### E. Runtime Wiring (`backend/services/execution_consumer.py`, `backend/services/scalp_watcher_orchestrator.py`)
- `ExecutionConsumer.evaluate_quote_sync` `[HIỆN CÓ — updated]`: Wired `advance_active_stop_comparisons(db, quote, as_of_ms=proc_now_ms)` across both active-position and no-active-position branches.
- `ScalpWatcherOrchestrator._arm_paper_candidate` `[HIỆN CÓ — updated]`:
  - Added `validate_applied_stop_decision` barrier check.
  - Snapshots exact decision `mode`, `policy_version`, and `applied_variant` at ARM time.

### F. Post-Trade Review & Lesson Governance (`backend/services/post_trade_review_service.py`)
- `classify_post_trade_lesson` `[HIỆN CÓ — updated]`:
  - Reads `stop_placement` from `entry_snapshot` and cost snapshot.
  - Distinguishes verified structural stop (C) from unverified legacy stop (A) or missing proof.
  - Emits factual Vietnamese reason without generic unproven claims.

### G. Frontend Components (`frontend/src/components/`)
- `StopComparisonModal.tsx` `[HIỆN CÓ — updated]`: Reads `variants['A'] || variant_a`, `variants['B'] || variant_b`, `variants['C'] || variant_c` gracefully.
- `StopPlacementDetails.tsx` `[HIỆN CÓ — updated]`:
  - Evaluates verified badge: only displays "Xác minh cấu trúc (C)" when `status === 'VALID' && anchor && applied_variant === 'C'`.
  - Evaluates legacy badge: "SL Legacy hợp lệ (A)" when `status === 'VALID' && applied_variant === 'A'`.
  - Reads `estimated_net_rr` and `risk_usdt` directly from order snapshot.
  - Accordion displays authentic ATR method (`SMA_TR_V1` or "Chưa có dữ liệu ATR").
