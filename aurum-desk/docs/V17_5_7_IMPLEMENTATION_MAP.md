# AURUM V17.5.7 — IMPLEMENTATION MAP
## Independent Protected Leg Derivation & True Cycle Cost Parity

**Release Target**: AURUM V17.5.7  
**Base Commit**: `d9a1c9afb929491b26417dc1d8ced2a780c57348`  
**Branch**: `fix/v14-1-rr-flow`  
**Execution Timestamp**: 2026-10-24 18:23:45 (Vietnam Time)  
**Overall Verdict**: **ACCEPTED** (All 13 Gates Passed: 208 Backend Tests across 41 suites, 501 Vitest Unit Tests across 48 suites, Frontend Build, Frontend Lint, 5 Playwright Browser Tests)

---

### 1. Scope & Architectural Boundaries

This patch strictly closes the two remaining technical gaps without strategy expansion, heuristic score alterations, or relaxing risk invariants:
- **Gap A: Independent Protected Leg Episode Derivation**: `derive_protected_leg_episode` derives episode boundaries directly from structural reference, swing, and break nodes via `resolve_episode_membership_from_structure`. It no longer depends on optional caller-injected `episode_start_ms`. Multi-leg structures are strictly partitioned: if a newer reference swing exists between the selected reference and the break bar, candidate pivots occurring at or after that newer reference are excluded (`ORIGIN_AFTER_EPISODE_END`). Origin candidates are verified against source candle extremes and sorted deterministically by directional extreme (highest High for SHORT, lowest Low for LONG).
- **Gap B: True Cycle Cost Parity**: Proves complete cost authority from actual `evaluate_cycle` through ARM, persisted snapshot, and `ExecutionCoordinator.evaluate_orders_sync` fill, without manually seeding `PaperOrder(state='armed')`. In `scalp_m1_model_service.py`, `apply_stop_placement_to_candidate` passes `costs=costs_arg` to all `calculate_risk_reward` calls. In `plan_financial_context_service.py`, `resolve_execution_cost_context` prioritizes authoritative settings over calculator fallback. In `scalp_watcher_orchestrator.py`, `evaluate_cycle` provisions `policy_dict["costs"] = CostAssumptions(...)` matching effective system settings.

---

### 2. Exact Function & Caller Implementation Map

#### Gap A: Independent Protected Leg Episode Derivation

| Function / Symbol | File | Role & Modifications | Callers | Key Inputs / Outputs | Associated Tests |
|---|---|---|---|---|---|
| `ProtectedLegEpisode` | `backend/services/stop_placement_types.py` | Added additive fields `boundary_source: Optional[str] = "CONFIRMED_STRUCTURE_NODES"` and `derivation_version: int = 1`. Updated `.to_dict()` and `from_dict()` for full serialization parity. | `derive_protected_leg_episode`, `select_protected_origin_for_break` | In: fields<br>Out: dataclass instance with serialization | `test_v17_5_7_episode_derivation.py` (P101, P108, P120) |
| `resolve_episode_membership_from_structure` | `backend/services/stop_placement_service.py` | Resolves `ref_occ` from reference object, swings, or closed bars. Detects subsequent reference nodes to bound `episode_end_ms = min(b_open_ms, next_ref_occ)`. Returns status, boundaries, reason, and derivation version. | `derive_protected_leg_episode` | In: `model_id`, `direction`, `selected_reference`, `selected_break`, `structure_nodes`, `closed_bars`, `decision_cutoff_ms`<br>Out: dict with `status`, `episode_start_ms`, `episode_end_ms`, `reasons`, `derivation_version` | `test_v17_5_7_episode_derivation.py` (P101, P102, P103, P105) |
| `validate_protected_origin_binding` | `backend/services/stop_placement_service.py` | Added optional `episode_end_ms: Optional[int] = None`. Enforces: if `episode_end_ms is not None` and `p_occ >= episode_end_ms`, rejects with `ORIGIN_AFTER_EPISODE_END`. Preserves existing source extreme and timestamp checks. | `derive_protected_leg_episode`, `select_protected_origin_for_break` | In: origin, direction, break_open_ms, closed_bars, tick_size, episode_start_ms, episode_end_ms<br>Out: `Tuple[bool, Optional[str]]` | `test_v17_5_7_episode_derivation.py` (P102, P111-P119) |
| `derive_protected_leg_episode` | `backend/services/stop_placement_service.py` | Invokes `resolve_episode_membership_from_structure` when `episode_start_ms` is not supplied. Verifies origin candidates are within boundaries, pass directional extreme check (`price > ref_price` for SHORT, `price < ref_price` for LONG), and sorts deterministically by directional extreme price. | `select_protected_origin_for_break` | In: `model_id`, `direction`, `selected_reference`, `selected_break`, `confirmed_swings`, `closed_bars`, `decision_cutoff_ms`<br>Out: `ProtectedLegEpisode` | `test_v17_5_7_episode_derivation.py` (P101-P110) |
| `evaluate_m1_fvg_continuation_candidate` | `backend/services/scalp_m1_model_service.py` | Updated to pass full `swing_ref` dictionary (including `occurred_at_ms`, `known_at_ms`, `source`) into descriptor instead of truncated `{"id": ..., "price": ...}`. | Model evaluation loop | In: candles, quotes, policy settings<br>Out: `CandidateEvaluationResult` | `test_v17_5_7_episode_derivation.py` (P110) |

