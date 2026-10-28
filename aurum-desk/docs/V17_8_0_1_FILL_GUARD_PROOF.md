# AURUM DESK V17.8.0.1 — FILL-TIME PROTECTIVE DISTANCE & QUOTE GUARD PROOF (`DEF-178-09`)

**Defect ID:** `DEF-178-09`  
**Severity:** P0 (Release Blocking Execution Safety Defect)  
**Affected Files:**  
- `backend/services/scalp_watcher_orchestrator.py`
- `backend/services/order_plan_snapshot_service.py`
- `backend/services/entry_guard_service.py`
- `backend/services/execution_coordinator.py`
- `backend/tests/test_v17_8_runtime_integration.py`

---

## 1. Defect Description

In V17.8.0, `evaluate_entry_guard` (`backend/services/entry_guard_service.py`) checked `req.min_protective_distance` only when `req.min_protective_distance > 0`. However, at `FILL` time inside `ExecutionCoordinator.evaluate_orders_sync` (`backend/services/execution_coordinator.py`), `EntryGuardRequest` was constructed without passing `min_protective_distance` or `unicorn_evidence` from the armed order's persisted `cost_snapshot`. Consequently, `min_protective_distance` defaulted to `0.0` at `FILL` time, bypassing the quote-to-stop protective distance check when the market spread or quote drifted closer to the stop between `ARM` and `FILL`.

---

## 2. End-to-End ARM → Persisted Snapshot → FILL Guard Architecture

### 2.1 Persistence at ARM (`ScalpWatcherOrchestrator` & `order_plan_snapshot_service`)
When an order is armed in `ScalpWatcherOrchestrator._evaluate_and_arm`, the frozen `cost_snapshot` JSON persisted on `PaperOrder` now stores:
- `min_protective_distance`: authoritative `buffer.buffer_price` (`max(min_buffer_ticks * tick_size, atr_m1_component, atr_m5_component, spread_component)`)
- `unicorn_evidence`: serialized `UnicornEvidence.to_dict()` (if present)
- `stop_mode_at_arm`: active `StopPlacementMode` at ARM
- `applied_variant`: `"A"` or `"C"`
- `v18_structural_intelligence_enabled`, `v18_unicorn_detection_enabled`, `v18_noise_research_enabled`, `v18_execution_policy_version`

### 2.2 Restoration & Enforcement at FILL (`ExecutionCoordinator.evaluate_orders_sync`)
Before calling `TradeLifecycleService.execute_fill`, `ExecutionCoordinator.evaluate_orders_sync` parses `order.cost_snapshot`, extracts `min_protective_distance` and `unicorn_evidence`, and passes them into `EntryGuardRequest(stage="FILL", ...)`.

### 2.3 Fail-Closed Guard Rules (`evaluate_entry_guard` in `entry_guard_service.py`)
1. **Quote Validity & Freshness:** Missing, non-finite, or stale bid/ask quotes fail closed with `STALE_QUOTE` / `INVALID_QUOTE`.
2. **Pre-FILL Stop Breach Guard (`INVALID_GEOMETRY_STOP_BREACHED`):**
   - For `SHORT`: if `executable_ask >= stop_loss`, reject fill immediately (`INVALID_GEOMETRY_STOP_BREACHED`).
   - For `LONG`: if `executable_bid <= stop_loss`, reject fill immediately (`INVALID_GEOMETRY_STOP_BREACHED`).
3. **Missing / Invalid Protective Distance Fail-Closed (`MISSING_PROTECTIVE_DISTANCE` / `INVALID_PROTECTIVE_DISTANCE`):**
   - When `require_protective_distance=True` (active for V17.8 structural/Unicorn orders), a `None`, non-finite, or `<= 0` `min_protective_distance` fails closed with `MISSING_PROTECTIVE_DISTANCE` or `INVALID_PROTECTIVE_DISTANCE`.
4. **Pre-FILL Minimum Protective Distance Guard (`STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE`):**
   - For `SHORT`: `actual_stop_dist = stop_loss - executable_ask`. If `actual_stop_dist < min_protective_distance`, reject fill with `STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE`.
   - For `LONG`: `actual_stop_dist = executable_bid - stop_loss`. If `actual_stop_dist < min_protective_distance`, reject fill with `STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE`.
5. **Unicorn Invalidation Guard (`UNICORN_INVALIDATED_AT_FILL`):**
   - If the armed order carries confirmed `unicorn_evidence` with `invalidation_price`, and the executable quote at fill time has already breached `invalidation_price`, reject fill with `UNICORN_INVALIDATED_AT_FILL`.

---

## 3. Verification Evidence

- `test_def_178_09_fill_guard_protective_distance_and_breached_quote` (`backend/tests/test_v17_8_runtime_integration.py`):
  - Verifies LONG and SHORT orders armed through the real orchestrator persist `min_protective_distance` in `PaperOrder.cost_snapshot`.
  - Verifies `ExecutionCoordinator.evaluate_orders_sync` blocks fill when executable Bid/Ask is within `min_protective_distance` (`STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE`), blocks fill when executable Bid/Ask breaches SL (`INVALID_GEOMETRY_STOP_BREACHED`), blocks when `require_protective_distance=True` and `min_protective_distance` is missing (`MISSING_PROTECTIVE_DISTANCE`), and fills cleanly when executable quote distance is >= `min_protective_distance`.

