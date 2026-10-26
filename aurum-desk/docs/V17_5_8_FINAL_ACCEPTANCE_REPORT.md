# AURUM V17.5.8 — FINAL ACCEPTANCE REPORT
## Structural Provenance, Execution Safety, Cost Parity & Reproducible Acceptance

**Release Version**: AURUM V17.5.8
**Evaluation Date**: 2026-10-24 22:21:40 (Vietnam Local Time)
**Target Repository**: `quocan050302/Build-app-crypto`
**Target Branch**: `fix/v14-1-rr-flow`
**Baseline Commit**: `90fe2e368083476c303393522943c243db0b8f74`
**Acceptance Verdict**: **ACCEPTED** (All 14 Gates Passed: 242 Backend Tests, 501 Vitest Unit Tests, Frontend Build, Frontend Lint, 6 Playwright Browser Tests)

---

### 1. Executive Summary

AURUM V17.5.8 hardens the quantitative trading execution system by closing all remaining correctness, provenance, execution safety, and cost parity gaps without altering core strategy parameters or weakening risk rules:

1. **Structural Provenance & Episode Authority (Phases B, C, D)**:
   - `resolve_verified_reference_node`: Enforces source swing/candle existence, eliminates ambiguous price-only matches (`AMBIGUOUS_REFERENCE_PRICE`), rejects future-known references (`REFERENCE_KNOWN_AFTER_DECISION`), and validates timestamp causality (`occurred <= known <= cutoff`).
   - `resolve_verified_break_candle`: Eliminates permissive `except Exception: pass` paths, verifies break candle presence in `closed_bars`, enforces valid finite OHLC geometry, and enforces strict close-through (`close < ref` for SHORT, `close > ref` for LONG).
   - `validate_episode_interval` & `validate_protected_origin_binding`: Strictly enforces episode membership `[episode_start_ms, episode_end_ms)`. Multi-leg boundary isolation excludes candidate pivots from subsequent legs (`ORIGIN_AFTER_EPISODE_END`). Directional selection picks the highest swing High for SHORT and lowest swing Low for LONG, resilient to lookback noise.
   - Caller boundary authority is completely removed: arbitrary caller-supplied `episode_start_ms` cannot redefine derived structural intervals.

2. **Financial Cost Authority & Full Cycle ARM Safety (Phases E, F, G)**:
   - `build_verified_cost_context`: Implements single authoritative cost resolution prioritizing frozen order snapshots, strictly validating finite non-negative numbers, and preserving explicit `0.0` slippage.
   - `get_system_config_cost_value`: Replaces ambiguous `.first()` queries with deterministic key precedence (`["slippage_usd", "slippage", "default_slippage"]`, etc.).
   - `_arm_paper_candidate`: Enforces fail-closed cost check (rejects candidate with `COST_CONTEXT_UNAVAILABLE` if cost context is unavailable).
   - `validate_arm_invariants`: Pure ARM validator enforcing quantity > 0, stop plan consistency, daily cap, quote freshness, trigger expiry, and LONG/SHORT geometry.
   - `assert_order_financial_parity`: Reconciles slippage and fees across candidate calculation, comparison plan, frozen order, and fill result.
   - Full cycle verification: Autonomous execution from `evaluate_cycle` -> ARM -> `PaperOrder(state='armed')` -> `ExecutionCoordinator` fill -> `paper_open` verified on isolated database fixtures without synthetic order seeding.

3. **Frontend Chart Evidence & Reproducible Provenance (Phases H, J)**:
   - Playwright E2E test `P06` in `frontend/tests/e2e/v17_5_6_chart_evidence.e2e.ts` verifies persisted financial parity on focused chart orders without UI drift.
   - Acceptance runner `tools/run_v17_5_8_acceptance.py` dynamically parses real JUnit XML and Vitest outputs, verifies return codes, computes SHA-256 fingerprints of all critical source files, and produces structured artifacts in `artifacts/v17_5_8/`.

---

### 2. Confirmed Root Causes & Changes Summary

| Subsystem | Root Cause Confirmed | Remediation Applied |
|---|---|---|
| Break Close-Through Validation | Permissive `except Exception: pass` around candle OHLC parsing allowed unverified or malformed bars to slip through. | Replaced with `resolve_verified_break_candle` which verifies source candle in `closed_bars`, checks OHLC geometry, and fails closed with `BREAK_CANDLE_INVALID`. |
| Ambiguous Reference Matches | Lookup by price alone could match multiple swings with equal prices at different times, silently picking the first. | `resolve_verified_reference_node` detects ambiguous matches and returns `AMBIGUOUS_REFERENCE_PRICE`. |
| Caller Boundary Authority | `resolve_episode_membership_from_structure` allowed caller-injected `episode_start_ms` to redefine derived structure if the candle existed. | Caller authority was completely removed; episode start strictly resolves from verified reference occurrence. |
| Cost Setting Key Ambiguity | `.filter(key.in_(...)).first()` on `models.SystemConfig` produced ambiguous results depending on database row scan order. | Extracted `get_system_config_cost_value` evaluating explicit ordered key lists deterministically. |
| Permissive ARM Cost Fallback | In `_arm_paper_candidate`, if `cost_ctx.get("available")` was False, the code fell back to calculation slippage and armed anyway. | Enforced strict fail-closed check: candidate rejected with `COST_CONTEXT_UNAVAILABLE`. |

---

### 3. Exact Changed & Created Files

