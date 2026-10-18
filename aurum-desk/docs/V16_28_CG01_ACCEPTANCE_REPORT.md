# V16.28 CG-01 Setup Correctness Acceptance Report

- **Timestamp**: `2026-10-18T13:30:13.926871+00:00`
- **Commit SHA**: `56850aab92161ff4aeead3e7f05b55f609794d8c`
- **Diff Hash**: `042f3b7543ea4f2007c5759bda7c2df8fa215c24a0214c3ae52a3dbd99d6956c`
- **Overall Status**: **PASS**
- **Forward Status**: `NOT_OBSERVED` (Truthful runtime observation; runner is standalone verification suite)
- **Trading Performance**: `NOT_EVALUATED` (Correctness verification passing; zero forward winrate/P&L claims)
- **Unique Tests Verified**: `526` distinct tests (strictly deduplicated across gates)
- **Full V16 Test Files**: `68` test files in regression scope

## Summary of Mandatory Technical Gates

| Gate | Description | Status | Tests Passed | Elapsed |
|---|---|---|---|---|
| Gate 1: V16.28 Setup Correctness (ATR, FVG Lifecycle, Multi-POI) | `gate_1_setup_correctness` | **PASS** | 19 | 1.14s |
| Gate 2: CG-01 Core Strategy Pipeline & Configuration | `gate_2_core_strategy_pipeline` | **PASS** | 61 | 3.11s |
| Gate 3: Execution Lifecycle & Production Flows | `gate_3_execution_lifecycle_and_production` | **PASS** | 33 | 19.36s |
| Gate 4: Research Baseline Tools & Reliability | `gate_4_research_baseline_tools` | **PASS** | 77 | 26.33s |
| Gate 5: Full V16 Regression Suite | `gate_5_full_v16_regression` | **PASS** | 495 | 65.67s |
| Gate 6: Frontend Vitest Integration | `gate_6_frontend_vitest` | **PASS** | 232 | 1.95s |

## Root Causes & Corrective Implementations

### 1. Dynamic & Causal M1 ATR Displacement Verification
- **Defect Fixed**: `evaluate_cg01_candidate()` previously called `resolve_cg01_mss_and_displacement()` without passing dynamic M1 ATR, causing the production path to fall back to a fixed constant scalar of `1.0`. Displacement thresholds were thus fixed price distance thresholds rather than volatility-scaled thresholds.
- **Implementation**: Added `build_cg01_displacement_atr_series(closed_m1, period, as_of_ms)` utilizing true range with previous candle close: `TR = max(H - L, |H - C_prev|, |L - C_prev|)` and Wilder RMA smoothing. Evaluates causal pre-break baseline so displacement candle does not distort its own threshold. Enforces M1 ATR warmup (`len(c_1m) >= period + 1`); returns `OBSERVING` with `ATR_WARMUP` if insufficient. Safe floor of `0.20` prevents division by zero in flat markets while non-finite inputs fail safely.

### 2. Meaningful FVG Lifecycle & Invalidation Assessment
- **Defect Fixed**: `select_cg01_leg_fvg()` had an empty `pass` in its mitigation check, allowing over-mitigated or used FVGs to be selected without lifecycle validation. Furthermore, `resolve_cg01_retest()` broke early on first retest touch, ignoring structural invalidation that occurred subsequently.
- **Implementation**: Implemented `assess_cg01_fvg_lifecycle(fvg, closed_m1, direction, as_of_ms, config, entry_mode, structural_sl)`: tracks formation known time (close of 3rd candle), penetration depth, CE touch, and boundary violation. Distinguishes `LIMIT_AT_CE_AFTER_CONFIRMATION` (cannot arm if CE was already touched before arming -> `FVG_USED_BEFORE_ARM`) from `CONFIRMED_RETEST_MARKET` (touching CE enables retest -> `ACTIVE_FOR_RETEST`). Updated `resolve_cg01_retest()` to track subsequent candles up to `as_of_ms` for structural SL breaches, returning `INVALIDATED_AFTER_RETEST` if breached.

### 3. Exhaustive Multi-POI Candidate Evaluation
- **Defect Fixed**: In confirmed-retest mode, `evaluate_cg01_candidate()` executed an early return on the first POI awaiting retest confirmation (`return CG01Decision(stage=WAIT_ENTRY, ...)`), masking subsequent eligible POIs that were already confirmed and executable.
- **Implementation**: Refactored evaluation loop to exhaustively scan all eligible POIs across all allowed directions. Collects `executable_decisions`, `waiting_decisions`, and `rejection_diagnostics`. Prioritizes executable candidates with stable deterministic ranking. If no executable candidate exists, returns deterministic waiting state without masking valid opportunities.

## Acceptance Invariants Preserved
- **Zero Speculative Alterations**: No Unicorn, no BPR, no expanded trading sessions, no forced quota fills.
- **Preserved Guardrails**: Risk calculator, Quote authority, News guards, Daily cap, Account guards, and Order lifecycle remain 100% intact.
- **Clean Data Boundary**: No live exchange connections, no live DB modifications, no Telegram dispatches.
- **Research Compatibility**: Downloader pagination, trade export schemas, and EventBus lifecycle from prior patches remain completely functional.
