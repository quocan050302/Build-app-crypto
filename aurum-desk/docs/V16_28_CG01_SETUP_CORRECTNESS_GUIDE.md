# V16.28 CG-01 Setup Correctness Operational Guide

## Overview
V16.28 resolves three fundamental correctness defects in the CG-01 setup evaluation engine:
1. **Dynamic Causal M1 ATR Displacement Verification**: Replaces fixed 1.0 default ATR with exact True Range Wilder ATR.
2. **FVG Lifecycle & Invalidation Assessment**: Tracks formation known boundary (close of 3rd candle), penetration depth, CE touch, and post-retest structural SL breaches.
3. **Exhaustive Multi-POI Candidate Evaluation**: Scans all eligible POIs without premature early exits on waiting states.

## Key Behavioral Policies

### 1. M1 ATR Displacement Thresholds
- **Warmup Requirement**: Requires at least `displacement_atr_period + 1` closed M1 candles. If missing, candidate evaluation returns `OBSERVING` with reason `ATR_WARMUP`.
- **Pre-Break Baseline**: True range is measured from candles preceding the break candle to prevent the displacement candle from expanding its own threshold.
- **Dynamic Ratio**: `body_atr_ratio = abs(close - open) / atr_1m`. Must satisfy `body_atr_ratio >= displacement_body_atr_min` (default 1.0).

### 2. FVG Freshness & Entry Modes
- **LIMIT_AT_CE_AFTER_CONFIRMATION**:
  - If price touches or trades through CE before the order is armed, the zone is classified as `FVG_USED_BEFORE_ARM` and rejected.
  - If penetration depth exceeds `max_fvg_mitigation_ratio` (default 0.50), it is rejected as `FVG_OVER_MITIGATED`.
- **CONFIRMED_RETEST_MARKET**:
  - Touching CE does not reject the FVG; it activates the retest sequence (`ACTIVE_FOR_RETEST`).
  - Retest must close back in alignment with direction.
  - Any subsequent candle up to `as_of_ms` breaching structural SL invalidates the retest (`INVALIDATED_AFTER_RETEST`).

### 3. Multi-POI Evaluation & Precedence
- The engine collects all decisions across eligible POIs and directions:
  - `executable_decisions`: Fully eligible candidates meeting all rules.
  - `waiting_decisions`: Setups awaiting retest or confirmation.
  - `rejection_diagnostics`: Setups failing specific geometric or risk criteria.
- **Priority**:
  1. If any executable candidate exists, it is selected and armed immediately (highest priority).
  2. If none are executable but waiting candidates exist, the first waiting candidate is returned with its respective wait stage.
  3. If all candidates are rejected, the most representative rejection diagnostic is returned.
