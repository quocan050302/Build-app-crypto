# AURUM V17.5.7 — FINAL ACCEPTANCE REPORT
## Independent Protected Leg Derivation & True Cycle Cost Parity

**Release Version**: AURUM V17.5.7  
**Evaluation Date**: 2026-10-24 18:23:45 (Vietnam Local Time)  
**Target Repository**: `quocan050302/Build-app-crypto`  
**Target Branch**: `fix/v14-1-rr-flow`  
**Base Commit**: `d9a1c9afb929491b26417dc1d8ced2a780c57348`  
**Acceptance Verdict**: **ACCEPTED** (All 13 Gates Passed: 208 Backend Tests, 501 Vitest Unit Tests, Frontend Build, Frontend Lint, 5 Playwright Browser Tests)

---

### 1. Executive Summary

AURUM V17.5.7 definitively closes the two remaining technical gaps identified in the V17.5.6 review through production code enhancements and exhaustive verification without any strategy changes:

1. **Independent Protected Leg Derivation (Gap A)**:
   - `derive_protected_leg_episode` now invokes `resolve_episode_membership_from_structure` to independently derive displacement break episode boundaries directly from confirmed structure swings, reference nodes, and break bars.
   - It eliminates reliance on optional caller-injected `episode_start_ms`.
   - Multi-leg structural partitioning is strictly enforced: candidate pivots occurring at or after subsequent reference swings are rejected (`ORIGIN_AFTER_EPISODE_END`), preventing pivots from later legs from being misattributed to earlier reference breaks.
   - Origin candidates are verified against source candle extremes (High for SHORT, Low for LONG) and sorted deterministically.
   - `ProtectedLegEpisode` includes provenance metadata: `boundary_source="CONFIRMED_STRUCTURE_NODES"` and `derivation_version=1`.

2. **True Cycle Cost Parity (Gap B)**:
   - Cost parity is proven across the complete production cycle without synthetic `PaperOrder(state='armed')` seeding:
     `evaluate_cycle` -> `_arm_paper_candidate` -> persisted `PaperOrder` & `StopComparisonPlan` -> `ExecutionCoordinator.evaluate_orders_sync` fill.
   - In `scalp_m1_model_service.py`, `apply_stop_placement_to_candidate` passes `costs=costs_arg` to all `calculate_risk_reward` invocations, preventing the risk calculator from falling back to default 0.10 slippage.
   - In `plan_financial_context_service.py`, `resolve_execution_cost_context` prioritizes authoritative `settings` (`slippage_usd`, `maker_fee_rate`, `taker_fee_rate`, `costs`) over calculation fallback defaults.
   - In `scalp_watcher_orchestrator.py`, `evaluate_cycle` seeds `policy_dict["costs"] = CostAssumptions(...)` from effective system settings.
   - Tested and verified: custom slippage 0.27 (LONG fills at 2000.47, SHORT fills at 1999.73), explicit 0.0 slippage (LONG fills at 2000.20, SHORT fills at 2000.00), non-default maker/taker fee rates, and policy versioning.

---

### 2. Before vs. After Comparison Matrix

| Area | Baseline (V17.5.6) | V17.5.7 Implementation | Evidence / Test Gate |
|---|---|---|---|
| **Episode Boundary Derivation** | `derive_protected_leg_episode` required `selected_break` or `selected_reference` to supply `episode_start_ms`. Without it, boundary was `None` and pivot candidates were unconstrained. | `resolve_episode_membership_from_structure` derives `episode_start_ms = ref_occ` and detects subsequent reference swings to bound `episode_end_ms`. | `test_v17_5_7_episode_derivation.py` (P101-P110) |
| **Multi-Leg Origin Attribution** | Pivots occurring after a subsequent reference structure could be selected if their timestamp was before the break bar. | `validate_protected_origin_binding` rejects pivots occurring at or after `episode_end_ms` with `ORIGIN_AFTER_EPISODE_END`. | `test_p102_newer_pivot_in_subsequent_leg_is_excluded` |
| **Full-Cycle Cost Parity Evidence** | Cost parity suite created `PaperOrder(state='armed')` manually to test snapshot-to-fill, leaving `evaluate_cycle` and ARM wiring unproven. | True cycle test invokes `evaluate_cycle` directly with seeded settings; ARM creates the order; fresh DB session verifies snapshot parity; coordinator executes fill. | `test_v17_5_7_true_cycle_cost_parity.py` (P141-P150) |
| **Calculator Cost Attribution** | `apply_stop_placement_to_candidate` called `calculate_risk_reward` without `costs`, defaulting `CalculationResult.slippage_usd` to 0.10. | `apply_stop_placement_to_candidate` constructs `CostAssumptions` from `policy_settings` and passes `costs=costs_arg` to all 4 calculation calls. | `test_p141_true_cycle_long_custom_costs_match_comparison_order_and_fill` |
| **Cost Authority Hierarchy** | `resolve_execution_cost_context` prioritized `calc.slippage_usd` (0.10 fallback) over authoritative `settings["slippage_usd"]`. | Prioritizes authoritative `settings` first, preserving custom rates and explicit 0.0 slippage. | `test_p143_true_cycle_explicit_zero_slippage_is_preserved` |

