# V15.4 — FINAL CAUSAL ENTRY CORRECTNESS CLOSURE REPORT
**Repository:** quocan050302/Build-app-crypto  
**Branch:** `fix/v14-1-rr-flow`  
**Baseline HEAD:** `f006e574ea8fa4e6c794e099de08659d3d8aea1e` (`feat(v15.3): repair causal data contracts and connect real zone assessment`)  
**Scope:** Real FVG Intersection + Verified Liquidity Sweep + Prefix ATR + Consistent Target Authority + Non-Refreshing Hypothesis Expiry.

---

## 1. Executive Summary & Root Cause Analysis

V15.4 permanently eliminates five core causal vulnerabilities identified by the reviewer in the scalp M1 execution engine, bringing the pipeline into full causal rigor:

| Defect Area | Baseline Failure Mode (Root Cause) | Resolution Architecture |
| :--- | :--- | :--- |
| **1. FVG Retest Geometry** | Baseline LONG check used `low <= fvg_top and low >= sweep_price`. A candle completely below the FVG (e.g. `[92, 95]` against FVG `[100, 110]`) passed. Symmetric defect in SHORT. | Implemented `candle_intersects_price_zone(candle, bottom, top)` enforcing pure closed interval intersection `low <= top and high >= bottom` with strict finite/order validation and rejection of deep structural invalidation breaches. |
| **2. Liquidity Sweep Verification** | Baseline reversal accepted any local swing near a zone without establishing a verified liquidity sweep with prior reference, breach, and reclaim. | Added `select_sweep_reference()` and wired `confirm_liquidity_sweep()` into `evaluate_m1_reversal_candidate()`. Requires a confirmed prior liquidity level, physical breach beyond the level, and candle close reclaim. Sets structural SL at the actual swept extreme. |
| **3. Causal Prefix ATR** | `ZoneWatchlistService.extract_zones_from_candles()` passed `atr=calc_atr` (current window volatility) into `detect_order_blocks()`, causing historical breaks to use future window volatility. M1 displacement ATR also used full window. | Removed current ATR override from extractor. Refactored `detect_order_blocks()` and `evaluate_m1_reversal_candidate()` to compute prefix ATR causal to the exact historical break bar index `i`: `compute_atr(candles[:i + 1], period=14)`. Appending future volatility bars leaves historical OBs and displacement qualification at decision $T$ immutable. |
| **4. Authoritative Target Consistency** | When hypothesis target was unavailable or unconsumed levels did not exist, candidate fell back to synthetic risk-multiplier / ATR targets, breaking target authority between hypothesis, candidate, snapshot, and order. | Implemented canonical `TargetDecision` contract and `resolve_executable_target()` requiring unconsumed liquidity levels on the correct side with minimum ATR distance. Prohibits synthetic risk fallbacks for SMC models: if target is unavailable, candidate returns `WAITING_LIQUIDITY_TARGET` and remains non-eligible. Order, snapshot, and candidate freeze identical target price and source level ID. |
| **5. Non-Refreshing Hypothesis Expiry** | `build_liquidity_hypotheses()` previously computed `expiry_ms = now_ms + ttl_ms` on every cycle, causing hypothesis expiry to drift forward indefinitely across repeated evaluations. | Implemented `reconcile_hypothesis_lifecycle()` and wired `existing_hypotheses` into `build_liquidity_hypotheses()` and `ScalpWatcherOrchestrator`. Preserves immutable `created_at_ms` and `expiry_ms` for active hypotheses. Marks status as `EXPIRED` once `now_ms >= expiry_ms` and prevents execution of expired hypotheses. |

---

## 2. Modified Functions & Call Chains

### A. FVG Retest Intersection
- **Helper:** `candle_intersects_price_zone(candle: Any, bottom: float, top: float) -> bool`
  - Validates `math.isfinite(low, high, bottom, top)`, `low <= high`, `bottom < top`.
  - Condition: `low <= top and high >= bottom`.
- **Caller:** `ScalpM1ModelService.evaluate_m1_reversal_candidate()`
  - Retest bar selection: `[b for b in post_fvg_bars if candle_intersects_price_zone(b, fvg_bot, fvg_top) and float(b.low) >= sweep_price]`.
  - Ensures first qualifying retest bar establishes immutable `trigger_known_at` (no TTL drift from subsequent touches).

### B. Verified Liquidity Sweep
- **Helper:** `select_sweep_reference(levels, zone, direction, as_of_ms, candles=None, atr=1.0)`
  - Selects unconsumed sell-side (for LONG) or buy-side (for SHORT) level confirmed before `as_of_ms`.
- **Validation:** Calls `confirm_liquidity_sweep(sweep_ref, closed_1m, now_ms=now_ms, candle_duration_ms=60_000)`
  - Validates breach price, reclaim price, rejection ratio, and returns `WAITING_LIQUIDITY_REFERENCE` or `WAITING_M1_STRUCTURE` if sweep is unconfirmed.
  - Frozen structural SL: `sweep_conf.breach_price - sl_buffer_ticks * tick_size` (for LONG).