**Production Code Modifications**:
- `backend/services/stop_placement_service.py`: Added `validate_episode_interval`, `resolve_verified_reference_node`, `resolve_verified_break_candle`; updated `resolve_episode_membership_from_structure`, `derive_protected_leg_episode`, `validate_protected_origin_binding`.
- `backend/services/plan_financial_context_service.py`: Added `build_verified_cost_context`, `assert_order_financial_parity`.
- `backend/services/scalp_watcher_orchestrator.py`: Added `get_system_config_cost_value`, `validate_arm_invariants`; hardened `evaluate_cycle` and `_arm_paper_candidate`.
- `frontend/tests/e2e/v17_5_6_chart_evidence.e2e.ts`: Added browser E2E test `P06` for persisted financial parity.

**New Dedicated Test Suites**:
- `backend/tests/test_v17_5_8_reference_provenance.py` (7 tests)
- `backend/tests/test_v17_5_8_break_fail_closed.py` (8 tests)
- `backend/tests/test_v17_5_8_episode_authority.py` (6 tests)
- `backend/tests/test_v17_5_8_cost_authority.py` (8 tests)
- `backend/tests/test_v17_5_8_full_cycle.py` (5 tests)

**Acceptance & Documentation**:
- `tools/run_v17_5_8_acceptance.py`
- `docs/V17_5_8_IMPLEMENTATION_MAP.md`
- `docs/V17_5_8_FINAL_ACCEPTANCE_REPORT.md`
- `artifacts/v17_5_8/acceptance.json`
- `artifacts/v17_5_8/manifest.json`

---

### 4. Acceptance Execution Results

Executed via `python3 tools/run_v17_5_8_acceptance.py`:

```
======================================================================
[*] AURUM V17.5.8 VERDICT: ACCEPTED
[*] Backend tests: 242/242 passed (0 failed)
[*] Frontend tests: 501 passed
[*] Playwright E2E: 6 passed
[*] Build: PASS
[*] Lint: PASS
[*] Acceptance JSON: artifacts/v17_5_8/acceptance.json
======================================================================
```

#### Gate-by-Gate Verification Breakdown

| Gate | Suite / Target | Tests Passed | Status | Duration |
|---|---|---|---|---|
| **Gate 0** | V17.5.8 Core Targeted Suites (Reference Provenance, Break Fail-Closed, Episode Authority, Cost Authority, Full Cycle) | **34 / 34** | **PASS** | 1.65s |
| **Gate 1** | V17.5.7 Baseline Suites (Episode Derivation & True Cycle Cost Parity) | **16 / 16** | **PASS** | 1.43s |
| **Gate 2** | V17.5.6 Targeted Suites (Cost Parity, Episode Origin, Break Time, Cycle Regression) | **37 / 37** | **PASS** | 1.32s |
| **Gate 3** | V17.5.5 Baseline Suites (Entry Gate, Cost Authority, Origin Bridge, Cycle Contract) | **23 / 23** | **PASS** | 1.32s |
| **Gate 4** | V17.5.4 Target Suites (Context, Cost, Anchor, Cycle) | **16 / 16** | **PASS** | 1.25s |
| **Gate 5** | V17.5.3 Boundary Probes & Cycle Review | **17 / 17** | **PASS** | 1.33s |
| **Gate 6** | V17.5.2 Baseline Suites (Boundary Probes, Detector to Exit) | **7 / 7** | **PASS** | 1.40s |
| **Gate 7** | V17.5.1 Feature Suites & Probes (9 suites) | **26 / 26** | **PASS** | 1.81s |
| **Gate 8** | Existing V17.5 Regression Suites (8 suites) | **31 / 31** | **PASS** | 1.25s |
| **Gate 9** | Prior Regression Suites (CG01, sessions, authority) | **35 / 35** | **PASS** | 3.21s |
| **Gate 10** | Frontend Vitest Unit Tests (48 files) | **501 / 501** | **PASS** | 3.50s |
| **Gate 11** | Frontend Build (`npm run build`) | **0 errors** | **PASS** | 5.89s |
| **Gate 12** | Frontend Lint (`npm run lint`) | **0 errors** | **PASS** | 0.33s |
| **Gate 13** | Playwright Browser Verification (`v17_5_6_chart_evidence.e2e.ts`) | **6 / 6** | **PASS** | 12.57s |

---

### 5. Invariant & Boundary Assurance

1. **Safety Invariants Maintained**:
   - Paper execution only; instrument strictly restricted to XAUUSDT.
   - Max 3 new trades per trading day and max 1 simultaneous open position enforced atomically.
   - Risk budget preservation: stop-distance position sizing strictly enforced.
   - No real exchange orders placed; zero Telegram messages dispatched during testing.
2. **Deterministic & Isolated Testing**:
   - All tests use in-memory SQLite and fake/replay clocks.
   - Real fresh session readbacks ensure that persisted database state matches in-memory calculations.
3. **Empirical Performance Boundary**:
   - Technical test passage proves structural provenance, geometric consistency, and execution safety under paper trading assumptions.
   - Empirical market profitability remains **UNPROVEN**.

---

### 6. Reproduction Instructions

From repository root:

```bash
cd aurum-desk

# Run all 14 acceptance gates
python3 tools/run_v17_5_8_acceptance.py

# Run targeted V17.5.8 backend tests directly
cd backend
venv/bin/python -m pytest tests/test_v17_5_8_reference_provenance.py tests/test_v17_5_8_break_fail_closed.py tests/test_v17_5_8_episode_authority.py tests/test_v17_5_8_cost_authority.py tests/test_v17_5_8_full_cycle.py -v

# Run frontend unit & browser E2E tests
cd ../frontend
npm test -- --run
npm run build
npm run lint
npm run test:e2e -- tests/e2e/v17_5_6_chart_evidence.e2e.ts
```