---

### 3. Acceptance Execution Results

Executed via `tools/run_v17_5_7_acceptance.py`:

```
======================================================================
AURUM V17.5.7 ACCEPTANCE RESULT: ACCEPTED
Backend Tests: 208/208 passed across 9 gates (41 suites)
Frontend Vitest: 501 tests across 48 files passed
Frontend Build & Lint: PASS
Playwright Browser E2E: 5 tests passed
Artifact report saved: artifacts/v17_5_7/acceptance.json
======================================================================
```

#### Detailed Gate Breakdown

- **Gate 0 (V17.5.7 Core Targeted Suites)**: **PASS** (16 / 16 passed in 2.45s)
  - `test_v17_5_7_episode_derivation.py`: 11 passed
  - `test_v17_5_7_true_cycle_cost_parity.py`: 5 passed
- **Gate 1 (V17.5.6 Targeted Suites)**: **PASS** (37 / 37 passed in 1.58s)
- **Gate 2 (V17.5.5 Baseline Suites)**: **PASS** (23 / 23 passed in 1.20s)
- **Gate 3 (V17.5.4 Target Suites)**: **PASS** (16 / 16 passed in 1.44s)
- **Gate 4 (V17.5.3 Probes & Cycle Review)**: **PASS** (17 / 17 passed in 1.57s)
- **Gate 5 (V17.5.2 Baseline Suites)**: **PASS** (7 / 7 passed in 1.58s)
- **Gate 6 (V17.5.1 Feature Suites & Probes)**: **PASS** (26 / 26 passed in 2.75s)
- **Gate 7 (Existing V17.5 Regression Suites)**: **PASS** (31 / 31 passed in 1.77s)
- **Gate 8 (Prior Regression Suites)**: **PASS** (35 / 35 passed in 3.45s)
- **Gate 9 (Frontend Vitest Unit Tests)**: **PASS** (501 / 501 passed across 48 files in 3.70s)
- **Gate 10 (Frontend Build - `npm run build`)**: **PASS** (in 5.44s)
- **Gate 11 (Frontend Lint - `npm run lint`)**: **PASS** (0 errors in 0.32s)
- **Gate 12 (Playwright Browser Verification - `v17_5_6_chart_evidence.e2e.ts`)**: **PASS** (5 / 5 passed in 10.88s)

---

### 4. Artifacts and Audit Trail

- **Acceptance Metadata**: `artifacts/v17_5_7/acceptance.json`
- **JUnit Reports**:
  - `artifacts/v17_5_7/junit/gate0_v17_5_7_core.xml`
  - `artifacts/v17_5_7/junit/gate1_v17_5_6_core.xml`
  - `artifacts/v17_5_7/junit/gate2_v17_5_5_core.xml`
  - `artifacts/v17_5_7/junit/gate3_v17_5_4.xml`
  - `artifacts/v17_5_7/junit/gate4_v17_5_3.xml`
  - `artifacts/v17_5_7/junit/gate5_v17_5_2.xml`
  - `artifacts/v17_5_7/junit/gate6_v17_5_1.xml`
  - `artifacts/v17_5_7/junit/gate7_v17_5.xml`
  - `artifacts/v17_5_7/junit/gate8_prior.xml`
- **Execution Logs**: `artifacts/v17_5_7/logs/` (All 13 gate stdout/stderr outputs preserved)

---

### 5. Boundaries and Limitations

1. **Synthetic vs Empirical Reality**: All tests execute under simulated and deterministic market conditions (ReplayClock, in-memory SQLite). Real-market gold (XAUUSDT) spreads, liquidity gaps, and slippage during news events can deviate from static parameters.
2. **Empirical Profitability is UNPROVEN**: Verification of technical correctness (structural derivation and cost parity) does not imply or guarantee forward commercial profitability.
3. **No Strategy Drift**: No ATR parameters, reward thresholds, entry criteria, or risk controls (cap3/max1position) were altered. All invariants remain intact.
