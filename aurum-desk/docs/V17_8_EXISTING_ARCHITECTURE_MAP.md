# AURUM DESK V17.8 — EXISTING ARCHITECTURE REUSE & INTEGRATION MAP

## 1. Overview
In accordance with Section 3 of the Master Execution Directive, Aurum Desk V17.8 reuses all verified production infrastructure from V17.7 and prior releases rather than creating parallel or duplicative systems.

---

## 2. Component Integration Matrix

### 2.1 Pattern Recognition & SMC Signals
- **Reused Component:** `backend/services/smc_liquidity_service.py` & `backend/services/scalp_m1_model_service.py`
  - *Reused Logic:* Swing detection (`confirmed_swings`), liquidity sweep identification, market structure shift (MSS/BOS), and fair value gap (FVG) detection on closed candles.
  - *New Logic in V17.8:* ICT Breaker Block identification and confluence engine (`backend/services/unicorn_evidence_service.py`).
  - *Integration Point:* `_evaluate_fvg_continuation_model` in `scalp_m1_model_service.py` checks for `detect_unicorn_evidence` and attaches `unicorn_evidence` to the candidate.

### 2.2 Stop Placement Architecture
- **Reused Component:** `backend/services/stop_placement_service.py`, `backend/services/stop_placement_types.py`, and `backend/services/stop_placement_policy_service.py`
  - *Reused Logic:* Dynamic buffer computation (`compute_dynamic_stop_buffer`), tick quantization (`quantize_stop_outward`), minimum protective distance enforcement (`compute_minimum_protective_distance`), and raw ATR provenance validation (`validate_stop_atr_authority`).
  - *New Logic in V17.8:* `resolve_stop_anchor` updated to support `StopAnchorKind.BREAKER_DISTAL_BOUNDARY` via `unicorn_evidence`, and displacement origin fallback for FVG continuation when bound leg is invalidated.
  - *Integration Point:* `plan_structural_stop` generates `unicorn_sl_candidate`, sets `invalidation_authority`, and populates `StopPlacementDecision`.

### 2.3 Financial Calculation & Risk Sizing
- **Reused Component:** `backend/domain_calculator.py`
  - *Reused Logic:* Sole financial authority for all position sizing, Net R:R, exchange fee models, slippage deductions, and liquidation threshold calculations.
  - *New Logic in V17.8:* Zero changes to `domain_calculator.py`. All structural stops use `calculate_risk_reward()` with strict risk budget ($2.50 USDT @ 20x) and downscale quantity as stop distance expands.

### 2.4 Entry Guard & Noise Protection
- **Reused Component:** `backend/services/entry_guard_service.py`
  - *Reused Logic:* Multi-stage lifecycle gating (ARM, FILL), daily bias authority validation, session limits, news blackout detection, and account equity checks.
  - *New Logic in V17.8:* Added `min_protective_distance` and `unicorn_evidence` to `EntryGuardRequest`. Implemented executable Ask (SHORT) and Bid (LONG) distance checks and Unicorn invalidation checks in `_check_geometry`.

### 2.5 Execution & Paper Lifecycle
- **Reused Component:** `backend/services/execution_coordinator.py`, `backend/services/trade_lifecycle_service.py`, `backend/paper_broker.py`, and `backend/services/shared_exit_evaluator.py`
  - *Reused Logic:* PAPER state machine (`armed` -> `paper_open` -> `closed`), immutable snapshot persistence (`arm_decision_snapshot`, `fill_decision_snapshot`), and exit evaluations on tick/candle prices.
  - *New Logic in V17.8:* Decision snapshots persist `unicorn_id`, `breaker_id`, `invalidation_authority`, and comparison plans.

### 2.6 Persistence & API Layer
- **Reused Component:** `backend/models.py`, `backend/database.py`, and `backend/main.py`
  - *Reused Logic:* SQLite test database isolation, audit logs, paper order tables.
  - *New Logic in V17.8:* Exposed `/api/v1/orders/{order_id}/stop-evidence` and `/api/v1/research/stop-placement/replay-comparison`.
  - *Frontend:* Added `frontend/src/components/SLTPIntelligenceView.tsx` into the dashboard.

---

## 3. Strict Safety Boundaries
- No LIVE trading code exists or can be invoked.
- Test DB isolation asserts `assert_test_database_isolated()` before every run.
- Zero mutations to existing database schema migrations.