#### Gap B: True Cycle Cost Parity

| Function / Symbol | File | Role & Modifications | Callers | Key Inputs / Outputs | Associated Tests |
|---|---|---|---|---|---|
| `evaluate_cycle` | `backend/services/scalp_watcher_orchestrator.py` | Reads effective cost settings (`slippage_usd`, `maker_fee_rate`, `taker_fee_rate`) from `execution_financial_context` or `models.SystemConfig` into `policy_dict`, and sets `policy_dict["costs"] = CostAssumptions(...)`. Ensures calculator, stop comparison, and ARM all consume identical costs. | Watcher loop, cycle integration tests | In: db, symbol, now_ms, clock, quote, candles<br>Out: `CycleEvaluationResult` | `test_v17_5_7_true_cycle_cost_parity.py` (P141-P150) |
| `apply_stop_placement_to_candidate` | `backend/services/scalp_m1_model_service.py` | Extracts `costs_arg = CostAssumptions(...)` from `policy_settings` and passes `costs=costs_arg` to all 4 invocations of `calculate_risk_reward`. Prevents `CalculationResult` from falling back to default 0.10 slippage. | Model evaluators | In: candidate, model_id, candles, settings<br>Out: updated candidate with stop placement & calculation | `test_v17_5_7_true_cycle_cost_parity.py` (P141-P144) |
| `resolve_execution_cost_context` | `backend/services/plan_financial_context_service.py` | Prioritizes authoritative `settings` (`st["slippage_usd"]`, `st["maker_fee_rate"]`, `st["taker_fee_rate"]`, `st["costs"]`) over calculation fallback defaults. Normalizes explicit 0.0 slippage and non-default fee rates. | `build_arm_plan_snapshot`, `_arm_paper_candidate`, `ExecutionCoordinator.evaluate_orders_sync` | In: candidate, snapshot, settings, config, as_of_ms<br>Out: authoritative cost context dict | `test_v17_5_7_true_cycle_cost_parity.py` (P141-P144, P151-P160) |
| `ExecutionCoordinator.evaluate_orders_sync` | `backend/services/execution_coordinator.py` | Executes market fills using frozen `order_cost_snap` resolved via `resolve_execution_cost_context`. Uses exact fill math: Bid 2000.00 / Ask 2000.20 + slippage 0.27 -> LONG 2000.47, SHORT 1999.73. Explicit 0.0 -> LONG 2000.20, SHORT 2000.00. | Execution worker, cycle runner | In: db, ticker_override, clock, now_ms<br>Out: fill results | `test_v17_5_7_true_cycle_cost_parity.py` (P141-P150) |

---

### 3. Verification Artifacts & Test Coverage Matrix

| Test Suite File | Type | Gate | Pass Count | Status |
|---|---|---|---|---|
| `backend/tests/test_v17_5_7_episode_derivation.py` | Pytest | Gate 0 (Parts 101–140) | 11 / 11 | PASS |
| `backend/tests/test_v17_5_7_true_cycle_cost_parity.py` | Pytest | Gate 0 (Parts 141–180) | 5 / 5 | PASS |
| `backend/tests/test_v17_5_6_actual_order_cost_parity.py` | Pytest | Gate 1 | 12 / 12 | PASS |
| `backend/tests/test_v17_5_6_episode_origin_binding.py` | Pytest | Gate 1 | 11 / 11 | PASS |
| `backend/tests/test_v17_5_6_break_time_contract.py` | Pytest | Gate 1 | 8 / 8 | PASS |
| `backend/tests/test_v17_5_6_cycle_regression.py` | Pytest | Gate 1 | 6 / 6 | PASS |
| Baseline Suites (Gates 2–8) | Pytest | 35 Suites (V17.5.5 down to baseline) | 155 / 155 | PASS |
| **Total Backend Tests** | **Pytest** | **Gates 0–8 (41 Suites)** | **208 / 208** | **PASS** |
| Frontend Unit Tests | Vitest | Gate 9 (48 Suites) | 501 / 501 | PASS |
| Frontend Build & Lint | TypeScript / ESLint / Vite | Gates 10 & 11 | 0 errors | PASS |
| `frontend/tests/e2e/v17_5_6_chart_evidence.e2e.ts` | Playwright E2E | Gate 12 (Browser Geometry) | 5 / 5 | PASS |

---

### 4. Invariant Protection Summary

1. **Deterministic Execution**: No live DB or real Telegram calls in tests. All tests use in-memory SQLite and ReplayClock.
2. **Order Immutability**: Existing armed, open, or closed orders retain their frozen cost and stop loss snapshots.
3. **No Strategy Drift**: No ATR parameters, reward ratios, or entry filters were loosened to pass tests. Risk invariants (cap3/max1position) remain enforced.
4. **Empirical Note**: Passing technical tests proves causal and geometric correctness under paper trading assumptions. Empirical profitability on live markets remains **UNPROVEN**.