### C. Prefix ATR Causality
- **Caller:** `ZoneWatchlistService.extract_zones_from_candles()`
  - Removed `atr=calc_atr` argument from `detect_order_blocks()`.
- **Detector:** `detect_order_blocks(candles, swing_pivots, timeframe, ...)`
  - Per-break prefix ATR: `bar_atr = smc_engine.compute_atr(candles[:i + 1], period=14)`.
- **Model:** `ScalpM1ModelService.evaluate_m1_reversal_candidate()`
  - Prefix displacement ATR: `break_prefix_atr = smc_engine.compute_atr(closed_1m[:first_break_idx + 1], period=cfg.displacement_atr_period)`.

### D. Target Authority
- **Contract:** `TargetDecision(status, target_type, price, source_level_id, known_at_ms, reason_codes, label_vi)`
- **Resolver:** `resolve_executable_target(levels, direction, entry_price, as_of_ms, atr, model_id)`
  - Filters by minimum distance `max(1.0 * atr, 0.50)` and calls `select_unconsumed_target()`.
  - Prohibits synthetic multiplier: returns `WAITING_LIQUIDITY_TARGET` if no valid target exists.
  - Snapshot & order store `target_price`, `source_level_id`, and `reason_codes`.

### E. Non-Refreshing Hypothesis Expiry
- **Helper:** `reconcile_hypothesis_lifecycle(existing_hypotheses, new_hypotheses, now_ms)`
  - Matches existing hypotheses by `hypothesis_id`.
  - Preserves immutable `created_at_ms` and `expiry_ms`.
  - Transitions to `status="EXPIRED"` when `now_ms >= expiry_ms`.
- **Orchestrator:** `ScalpWatcherOrchestrator.evaluate_cycle()`
  - Passes `self._active_hypotheses.get(symbol, [])` to `build_liquidity_hypotheses()`.
  - Guards against expired hypotheses: records `OpportunityReasonCode.HYPOTHESIS_EXPIRED` and aborts arming.

---

## 3. Test Evidence & Verification Commands

### A. Test Execution & Counts
| Test Suite | Commands Executed | Result & Counts |
| :--- | :--- | :--- |
| **V15.4 Dedicated Suite** | `pytest tests/test_v15_4_causal_entry.py` | **26 passed** in 0.35s (100%) |
| **All V15 Test Suites** | `pytest tests/ -k "v15"` | **94 passed** in 2.58s (100%) |
| **Baseline Core SMC & Guards** | `pytest tests/test_smc_engine.py tests/test_v14_2_entry_guard.py tests/test_v14_2_news_blackout.py tests/test_v14_3_ny_quota.py` | **244 passed** in 18.47s (100%) |
| **Frontend Production Build** | `npm run build` (in `frontend/`) | **Exit code 0** (tsc -b & vite build passed) |

### B. Defect Resolution Matrix
- **Defect 1 (FVG Repros):** Verified with `test_exact_long_repro_candle_below_fvg` (FVG `[100, 110]`, candle `[92, 95]`) and `test_exact_short_repro_candle_above_fvg` (FVG `[100, 110]`, candle `[115, 118]`) -> Both fail retest verification and return `WAITING_M1_RETEST`.
- **Defect 2 (Sweep Verification):** Verified with `test_sweep_helper_positive_and_negative_controls` (touch-only and breach-without-reclaim fail) and `test_reversal_candidate_rejects_pivot_without_liquidity_sweep`.
- **Defect 3 (Prefix ATR Invariance):** Verified with `test_prefix_atr_caller_future_invariance` (appending future volatility bars leaves historical OBs and invalidation at $T$ identical) and `test_detect_order_blocks_ignores_current_window_atr_leakage`.
- **Defect 4 (Target Authority):** Verified with `test_target_authority_no_synthetic_fallback_when_unavailable` (consumed/wrong-side target results in `WAITING_LIQUIDITY_TARGET`, non-eligible) and `test_target_authority_consistency_when_present` (identical target price and source level across hypothesis, candidate, snapshot, order).
- **Defect 5 (Hypothesis Expiry):** Verified with `test_hypothesis_expiry_preserves_anchor_across_cycles` (repeated evaluations preserve `created_at_ms` and `expiry_ms`) and `test_hypothesis_marks_expired_past_deadline` (past deadline marked `EXPIRED`).

---

## 4. Invariants & Scope Confirmation
1. **M5 STANDARD_SMC Intact:** No alterations made to M5 detector, setup timeframes, or entry policies.
2. **Guards Uncompromised:** News blackout, risk rules, lessons, daily quota (3/3), spread/quote validation all remain strictly enforced.
3. **Paper Only Execution:** Zero real exchange order API routing.
4. **No Profitability Claims:** Correctness of causal geometry and state transitions does not imply or guarantee future trading performance.
5. **No Synthetic Fill Creation:** Daily targets remain strictly observational research goals.
