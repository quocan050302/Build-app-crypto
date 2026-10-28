# AURUM DESK V17.7 — FUNCTION CHANGE & CALL GRAPH MAP
**Author:** Principal Quant Trading Systems Engineer & Release Architect
**Scope:** Source Map, Call Hierarchy, Input/Output Contracts & Test Traces
**Date:** October 2026

---

## 1. Function Modification Matrix

| File Path | Function / Symbol | Change Type | Input Contract | Output Contract | Direct Callers | Covered Tests |
|---|---|---|---|---|---|---|
| `backend/services/stop_placement_types.py` | `StopProtectiveDistanceResult` | **NEW** | `min_distance, components, dominant_source, tick_size` | Frozen dataclass | `compute_min_protective_distance` | SL-19, SL-60 |
| `backend/services/stop_placement_types.py` | `StopPlacementPolicy` | **MODIFIED** (Additive) | Added `min_protective_ticks`, `min_distance_atr_m1_mult`, `min_distance_atr_m5_mult`, `min_distance_spread_mult`, `max_stop_distance_atr`, `max_spread_usd`, `algorithm_version` | Frozen dataclass | `stop_placement_policy_service`, `stop_placement_service` | SL-33, SL-34, SL-59 |
| `backend/services/stop_placement_types.py` | `StopPlacementDecision` | **MODIFIED** (Additive) | Added `protective_distance`, `expansion_applied`, `algorithm_version` | Frozen dataclass | `plan_structural_stop` | SL-19, SL-51, SL-55 |
| `backend/services/stop_placement_service.py` | `compute_min_protective_distance` | **NEW** | `policy, tick_size, atr_m1, atr_m5, spread, atr_m1_val, atr_m5_val` | `StopProtectiveDistanceResult` | `plan_structural_stop` | SL-18, SL-19, SL-60 |
| `backend/services/stop_placement_service.py` | `plan_structural_stop` | **MODIFIED** | Added finiteness/positivity validation, spread checks, protective distance expansion, maximum stop distance guard | `StopPlacementDecision` | `scalp_m1_model_service`, `cg01_engine` | SL-01..SL-04, SL-11..SL-20, SL-29..SL-32, SL-35, SL-54 |
| `backend/services/stop_placement_policy_service.py` | `resolve_stop_placement_policy` | **MODIFIED** | `db: Session` | `StopPlacementPolicy` with V17.7 fields | `main.py`, `scalp_watcher_orchestrator`, `cg01_engine` | SL-33, SL-34, SL-59 |
| `backend/services/stop_placement_policy_service.py` | `validate_stop_placement_policy` | **MODIFIED** | `payload: Dict[str, Any], current: StopPlacementPolicy` | Validated dict | `update_stop_placement_policy` | SL-33, SL-34 |
| `backend/services/stop_placement_policy_service.py` | `update_stop_placement_policy` | **MODIFIED** | `db, payload, expected_version` | `Tuple[StopPlacementPolicy, bool]` | `main.py` PUT endpoint | SL-33, SL-34, test_v17_5_1_policy_cas |
| `backend/services/stop_placement_research_service.py` | `build_stop_comparison_plan` | **MODIFIED** | `policy_settings.get("min_buffer_ticks", 5.0)` for Variant B | `StopComparisonPlan` | `cg01_engine`, `scalp_m1_model_service` | SL-57, test_v17_5_stop_research |
| `backend/services/scalp_m1_model_service.py` | `apply_stop_placement_to_candidate` | **MODIFIED** | Passes `atr_m5_val` and `spread` to `plan_structural_stop` | `StopApplicationResult` | M1 evaluators | SL-37..SL-40 |
| `backend/services/cg01_engine.py` | `evaluate_cg01_candidate` | **MODIFIED** | Passes `atr_m5_val` and `spread` to `plan_structural_stop`; fail-closed `sl = stop_dec.selected_sl` in PAPER_STRUCTURAL | `CG01Decision` | Orchestrator, Scheduler | SL-36, SL-60 |
| `backend/schemas.py` | `StopPlacementPolicyResponse` & `StopPlacementPolicyUpdate` | **MODIFIED** (Additive) | Added V17.7 fields | Pydantic Schema | FastAPI routers | SL-59 |
| `backend/main.py` | `GET /api/v1/policy/stop-placement` | **MODIFIED** (Additive) | Response serialization of new fields | JSON dict | Frontend | SL-59 |
| `backend/main.py` | `PUT /api/v1/policy/stop-placement` | **MODIFIED** (Additive) | Validation and update of new fields | JSON dict | Frontend | SL-59 |
| `backend/main.py` | `GET /api/v1/orders/{order_id}/stop-evidence` | **MODIFIED** (Additive) | Added `algorithm_version`, `min_protective_distance`, `protective_expansion_applied`, `ui_explanation_vi` | JSON dict | Frontend Drawer | SL-56 |

