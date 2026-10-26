# AURUM DESK V17.7.1 — FUNCTION CHANGE MAP
**Role:** Senior Python Architect & Quant Systems Auditor  
**Date:** October 2026  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Version:** V17.7.1  

---

## 1. Function Change Map

### 1.1 `plan_structural_stop`
- **File:** `backend/services/stop_placement_service.py`
- **Existing Inputs:** `direction`, `planned_entry`, `take_profit`, `legacy_sl`, `anchor_result`, `buffer_result`, `policy`, `as_of_ms`, `tick_size`, `atr_m1_obj`, `atr_m5_obj`, `eff_spread`, `atr_m1_val`, `atr_m5_val`, `policy_ver`, `mode`, `algo_ver`.
- **Existing Outputs:** `StopPlacementDecision` object.
- **Callers:** `cg01_engine.py`, `scalp_m1_model_service.py`, `stop_placement_research_service.py`, `scalp_watcher_orchestrator.py`.
- **Downstream Consumers:** Candidate generator, ARM validator, execution coordinator, paper broker, frontend API.
- **New Behavior:**
  1. Removed hard rejection when `final_dist > max_allowed_dist` (`4.0 * atr_m1`).
  2. Added non-blocking distance classification (`DISTANCE_NORMAL`, `DISTANCE_ELEVATED`, `DISTANCE_EXTREME`, `DISTANCE_ATR_UNAVAILABLE`) in `StopPlacementDecision.distance_assessment`.
  3. Added comprehensive diagnostic fields to `vol_ctx`: `stop_distance_price`, `stop_distance_ticks`, `stop_distance_atr_m1`, `stop_distance_atr_m5`, `structural_anchor_id`, `structural_anchor_kind`, `protected_episode_id`, `anchor_invalidation_evidence`, `distance_assessment`.
  4. Safe attribute resolution (`getattr`) on `anchor` object to support duck-typed/mock anchor models in regression suites.
  5. Default `algorithm_version = "V17_7_1"`.
- **Preserved Behavior:**
  - Preserved directional price geometry checks (`variant_c_sl < planned_entry < take_profit` for LONG).
  - Preserved outward tick quantization (`quantize_stop_outward`).
  - Preserved dynamic volatility buffer calculation and protective distance expansion (`min_protective_distance`).
  - Preserved mode parity (`LEGACY`, `SHADOW_COMPARE`, `PAPER_STRUCTURAL`).
  - Preserved spread verification and `max_spread_usd` shock barrier.
- **Regression Tests:** `SL-01` to `SL-34`, `SL-53` to `SL-55`, `DSL-01` to `DSL-20`, `test_v17_5_model_integration.py`.

---

### 1.2 `StopPlacementDecision` & `StopPlacementPolicy` Serialization
- **File:** `backend/services/stop_placement_types.py`
- **Existing Inputs:** Dataclass constructor parameters.
- **Existing Outputs:** Dataclass instance, `.to_dict()` dictionary.
- **Callers:** `stop_placement_service.py`, `stop_placement_policy_service.py`, `main.py`.
- **Downstream Consumers:** Database `cost_snapshot` storage, REST API JSON responses (`/api/v1/policy/stop-placement`, `/api/v1/orders/{id}/stop-evidence`).
- **New Behavior:**
  - Added `distance_assessment: Optional[str] = "DISTANCE_NORMAL"` to `StopPlacementDecision`.
  - Updated default `algorithm_version` to `"V17_7_1"`.
  - Included `distance_assessment` in `.to_dict()` serialization.
- **Preserved Behavior:**
  - Retained `max_stop_distance_atr` in `StopPlacementPolicy` for schema backward compatibility (non-blocking diagnostic threshold).
  - Preserved all existing serialization keys (`mode`, `version`, `min_buffer_ticks`, `spread_multiplier`, etc.).
- **Regression Tests:** `SL-33`, `SL-59`, `DSL-08`, `DSL-17`.

---

### 1.3 `evaluate_cg01_candidate` (ATR Fallback Cleanup)
- **File:** `backend/services/cg01_engine.py`
- **Existing Inputs:** `candles_1m`, `candles_5m`, `current_quote`, `now_ms`, etc.
- **Existing Outputs:** CG01 candidate dict or ineligible dict.
- **Callers:** `scalp_watcher_orchestrator.py`, `cg01_scheduler_service.py`.
- **Downstream Consumers:** ARM pipeline, execution coordinator.
- **New Behavior:**
  - Eliminated hardcoded `1.0` fallback for `atr_m1_val` when neither `atr_m1_obj` nor `atr_series` is available. Sets to `None` so missing volatility evidence fails closed cleanly.
- **Preserved Behavior:**
  - Preserved sweep detection, MSS confirmation, FVG generation, entry pricing, target selection.
- **Regression Tests:** `SL-36`, `DSL-10`, `eval_cg01_v16_27_baseline.py`.

---

### 1.4 `get_order_stop_evidence_endpoint`
- **File:** `backend/main.py`
- **Existing Inputs:** `order_id: str`, `db: Session`.
- **Existing Outputs:** Dict with stop placement evidence.
- **Callers:** Frontend TradeDetailsDrawer, research analytics.
- **New Behavior:**
  - Fallback `algorithm_version` updated to `"V17_7_1"`.
  - Robust extraction of `stop_placement` from `order.cost_snapshot`.
- **Preserved Behavior:**
  - Returns `UNAVAILABLE_LEGACY` for legacy orders without cost snapshots.
  - Returns complete structural evidence when available.
- **Regression Tests:** `SL-55`, `SL-56`, `DSL-08`.

---

### 1.5 Acceptance Master Runner (`run_v17_7_acceptance.py`)
- **File:** `tools/run_v17_7_acceptance.py`
- **New Behavior:**
  - Rewritten from scratch to execute authentic matrix: dedicated V17.7.1 suite (80 gates), V17.5 regression (278 tests), V17.6 regression (776 tests), frontend Vitest (548 tests), frontend build and lint.
  - Dynamically parses real pytest and vitest outputs for collected, passed, failed, and duration.
  - Instruments actual outbound network boundaries with active spy to assert zero live exchange calls.
  - Enforces strict git branch check (`feat/v17-7-adaptive-structural-stop-loss`).
  - Verifies presence and completeness of all V17.7.1 documentation files.
  - Only outputs `ACCEPTED_FOR_PAPER_OPT_IN` when every gate passes.
- **Regression Tests:** Executed directly via CLI.
