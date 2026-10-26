# V16.28 CG-01 Setup Correctness Operational Guide

## Overview
V16.28 resolves fundamental correctness defects in the CG-01 setup evaluation engine:
1. **Dynamic Causal M1 ATR Displacement Verification**: Replaces fixed 1.0 default ATR with exact arithmetic rolling mean (SMA) of True Range over completed pre-break bars.
2. **FVG Lifecycle, Invalidation, & Diagnostic Reasons**: Tracks formation known boundary (close of 3rd candle), penetration depth, CE touch, post-retest structural SL breaches, and distinguishes `WAIT_MSS` / `WAIT_FVG` progress states.
3. **Exhaustive Multi-POI Candidate Evaluation**: Scans all eligible POIs without premature early exits on waiting states; guarantees executable candidate priority and furthest progress milestone selection.

## Key Behavioral Policies

### 1. M1 ATR Displacement Thresholds (Arithmetic Rolling Mean SMA)
- **Calculation Formula**: `TR = max(high - low, abs(high - prev_close), abs(low - prev_close))`. ATR is the simple arithmetic mean of TR over the specified `period` of completed bars.
- **Pre-Break Baseline**: True range is measured strictly from candles preceding the break candle to prevent the displacement candle from expanding its own threshold.
- **Warmup Requirement**: Requires at least `displacement_atr_period + 1` closed M1 candles. If missing, candidate evaluation returns `OBSERVING` with reason `ATR_WARMUP`.
- **Volatility Floor**: Effective ATR is `max(atr_series, 0.20)`, where 0.20 is a price-volatility floor in instrument price units.
- **Dynamic Ratio**: `body_atr_ratio = abs(close - open) / effective_atr`. Must satisfy `body_atr_ratio >= displacement_body_atr_min` (default 0.80).

### 2. FVG Freshness & Entry Modes
- **LIMIT_AT_CE_AFTER_CONFIRMATION**:
  - If price touches or trades through CE before the order is armed, the zone is classified as `FVG_USED_BEFORE_ARM` and rejected.
  - If penetration depth exceeds `max_fvg_mitigation_ratio` (default 0.50), it is rejected as `FVG_OVER_MITIGATED`.
- **CONFIRMED_RETEST_MARKET**:
  - Touching CE does not reject the FVG; it activates the retest sequence (`ACTIVE_FOR_RETEST`).
  - Retest must close back in alignment with direction.
  - Any subsequent candle up to `as_of_ms` breaching structural SL invalidates the retest (`INVALIDATED_AFTER_RETEST`).

### 3. Diagnostic Accuracy & Progress Milestones
- **Progress Hierarchy**:
  - `WAIT_SWEEP` (Rank 2): Actively watching HTF POI; waiting for liquidity sweep.
  - `WAIT_MSS` (Rank 3): Liquidity swept; waiting for closed candle confirming market structure shift and displacement.
  - `WAIT_FVG` (Rank 4): MSS and displacement confirmed; waiting for fresh, valid FVG on leg (records lifecycle rejections like `FVG_USED_BEFORE_ARM` or `FVG_OVER_MITIGATED`).
  - `WAIT_ENTRY` (Rank 5): Valid FVG identified; waiting for retest touch/confirmation or limit order fill.
- **Milestone Selection**: When no candidate is executable, `select_cg01_progress_diagnostic()` returns the diagnostic with the furthest progress milestone.

### 4. Multi-POI Evaluation & Precedence
- The engine collects all decisions across eligible POIs and directions:
  - `executable_decisions`: Fully eligible candidates meeting all rules.
  - `waiting_decisions`: Setups awaiting retest or confirmation.
  - `rejection_diagnostics`: Setups failing specific geometric or risk criteria.
  - `progress_diagnostics`: Milestone progress facts for each evaluated POI.
- **Priority**:
  1. If any executable candidate exists, the first executable is selected and armed immediately (highest priority).
  2. If none are executable but waiting candidates exist, the first waiting candidate is returned.
  3. If all candidates are rejected, the most specific rejection diagnostic is returned.
  4. If setups are in progress, the furthest milestone progress diagnostic is returned.