---

## 2. Real Call Graph Trace

```text
Market Feed (Ticks & Closed Candles)
       │
       ▼
Closed Candle Stream (1M / 5M)
       │
       ├─► compute_stop_atr(closed_bars, period=14, timeframe="1M") ──► StopATRResult (M1)
       ├─► compute_stop_atr(closed_bars, period=14, timeframe="5M") ──► StopATRResult (M5)
       │
       ▼
SMC Strategy Model Evaluation (CG01 / Reversal / Continuation / FVG / Sweep Reclaim)
       │
       ├─► resolve_stop_anchor(model_id, direction, closed_bars) ──► StopAnchorResult
       ├─► compute_stop_buffer(policy, tick_size, atr_m1, atr_m5, spread) ──► StopBufferResult
       │
       ▼
plan_structural_stop(direction, planned_entry, take_profit, legacy_sl, anchor, buffer, policy, ...)
       │
       ├─► compute_min_protective_distance(policy, atr_m1, atr_m5, spread) ──► StopProtectiveDistanceResult
       ├─► Outward Expansion Check (anchor distance vs min_protective_distance)
       ├─► Maximum Stop Distance Guard (distance <= 4.0 * ATR_M1)
       ├─► Spread Shock Guard (spread <= max_spread_usd)
       ├─► quantize_stop_outward(anchor/entry, buffer/distance, direction, tick_size)
       │
       ▼
StopPlacementDecision (status: VALID, selected_sl, applied_variant: "C", protective_distance)
       │
       ▼
calculate_risk_reward(direction, planned_entry, stop_loss=selected_sl, take_profit, min_net_rr, ...)
       │
       ├─► Position Sizing (Quantity scaled down inversely with wider stop distance)
       ├─► Net R:R Invariant Check (Net R:R >= min_net_rr; rejects if stop too wide for target)
       │
       ▼
build_stop_comparison_plan(Variants A, B, C evaluated on identical entry/target)
       │
       ▼
ScalpCandidate (candidate.stop_loss == calculation.stop_loss == stop_placement.selected_sl)
       │
       ▼
Watcher Orchestrator (evaluate_cycle -> _arm_paper_candidate)
       │
       ├─► validate_applied_stop_decision (arm barrier verifying candidate SL == decision SL)
       ├─► arm_snap freezing (stop_placement, algorithm_version="V17_7", immutable stop_loss)
       │
       ▼
PaperOrder (state: "armed", stop_loss: immutable)
       │
       ▼
Execution Coordinator (evaluate_orders_sync)
       │
       ├─► Fill-time Risk & Slippage Re-check
       ├─► Quantity Preservation / Downsize Rule (never increase armed quantity)
       │
       ▼
PaperBroker / Shared Exit Evaluator (evaluate_order_exit)
       │
       ├─► Bid/Ask Stop Loss Triggering (gap pricing, no suppression)
       ├─► Realized PnL Calculation & Observation Recording
```

---

## 3. Downstream Consumers & Contracts Preserved

1. **Frontend TradeDetailsDrawer:** Consumes `/api/v1/orders/{order_id}/stop-evidence`. The response format is 100% backward-compatible, with additive fields (`min_protective_distance`, `protective_expansion_applied`, `ui_explanation_vi`).
2. **Frontend Settings Modal:** Consumes `/api/v1/policy/stop-placement`. The GET and PUT contracts preserve all existing keys and add validated configuration fields.
3. **Observation & Journal Exporters:** All fields written to `PaperObservation` use standard serializable JSON dictionaries.
