# V16.28 CG-01 Setup Correctness Acceptance Report

- **Timestamp**: `2026-10-18T15:26:09.729430+00:00`
- **Commit SHA**: `9a3c47c4b66a825522a25bcda4ed5156271f3fe3`
- **Diff Hash**: `2b28904079edb44bd5188d712ffde53c39a393473e5ba4a2bc325f33e71168c7`
- **Overall Status**: **PASS**
- **Forward Status**: `NOT_OBSERVED` (Truthful runtime observation; runner is standalone verification suite)
- **Trading Performance**: `NOT_EVALUATED` (Correctness verification passing; zero forward winrate/P&L claims)
- **Unique Tests Verified**: `532` distinct tests (strictly deduplicated across gates)
- **Full V16 Test Files**: `68` test files in regression scope

## Summary of Mandatory Technical Gates

| Gate | Description | Status | Tests Passed | Elapsed |
|---|---|---|---|---|
| Gate 1: V16.28 Setup Correctness (ATR, FVG Lifecycle, Multi-POI) | `gate_1_setup_correctness` | **PASS** | 25 | 1.7s |
| Gate 2: CG-01 Core Strategy Pipeline & Configuration | `gate_2_core_strategy_pipeline` | **PASS** | 61 | 3.76s |
| Gate 3: Execution Lifecycle & Production Flows | `gate_3_execution_lifecycle_and_production` | **PASS** | 33 | 21.29s |
| Gate 4: Research Baseline Tools & Reliability | `gate_4_research_baseline_tools` | **PASS** | 77 | 28.28s |
| Gate 5: Full V16 Regression Suite | `gate_5_full_v16_regression` | **PASS** | 501 | 71.6s |
| Gate 6: Frontend Vitest Integration | `gate_6_frontend_vitest` | **PASS** | 232 | 4.0s |

## Root Causes & Corrective Implementations

### 1. Dynamic & Causal M1 ATR Displacement Verification (Arithmetic Rolling Mean SMA)
- **Defect Fixed**: `evaluate_cg01_candidate()` previously called `resolve_cg01_mss_and_displacement()` without passing dynamic M1 ATR, causing the production path to fall back to a fixed constant scalar of `1.0`. Displacement thresholds were thus fixed price distance thresholds rather than volatility-scaled thresholds.
- **Formula & Arithmetic**: `build_cg01_displacement_atr_series(closed_m1, period, as_of_ms)` calculates the True Range using prior close: `TR = max(H - L, |H - C_prev|, |L - C_prev|)` and computes an arithmetic rolling mean (SMA) over the completed `period` bars immediately preceding the break candle (pre-break baseline). This is strictly an arithmetic mean, NOT recursive Wilder RMA smoothing.
- **Causality & Non-Self-Referentiality**: The break candle's own high, low, or body is excluded from its baseline ATR so the candidate break cannot self-inflate its threshold.
- **Warmup & Units**: ATR is denominated in instrument price units (e.g., USD/oz for XAUUSDT). Requires at least `period + 1` closed M1 candles for warmup; if insufficient, candidate evaluation returns `OBSERVING` with reason `ATR_WARMUP`.
- **Volatility Floor**: `resolve_cg01_mss_and_displacement()` enforces `effective_atr = max(effective_atr, 0.20)`, which is a price-volatility floor (not a tick size) protecting against division by zero in flat markets.
- **Displacement Ratio**: `body_atr_ratio = abs(close - open) / effective_atr`. Must satisfy `body_atr_ratio >= displacement_body_atr_min` (default 1.0).

### 2. Meaningful FVG Lifecycle, Invalidation, & Diagnostic Reasons
- **Defect Fixed**: `select_cg01_leg_fvg()` had an empty `pass` in its mitigation check, allowing over-mitigated or used FVGs to be selected without lifecycle validation. When candidates were rejected, the caller received `None` without the underlying cause, frequently causing the engine to fall back to `WAIT_SWEEP` even though liquidity had already swept.
- **Implementation**: Added `assess_cg01_fvg_lifecycle(fvg, closed_m1, direction, as_of_ms, config, entry_mode, structural_sl)`: tracks formation known time (close of 3rd candle), penetration depth, CE touch, and boundary violation. Distinguishes `LIMIT_AT_CE_AFTER_CONFIRMATION` (cannot arm if CE was already touched before arming -> `FVG_USED_BEFORE_ARM`) from `CONFIRMED_RETEST_MARKET` (touching CE enables retest -> `ACTIVE_FOR_RETEST`). Updated `resolve_cg01_retest()` to track subsequent candles up to `as_of_ms` for structural SL breaches, returning `INVALIDATED_AFTER_RETEST` if breached.
- **Diagnostic Accuracy & Progress Hierarchy**: Introduced `CG01CandidateDiagnostic` and `select_cg01_leg_fvg_with_diagnostics()`. When a POI sweeps liquidity but lacks MSS/displacement, the engine accurately reports `WAIT_MSS` (`WAITING_MSS_CONFIRMATION`) rather than reverting to `WAIT_SWEEP`. When MSS is confirmed but FVG is missing or rejected, it reports `WAIT_FVG` with specific lifecycle reasons (`FVG_USED_BEFORE_ARM`, `FVG_OVER_MITIGATED`).

### 3. Exhaustive Multi-POI Candidate Evaluation & Milestone Selection
- **Defect Fixed**: In confirmed-retest mode, `evaluate_cg01_candidate()` executed an early return on the first POI awaiting retest confirmation (`return CG01Decision(stage=WAIT_ENTRY, ...)`), masking subsequent eligible POIs that were already confirmed and executable.
- **Implementation**: Refactored evaluation loop to exhaustively scan all eligible POIs across all allowed directions. Collects `executable_decisions`, `waiting_decisions`, `rejection_diagnostics`, and `progress_diagnostics`.
- **Selection Hierarchy**: Prioritizes `executable_decisions[0]` with stable deterministic ranking. Verified via symmetric LONG and SHORT multi-POI tests (`POI_A` waiting, `POI_B` executable in the same cycle) that `POI_B` is cleanly selected with 100% unmixed evidence nodes. If no executable candidate exists, returns waiting candidate or selects the furthest milestone progress diagnostic via `select_cg01_progress_diagnostic()` (`WAIT_ENTRY` > `WAIT_FVG` > `WAIT_MSS` > `WAIT_SWEEP`).

## Acceptance Invariants Preserved
- **Zero Speculative Alterations**: No Unicorn, no BPR, no expanded trading sessions, no forced quota fills.
- **Preserved Guardrails**: Risk calculator, Quote authority, News guards, Daily cap, Account guards, and Order lifecycle remain 100% intact.
- **Clean Data Boundary**: No live exchange connections, no live DB modifications, no Telegram dispatches.
- **Research Compatibility**: Downloader pagination, trade export schemas, and EventBus lifecycle from prior patches remain completely functional.
